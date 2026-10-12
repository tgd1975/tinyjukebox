"""Bluetooth headset: BlueZ (D-Bus) pairs and connects it, BlueALSA plays on it and sets its volume."""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Mapping
from typing import Any

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.bluetooth.avrcp_input import AvrcpInput
from tinyjukebox.devices.bluetooth.bluez import Bluez, BluezError, DbusBluez
from tinyjukebox.devices.bluetooth.link import (
    COLLECT_TIME,
    RECONNECT_INTERVAL,
    SEARCH_TIME,
    HeadsetLink,
    Mixer,
    MixerFactory,
    button_of,
)
from tinyjukebox.devices.schema.bluetooth import get_schema
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_PRESSED,
    PLAYBACK,
    BluetoothCommand,
    BluetoothState,
    Button,
    PlaybackStatus,
)
from tinyjukebox.volumemanager import AlsaVolumeControl

__all__ = ["Bluetooth"]

START_TIMEOUT = 15.0  # seconds to wait for the event loop at the start
RETRY_INTERVAL = 30.0  # seconds until BlueZ is tried again if it is not available
MIXER_RETRY = 0.5  # seconds until the mixer of the headset is opened again - it appears a moment after the headset


class Bluetooth(ADriver):
    """The box as bluetooth sender (like a tv set) for exactly one headset - see
    :class:`~tinyjukebox.devices.bluetooth.link.HeadsetLink` for what it does.

    * publishes :class:`~tinyjukebox.messages.BluetoothState` on :data:`~tinyjukebox.messages.BLUETOOTH_STATE` and the
      buttons of the headset on :data:`~tinyjukebox.messages.BUTTON_PRESSED`,
    * receives :class:`~tinyjukebox.messages.BluetoothCommand` on :data:`~tinyjukebox.messages.BLUETOOTH_COMMAND` and
      the playback status on :data:`~tinyjukebox.messages.PLAYBACK`.

    BlueZ is used via D-Bus in an event loop (asyncio) in a thread of its own - the handlers only pass the messages
    on to it. Needs the services bluetooth and bluealsa (``bluez-alsa-utils``) and the groups bluetooth and audio.
    Without BlueZ (service not running yet, no adapter) the box works without headset; the driver tries again every
    :data:`RETRY_INTERVAL` seconds.

    Example::

        driver:
            type: bluetooth
            adapter: hci0             # optional
            avrcp: player             # optional - player | input: how the buttons of the headset arrive
            play-pause-toggle: false  # optional - true: play and pause both switch between play and pause
    """

    # seconds - see tinyjukebox.devices.bluetooth.link (shorter in tests)
    reconnect_interval = RECONNECT_INTERVAL
    search_time = SEARCH_TIME
    collect_time = COLLECT_TIME
    retry_interval = RETRY_INTERVAL

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
        bluez: Bluez | None = None,
        mixer_factory: MixerFactory | None = None,
    ) -> None:
        """``bluez``, ``mixer_factory``: replace BlueZ and the BlueALSA mixer (tests)."""
        super().__init__(config, pubsub_client, logger)
        self._bluez = bluez
        self._mixer_factory = mixer_factory
        self._avrcp_input = self._config.get("avrcp", "player") == "input"
        self._toggle = bool(self._config.get("play-pause-toggle", False))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: LoggerThread | None = None
        self._link: HeadsetLink | None = None
        self._active_bluez: Bluez | None = None
        self._input: AvrcpInput | None = None
        self._started = threading.Event()
        self._playback = PlaybackStatus.STOPPED  # the last one - a new link starts with it
        self._add_ui_command("state", self._cmd_state)

    def _mixer(self, address: str) -> Mixer:
        # the mixer of BlueALSA for one headset ("single device mode"): its control is just 'A2DP' and the mixer
        # exists only while the headset is connected
        return AlsaVolumeControl("A2DP", self._logger, device=f"bluealsa:{address}", watch_retry=MIXER_RETRY)

    def _publish_button(self, button: Button) -> None:
        self._publish_value(BUTTON_PRESSED, button)

    def _publish_state(self, state: BluetoothState) -> None:
        self._publish_value(BLUETOOTH_STATE, state)

    def _input_command(self, command: str) -> None:
        """A button read from the input device (reader thread)."""
        link = self._link
        if link is not None:
            button = button_of(command, link.playback, self._toggle)
            if button is not None:
                self._publish_button(button)

    def _cmd_state(self, args: str) -> None:
        """state - the devices BlueZ knows: STATE"""
        bluez = self._active_bluez
        try:
            devices = bluez.devices() if bluez is not None else []
        except RuntimeError:  # changed by the event loop meanwhile
            devices = []
            print(f"[{self._name}] busy - try again.")
        for device in devices:
            print(
                f"[{self._name}] {device.address} '{device.name}' paired: {device.paired}, connected: "
                f"{device.connected}, audio: {device.audio}, battery: {device.battery}"
            )
        if not devices:
            print(f"[{self._name}] no devices.")

    # --- event loop thread ----------------------------------------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._main(loop))
        finally:
            self._started.set()
            self._loop = None
            pending = asyncio.all_tasks(loop)  # nothing is left behind - whatever happened
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _main(self, loop: asyncio.AbstractEventLoop) -> None:
        stop = self._stop_event = asyncio.Event()
        self._loop = loop
        self._started.set()
        failure: str | None = None
        while not stop.is_set():
            bluez = self._bluez or DbusBluez(self._logger, self._config.get("adapter", "hci0"))
            link = HeadsetLink(
                bluez,
                self._mixer_factory or self._mixer,
                self._publish_state,
                self._publish_button,
                self._logger,
                play_pause_toggle=self._toggle,
                register_player=not self._avrcp_input,
                reconnect_interval=self.reconnect_interval,
                search_time=self.search_time,
                collect_time=self.collect_time,
            )
            link.playback_status(self._playback)
            self._active_bluez, self._link = bluez, link
            try:
                await link.run(stop)
                failure = None
            except (BluezError, OSError, EOFError, TimeoutError) as e:
                # e.g. the service bluetooth is not running (yet) - the box plays on its speaker meanwhile
                level = logging.DEBUG if str(e) == failure else logging.ERROR  # the same reason only once
                failure = str(e)
                self._logger.log(
                    level, "Bluetooth - not available: %s - trying again every %s s", e, self.retry_interval
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.retry_interval)
            finally:
                self._link = None

    def _in_loop(self, callback: Any, *args: Any) -> None:
        loop = self._loop
        if loop is None:
            self._logger.warning("Bluetooth - not running, dropped %s", args)
            return
        loop.call_soon_threadsafe(callback, *args)

    def _command_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BLUETOOTH_COMMAND`."""
        link = self._link
        if isinstance(message, BluetoothCommand) and link is not None:
            self._in_loop(link.command, message)
        else:
            self._logger.warning("Bluetooth - cannot process %r (bluetooth available: %s)", message, link is not None)

    def _playback_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.PLAYBACK`."""
        if not isinstance(message, PlaybackStatus):
            return
        self._playback = message
        link = self._link
        if link is not None:
            self._in_loop(link.playback_status, message)

    # --- life cycle -----------------------------------------------------------------------------------------------

    def _driver_start(self) -> None:
        self._started.clear()
        self._thread = LoggerThread(target=self._run, name="bluetooth", logger=self._logger, daemon=True)
        self._thread.start()
        self._started.wait(START_TIMEOUT)
        self._pubsub_client.subscribe(BLUETOOTH_COMMAND, self._command_handler)
        self._pubsub_client.subscribe(PLAYBACK, self._playback_handler)
        if self._avrcp_input:
            self._input = AvrcpInput(self._input_command, self._logger)
            self._input.start()

    def _driver_stop(self) -> None:
        self._pubsub_client.unsubscribe(BLUETOOTH_COMMAND, self._command_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(PLAYBACK, self._playback_handler, ignore_not_found=True)
        if self._input is not None:
            self._input.stop()
            self._input = None
        loop, stop_event = self._loop, self._stop_event
        if loop is not None and stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._link = None

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
