"""The Tiny Jukebox service: drivers, state machines, player, volume, command line and rest api in one process."""

import argparse
import functools
import logging
import os
import signal
from collections.abc import Mapping, Sequence
from typing import Any

from tinyjukebox import __version__
from tinyjukebox.albumplayer.radioplayer import RadioPlayer
from tinyjukebox.albumplayer.simpleplayer import AudioOutput, SimplePlayer
from tinyjukebox.cli import CLI
from tinyjukebox.control import BoxControl
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.microservice import AbstractMicroservice
from tinyjukebox.core.pubsub import LocalQueue, PubSubClient
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.target import TargetAlbum, TargetRadio
from tinyjukebox.data.targets import LAST_ID_SUFFIX, Targets
from tinyjukebox.data.volumelimits import VolumeLimits
from tinyjukebox.devices.bluetooth.virtual import VirtualHeadset
from tinyjukebox.devices.drivermanager.drivermanager import DriverManager
from tinyjukebox.devices.schema.drivermanager import get_schema as get_drivers_schema
from tinyjukebox.headset import BluetoothVolumeControl, Headset
from tinyjukebox.images import Images, take_over
from tinyjukebox.latency import ReactionTimer
from tinyjukebox.messages import JOURNAL, PLAYBACK, JournalEvent, PlaybackStatus
from tinyjukebox.rgbled import ALARM, Color, RGBLed
from tinyjukebox.schema.tinyjukebox import get_schema
from tinyjukebox.slots import LOOK, Health, Slots
from tinyjukebox.stm.playradio import PlayRadio, RadioPlayerFactory
from tinyjukebox.stm.stmoperation import FORCED_POLLING_INTERVAL, STMOperation
from tinyjukebox.stm.stmplayalbum import PlayerFactory, STMPlayAlbum
from tinyjukebox.stm.stmvolume import STMVolume
from tinyjukebox.sync.box import BoxSync
from tinyjukebox.volumemanager import (
    AlsaVolumeControl,
    VirtualVolumeControl,
    VolumeControl,
    VolumeManager,
    VolumeProfile,
    Volumes,
)
from tinyjukebox.web.logbuffer import LogBuffer
from tinyjukebox.web.server import RestApi

__all__ = ["HEADPHONES_DEFAULTS", "TinyJukebox", "standalone"]

HEADPHONES_DEFAULTS = {"default-volume": 40, "max-volume": 75}
"""Volume of the headset (hearing protection) unless the config (``bluetooth``) or the volume json file says
otherwise."""


def _stop_on_signal(signum: int, frame: object) -> None:
    """SIGTERM (systemd) and SIGHUP (screen -X quit, closed terminal) stop the service like Ctrl-C.

    Further signals are ignored from then on: on a box with slots systemd and the start script may both send one,
    and a stop that is interrupted leaves threads behind that keep the process alive until systemd kills it."""
    for further in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(further, signal.SIG_IGN)
    raise KeyboardInterrupt(signal.Signals(signum).name)


class TinyJukebox(AbstractMicroservice):
    """Tiny Jukebox box - see docs/developer/configuration.md for the configuration (section ``tinyjukebox``)."""

    _version = __version__

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient | None = None,
        logger: logging.Logger | None = None,
        stdout_log_level: str | None = None,
        no_gui: bool | None = None,
        hardware: bool = True,
        webserver: bool | None = None,
        audio: bool = True,
        player_factory: PlayerFactory | None = None,
        poll_interval: float = FORCED_POLLING_INTERVAL,
        radio_player_factory: RadioPlayerFactory | None = None,
    ) -> None:
        """
        :param hardware: if False, run without device drivers (section ``drivers``) and without alsa mixer - with a
            virtual bluetooth headset.
        :param webserver: True/False - enable/disable rest api and web page; None - as configured in ``rest-api``.
        :param audio: if False, tracks are decoded in real time but not played (no sound card needed).
        :param player_factory: album player (default: :class:`SimplePlayer`) - for tests.
        :param radio_player_factory: radio player (default: :class:`RadioPlayer`) - for tests.
        :param poll_interval: seconds between the requests for the current rfid state after the start.
        """
        super().__init__(
            config,
            "tinyjukebox",
            pubsub_client=pubsub_client,
            logger=logger,
            logger_name=type(self).__name__,
            stdout_log_level=stdout_log_level,
            no_gui=no_gui,
        )
        self._logger.info("initializing (hardware: %s, webserver: %s, audio: %s)", hardware, webserver, audio)
        self._expand_folders()
        rest_api_config: dict[str, Any] | None = self._config.get("rest-api")
        if webserver is None:
            webserver = rest_api_config is not None and rest_api_config.get("enabled", True)
        self._log_buffer: LogBuffer | None = None
        if webserver:
            # attached right away - the web page shows the log of the start as well
            self._log_buffer = LogBuffer()
            self._logger.addHandler(self._log_buffer)

        self._tag_latency = ReactionTimer("tag", self._logger)
        # speaker or headset - the players look it up whenever a playback starts
        self._output = AudioOutput(null_output=not audio, buffer_time=self._config["albumplayer"].get("buffer-time"))
        self._player_factory: PlayerFactory = player_factory or functools.partial(
            SimplePlayer,
            on_play=functools.partial(self._tag_latency.end, "ffmpeg started"),
            output=self._output,
        )
        self._radio_player_factory: RadioPlayerFactory = radio_player_factory or functools.partial(
            RadioPlayer,
            on_play=functools.partial(self._tag_latency.end, "ffmpeg started"),
            output=self._output,
        )

        # the drivers use the pubsub client of the box; it is connected and disconnected by TinyJukebox
        self._driver_manager: DriverManager | None = None
        if hardware and "drivers" in config:
            self._driver_manager = DriverManager(config, self._pubsub_client, self._logger)
            for name, command in self._driver_manager.ui_commands.items():
                self._add_ui_command(name, command)
        elif hardware:
            self._logger.warning("no config section 'drivers' - running without device drivers")
        self._virtual_headset: VirtualHeadset | None = None
        if not hardware:
            self._virtual_headset = VirtualHeadset(self._pubsub_client, self._logger)
            for name, command in self._virtual_headset.ui_commands.items():
                self._add_ui_command(name, command)

        json_files = self._config["json"]
        images_folder = self._images_folder(rest_api_config or {})
        # with a library the json files are generated from what it delivered and the changes of this box
        self._sync = self._create_sync(images_folder)
        self._tags = Tags(json_files["tags"], self._logger)
        self._targets = Targets(json_files["targets"], self._config["albums-folder"], self._logger)
        self._activities = Activities(json_files["activities"], self._tags, self._logger)
        self._rgb_led = RGBLed(self._logger, self._pubsub_client)

        self._mixer_watch: AlsaVolumeControl | None = None  # follows volume changes made outside tinyjukebox
        headphones_control = BluetoothVolumeControl(self._pubsub_client, self._logger)
        self._limits = VolumeLimits(self._volume_file(), self._logger)
        self._volumes = self._create_volumes(hardware, headphones_control)
        self._stmvolume = STMVolume(
            self._pubsub_client,
            self._volumes,
            self._logger,
            ReactionTimer("volume button", self._logger),
        )
        self._stmoperation = STMOperation(
            self._pubsub_client,
            self._logger,
            self._rgb_led,
            self._tags,
            self._targets,
            self._activities,
            self._create_stmplayalbum,
            poll_interval=poll_interval,
            latency=self._tag_latency,
            radio_factory=self._create_playradio,
            sync=self._sync,
        )
        self._headset = Headset(
            self._pubsub_client,
            self._logger,
            self._rgb_led,
            self._output,
            self._volumes,
            headphones_control,
            self._stmoperation,
        )

        images = Images(images_folder)
        self._sync.bind(self._tags, self._targets, self._activities, images, self._limits, self._stmoperation)
        self._sync.on_fit = self._fit_changed
        self._slots = Slots(self._logger)
        self._health = self._create_health()
        self.control = BoxControl(
            self._config,
            self._pubsub_client,
            self._volumes,
            self._stmoperation,
            self._tags,
            self._targets,
            self._activities,
            self._logger,
            self._rgb_led,
            images,
            self._headset,
            self._virtual_headset,
            self._sync,
            self._slots,
            data_files=[
                json_files["tags"],
                json_files["targets"],
                os.path.splitext(json_files["targets"])[0] + LAST_ID_SUFFIX,
                json_files["activities"],
                self._volume_file(),
            ],
        )
        self._cli = CLI(self._add_ui_command, self.control)
        self._rest_api: RestApi | None = None
        if webserver:
            self._rest_api = RestApi(rest_api_config or {}, self.control, self._targets, self._logger, self._log_buffer)
        self._logger.info("initialized")

    def _expand_folders(self) -> None:
        """The albums folder and the json files may begin with ``~`` (the box on the PC)."""
        self._config["albums-folder"] = os.path.expanduser(self._config["albums-folder"])
        self._config["json"] = {key: os.path.expanduser(value) for key, value in self._config["json"].items()}

    def _images_folder(self, rest_api_config: Mapping[str, Any]) -> str:
        """The folder of the covers of the stations: ``images`` next to the json files unless the config names one.
        The covers that a version before 0.29 kept in the folder ``cards`` are moved there."""
        folder = rest_api_config.get("images-folder")
        if folder:
            return os.path.expanduser(str(folder))
        json_folder = os.path.dirname(self._config["json"]["tags"])
        folder = os.path.join(json_folder, "images")
        moved = take_over(os.path.join(json_folder, "cards"), folder)
        if moved:
            self._logger.info("%d covers of radio stations moved from the folder 'cards' to '%s'", moved, folder)
        return folder

    def _volume_file(self) -> str:
        json_files = self._config["json"]
        return str(json_files.get("volume") or os.path.join(os.path.dirname(json_files["tags"]), "volume.json"))

    def _create_sync(self, images_folder: str) -> BoxSync:
        """The synchronization with the library (section ``sync``). Its folders lie next to the folder of the json
        files unless the config says otherwise."""
        json_files = self._config["json"]
        base = os.path.dirname(os.path.dirname(os.path.abspath(json_files["tags"])))
        config = self._config.get("sync", {})
        sync = BoxSync(
            os.path.expanduser(config.get("folder") or os.path.join(base, "sync")),
            os.path.expanduser(config.get("identity") or os.path.join(base, "identity")),
            __version__,
            get_child(self._logger, "sync"),
            self._config["albums-folder"],
            images_folder,
            float(config.get("retry-seconds", 30.0)),
            int(float(config.get("keep-free", 500)) * 2**20),
        )
        sync.prepare(
            {
                "tag": json_files["tags"],
                "target": json_files["targets"],
                "position": json_files["activities"],
                "volume": self._volume_file(),
            }
        )
        return sync

    def _create_health(self) -> Health:
        """Which version runs, and whether it is healthy - only on a box that was set up with slots."""
        config = self._config.get("sync", {})
        health = Health(
            self._slots,
            self._logger,
            float(config.get("stage1-seconds", 10.0)),
            float(config.get("stage2-seconds", 60.0)),
            self._sync.journal,
            look=(LOOK[0], float(config.get("look-hours", LOOK[1] / 3600)) * 3600, LOOK[2]),
            alone=lambda: not self._sync.library,
        )
        health.on_change = self._sync.tell
        self._sync.slots, self._sync.health = self._slots, health
        return health

    def _fit_changed(self) -> None:
        """Box and library fit together, or not any more. A library that is too old for the box makes the box stop
        (docs/developer/library.md, section 4.15): it plays nothing and shows the alarm pattern."""
        if self._sync.blocked:
            self._rgb_led.show_overlay_sequence(ALARM)
        else:
            self._rgb_led.clear_overlay()
        self._stmoperation.recheck_tag()

    def _playback_changed(self, message: object) -> None:
        """A pause or the end of a playback: the library hears of the position. Something plays because of a tag:
        the proof that a new version works."""
        if message in (PlaybackStatus.PAUSED, PlaybackStatus.STOPPED):
            self._sync.tell()
        elif message == PlaybackStatus.PLAYING:
            uid = self._stmoperation.get_current_uid()  # none: a radio test, which no tag started
            tag = self._tags.tags.get(uid) if uid is not None else None
            if tag is not None and tag.target_type is not None:
                self._health.played(tag.target_type.name)

    def _journal(self, message: object) -> None:
        """What the state machines note about the playback goes into the journal of the box, with the output it
        played on - the library turns the journal into the play statistics."""
        if isinstance(message, JournalEvent):
            fields = dict(message.fields)
            if message.event == "tag":
                fields["output"] = self._volumes.active.value
            self._sync.journal(message.event, **fields)

    def _missing(self) -> list[str]:
        """What the config asks for and does not run - stage 1 of the health check of a new version."""
        missing = []
        if self._driver_manager is not None:
            missing += [name for name, driver in self._driver_manager.drivers.items() if not driver.healthy()]
        if self._rest_api is not None and not self._rest_api.running:
            missing.append("web page")
        return missing

    def _create_volumes(self, hardware: bool, headphones_control: BluetoothVolumeControl) -> Volumes:
        """The volumes of speaker and headset. Start value and maximum: the config - or what has been set at runtime
        (volume json file)."""
        volume_control: VolumeControl
        if hardware:
            volume_control = AlsaVolumeControl(self._config["volume"].get("mixer-control", "Master"), self._logger)
            self._mixer_watch = volume_control
        else:
            volume_control = VirtualVolumeControl(self._logger)
        volume_config = self._config["volume"]
        limits = self._limits
        steps = {key: volume_config[key] for key in ("volume-step", "volume-step-hold") if key in volume_config}
        headphones_config = {**HEADPHONES_DEFAULTS, **self._config.get("bluetooth", {}), **steps}
        return Volumes(
            VolumeManager(volume_control, limits.apply(VolumeProfile.SPEAKER.value, volume_config), self._logger),
            VolumeManager(
                headphones_control,
                limits.apply(VolumeProfile.HEADPHONES.value, headphones_config),
                self._logger,
                "VolumeManager.headphones",
            ),
            limits,
            self._logger,
        )

    def _create_stmplayalbum(self, album: TargetAlbum, activity: ActivityAlbum) -> STMPlayAlbum:
        return STMPlayAlbum(
            self._config["albumplayer"],
            self._pubsub_client,
            self._logger,
            self._rgb_led,
            album,
            activity,
            self._player_factory,
            self._sync.files if self._sync.attached else None,
        )

    def _create_playradio(self, station: TargetRadio) -> PlayRadio:
        return PlayRadio(self._logger, self._rgb_led, station, self._radio_player_factory, self._pubsub_client)

    @property
    def stmoperation(self) -> STMOperation:
        return self._stmoperation

    @property
    def rest_api(self) -> RestApi | None:
        return self._rest_api

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Complete schema; the section ``drivers`` is optional (not needed with ``--no-hardware``)."""
        schema = super().get_schema()
        schema["properties"].update(get_drivers_schema())
        return schema

    @classmethod
    def _get_description(cls) -> str:
        return "RFID-controlled audio player for albums and internet radio."

    def _start(self) -> None:
        if self._log_buffer is not None and self._log_buffer not in self._logger.handlers:
            self._logger.addHandler(self._log_buffer)  # restart after stop
        self._headset.start()  # before the drivers: the bluetooth driver reports its state when it starts
        if self._driver_manager is not None:
            self._driver_manager.start()  # before the first message to the led
        self._rgb_led.set_color(Color.MAGENTA)
        self._stmvolume.start()
        if self._mixer_watch is not None:
            self._mixer_watch.watch(self._volumes.manager(VolumeProfile.SPEAKER).mixer_changed)
        self._rgb_led.set_color(Color.GREEN)
        if self._sync.blocked:
            self._rgb_led.show_overlay_sequence(ALARM)
        self._stmoperation.start()
        self._pubsub_client.subscribe(PLAYBACK, self._playback_changed)
        self._pubsub_client.subscribe(JOURNAL, self._journal)
        self._sync.journal(
            "start", slot=self._health.slot, state="probation" if self._health.probation else "confirmed"
        )
        self._sync.start()
        self._health.start(self._missing)
        if self._virtual_headset is not None:
            self._virtual_headset.start()
        if self._rest_api is not None:
            self._rest_api.start()

    def _stop(self) -> None:
        self._slots.wait_for_command()  # a command of the page that asked for this restart gets its answer first
        self._health.stop()
        if self._rest_api is not None:
            self._rest_api.stop()
        if self._virtual_headset is not None:
            self._virtual_headset.stop()
        self._sync.stop()
        self._pubsub_client.unsubscribe(PLAYBACK, self._playback_changed, ignore_not_found=True)
        self._pubsub_client.unsubscribe(JOURNAL, self._journal, ignore_not_found=True)
        self._stmoperation.stop()
        if self._mixer_watch is not None:
            self._mixer_watch.stop_watching()
        self._stmvolume.stop()
        self._headset.stop()  # ends a led overlay - the led must show red
        self._rgb_led.set_color(Color.RED)
        if self._driver_manager is not None:
            if isinstance(self._pubsub_client, LocalQueue):
                self._pubsub_client.wait_until_delivered()  # the led must receive the last message
            self._driver_manager.stop()
        if self._log_buffer is not None:
            self._logger.removeHandler(self._log_buffer)

    @classmethod
    def _argument_parser(cls) -> argparse.ArgumentParser:
        parser = super()._argument_parser()
        parser.add_argument(
            "--hardware",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="--no-hardware: run without device drivers and alsa mixer (virtual volume) - tags and buttons are "
            "then operated via the web page",
        )
        parser.add_argument(
            "--webserver",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="enable/disable the web page and rest api (default: as configured in rest-api)",
        )
        parser.add_argument(
            "--audio",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="--no-audio: tracks are played in real time without sound output (no sound card needed)",
        )
        return parser

    @classmethod
    def standalone(cls, args: Sequence[str] | None = None) -> None:
        """Command line entry point: the common options plus --[no-]hardware, --[no-]webserver, --[no-]audio."""
        arguments = cls._argument_parser().parse_args(args)
        stdout_log_level = "" if arguments.quiet else "INFO" if arguments.verbose else "WARNING"
        config = cls._read_config(arguments.config)
        instance = None
        for signum in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, _stop_on_signal)
        try:
            instance = cls(
                config,
                stdout_log_level=stdout_log_level,
                no_gui=arguments.no_gui,
                hardware=arguments.hardware,
                webserver=arguments.webserver,
                audio=arguments.audio,
            )
            instance.run()
        except Exception:
            if instance is not None:
                instance._logger.exception("%s terminated with an exception", cls.__name__)
            raise


def standalone() -> None:
    """Console script ``tinyjukebox``."""
    TinyJukebox.standalone()


if __name__ == "__main__":
    standalone()
