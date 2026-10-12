"""The logic of the bluetooth driver: exactly one paired headset, search and pairing, reconnecting, its volume and
buttons - on top of :class:`~tinyjukebox.devices.bluetooth.bluez.Bluez`. Runs in the event loop of the driver."""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from tinyjukebox.core.logging import get_child
from tinyjukebox.devices.bluetooth.bluez import Bluez, BluezError, Device
from tinyjukebox.messages import (
    BluetoothAction,
    BluetoothCommand,
    BluetoothDevice,
    BluetoothState,
    Button,
    PairingResult,
    PlaybackStatus,
)

__all__ = ["RECONNECT_INTERVAL", "SEARCH_TIME", "HeadsetLink", "Mixer", "MixerFactory", "button_of"]

RECONNECT_INTERVAL = 15.0
"""Seconds between the attempts to reach the paired headset while it is not connected."""
SEARCH_TIME = 60.0
"""Seconds a search for a headset in pairing mode lasts at most."""
COLLECT_TIME = 3.0
"""Seconds the search goes on after the first headset has been found - the one with the strongest signal wins."""
DISCOVERY_RETRY = 1.0  # seconds until a refused start of the search is tried again
VOLUME_RETRIES = 10
VOLUME_RETRY_INTERVAL = 0.5  # seconds - the mixer of BlueALSA appears a moment after the headset has connected

_COMMANDS = {"Play": Button.PLAY, "Pause": Button.PAUSE, "Stop": Button.PAUSE, "Next": Button.NEXT,
             "Previous": Button.PREV, "VolumeUp": Button.VOLUME_UP, "VolumeDown": Button.VOLUME_DOWN}  # fmt: skip


def button_of(command: str, status: PlaybackStatus, toggle: bool) -> Button | None:
    """The button for a command of the headset (``Play``, ``Pause``, ``PlayPause``, ``Stop``, ``Next``, ``Previous``,
    ``VolumeUp``, ``VolumeDown``). ``PlayPause`` - and with ``toggle`` also ``Play`` and ``Pause`` - switch between
    play and pause: some headsets send the same button for both."""
    if command == "PlayPause" or (toggle and command in ("Play", "Pause")):
        return Button.PAUSE if status is PlaybackStatus.PLAYING else Button.PLAY
    return _COMMANDS.get(command)


class Mixer(Protocol):
    """The volume control of the connected headset (blocking calls - used in a worker thread)."""

    def set_volume(self, volume: float) -> bool:
        """Set the volume in % - False if the mixer is not available (yet)."""

    def watch(self, callback: Callable[[float], None]) -> None:
        """Call ``callback`` (in another thread) with the volume in % whenever it changes."""

    def stop_watching(self) -> None: ...


MixerFactory = Callable[[str], Mixer]
"""address of the headset -> its mixer"""


class HeadsetLink:
    """Keeps the connection to the one paired headset and publishes its :class:`~tinyjukebox.messages.BluetoothState`.

    * **search** (:meth:`command` ``PAIR``): looks for audio outputs in pairing mode for :data:`SEARCH_TIME` seconds,
      takes the one with the strongest signal, pairs, trusts and connects it - and only then removes all other
      pairings. If the search fails, the old pairing stays.
    * **reconnect**: tries to reach the paired headset every :data:`RECONNECT_INTERVAL` seconds.
    * **connected** means that the headset can play (A2DP transport) - not just a bluetooth connection.
    * **buttons** of the headset (:meth:`player_command`) are published as buttons of the box.
    """

    def __init__(
        self,
        bluez: Bluez,
        mixer_factory: MixerFactory | None,
        publish_state: Callable[[BluetoothState], object],
        publish_button: Callable[[Button], object],
        logger: logging.Logger,
        play_pause_toggle: bool = False,
        register_player: bool = True,
        reconnect_interval: float = RECONNECT_INTERVAL,
        search_time: float = SEARCH_TIME,
        collect_time: float = COLLECT_TIME,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._bluez = bluez
        self._mixer_factory = mixer_factory
        self._publish_state = publish_state
        self._publish_button = publish_button
        self._toggle = play_pause_toggle
        self._register_player = register_player
        self._reconnect_interval = reconnect_interval
        self._search_time = search_time
        self._collect_time = collect_time

        self._headset: str | None = None  # object path of the paired headset
        self._candidate: str | None = None  # object path of the headset that is being paired
        self._pairing = False
        self._removing = False  # the pairing is being removed
        self._result: PairingResult | None = None
        self._reconnecting: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._changed = asyncio.Event()
        self._mixer: Mixer | None = None
        self._mixer_address: str | None = None
        self._volume: float | None = None
        self._wanted_volume: float | None = None  # set by the box, not confirmed by the mixer yet
        self._volume_task: asyncio.Task[None] | None = None
        self._published: BluetoothState | None = None
        self._running = False  # connected to BlueZ - commands are accepted
        self.playback = PlaybackStatus.STOPPED

    # --- agent policy / player commands (called by Bluez) ---------------------------------------------------------

    def allows_pairing(self, device_path: str) -> bool:
        return device_path == self._candidate

    def allows_service(self, device_path: str) -> bool:
        return device_path in (self._headset, self._candidate)

    def player_command(self, command: str) -> None:
        button = button_of(command, self.playback, self._toggle)
        self._logger.info("player_command - %s -> %s", command, button)
        if button is not None:
            self._publish_button(button)

    # --- life cycle -----------------------------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Connect to BlueZ and keep the headset connected until ``stop`` is set.

        :raises BluezError: if BlueZ or the adapter is missing."""
        self._bluez.on_change = self._on_change
        try:
            await self._bluez.connect(self, self if self._register_player else None)
            self._running = True
            self._headset = self._paired_headset(warn=True)
            self._update()
            while not stop.is_set():
                if self._reconnecting is None or self._reconnecting.done():
                    self._reconnecting = self._spawn(self._reconnect())
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self._reconnect_interval)
        finally:
            self._running = False
            self._bluez.on_change = None
            while self._tasks:  # ending tasks may start others (e.g. the mixer follows the state)
                tasks = list(self._tasks)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            self._mixer_address = None
            await self._set_mixer(None)
            with contextlib.suppress(BluezError, TimeoutError, OSError):
                await self._bluez.stop_discovery()
            await self._bluez.close()

    def _paired_headset(self, warn: bool = False) -> str | None:
        """The headset BlueZ has paired: the (first) paired audio output."""
        paired = [device for device in self._bluez.devices() if device.paired]
        paired.sort(key=lambda device: (not device.is_audio_sink, device.path))
        if warn and len(paired) > 1:
            self._logger.warning(
                "%s paired devices - using %s (a new pairing removes the others)", len(paired), paired[0].address
            )
        return paired[0].path if paired else None

    def _device(self, path: str | None) -> Device | None:
        return next((device for device in self._bluez.devices() if device.path == path), None)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> "asyncio.Task[None]":
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._logger.error("task failed", exc_info=task.exception())

    # --- state ----------------------------------------------------------------------------------------------------

    def _on_change(self) -> None:
        """Something changed in BlueZ (called by Bluez in the event loop)."""
        self._changed.set()
        self._update()

    def _update(self) -> None:
        """Publish the state if it changed and follow the connection with the mixer."""
        if not self._running:
            return
        headset = self._device(self._headset)
        if headset is None and not self._pairing and not self._removing:
            # removed or paired outside the box (bluetoothctl), or BlueZ has been restarted
            self._headset = self._paired_headset()
            headset = self._device(self._headset)
        connected = headset is not None and headset.connected and headset.audio
        address = headset.address if headset is not None and connected else None
        if address != self._mixer_address:
            self._mixer_address = address
            self._volume = None
            if address is None:
                self._wanted_volume = None
            self._spawn(self._set_mixer(address))
        state = BluetoothState(
            device=BluetoothDevice(headset.address, headset.name) if headset is not None else None,
            connected=connected,
            battery=headset.battery if headset is not None else None,
            volume=self._volume if connected else None,
            pairing=self._pairing,
            pairing_result=self._result,
        )
        if state != self._published:
            self._published = state
            self._publish_state(state)

    async def _set_mixer(self, address: str | None) -> None:
        """Watch the volume control of the connected headset (``None``: stop watching)."""
        loop = asyncio.get_running_loop()
        mixer, self._mixer = self._mixer, None
        if mixer is not None:
            await loop.run_in_executor(None, mixer.stop_watching)
        if address is None or self._mixer_factory is None or address != self._mixer_address:
            return
        mixer = self._mixer_factory(address)
        self._mixer = mixer

        def reported(volume: float) -> None:  # in the watch thread of the mixer
            loop.call_soon_threadsafe(self._volume_reported, address, volume)

        mixer.watch(reported)
        if self._wanted_volume is not None and (self._volume_task is None or self._volume_task.done()):
            self._volume_task = self._spawn(self._set_volume())  # commanded before the mixer was there

    def _volume_reported(self, address: str, volume: float) -> None:
        if address == self._mixer_address:
            self._volume = volume
            self._update()

    # --- commands -------------------------------------------------------------------------------------------------

    def command(self, command: BluetoothCommand) -> None:
        """A command of the box (called in the event loop)."""
        self._logger.info("command - %s", command)
        if not self._running:
            self._logger.warning("command - ignored: no connection to BlueZ")
            return
        if command.action is BluetoothAction.PAIR:
            if self._pairing:
                self._logger.info("a search is running already")
                return
            self._pairing = True  # before the task starts - a second command is ignored
            self._result = None
            self._spawn(self._pair())
            self._update()
        elif command.action is BluetoothAction.UNPAIR:
            self._spawn(self._unpair())
        elif command.action is BluetoothAction.VOLUME and command.volume is not None:
            self._wanted_volume = command.volume
            if self._mixer is not None and (self._volume_task is None or self._volume_task.done()):
                self._volume_task = self._spawn(self._set_volume())

    async def _set_volume(self) -> None:
        """Set the wanted volume on the mixer of the connected headset - again if the mixer is not there yet, and
        always the latest one."""
        loop = asyncio.get_running_loop()
        failures = 0
        while (volume := self._wanted_volume) is not None and (mixer := self._mixer) is not None:
            if await loop.run_in_executor(None, mixer.set_volume, volume):
                failures = 0
                if self._wanted_volume == volume:
                    self._wanted_volume = None
                if mixer is self._mixer:  # known now - even if the report of the mixer has been missed
                    self._volume = volume
                    self._update()
                continue
            failures += 1
            if failures >= VOLUME_RETRIES:
                self._logger.error("the volume of the headset cannot be set")
                return
            await asyncio.sleep(VOLUME_RETRY_INTERVAL)

    def playback_status(self, status: PlaybackStatus) -> None:
        """What the box plays (called in the event loop) - reported to the headset."""
        self.playback = status
        self._bluez.set_playback_status(status.value)

    async def _unpair(self) -> None:
        headset = self._headset
        if headset is None or self._removing:
            return
        self._removing = True
        try:
            await self._bluez.remove_device(headset)
            self._headset = None
        except (BluezError, TimeoutError) as e:
            self._logger.error("removing the headset failed: %s", e)
        finally:
            self._removing = False
            self._update()

    async def _reconnect(self) -> None:
        headset = self._device(self._headset)
        if headset is None or self._pairing or (headset.connected and headset.audio):
            return
        try:
            await self._bluez.connect_device(headset.path)
        except (BluezError, TimeoutError) as e:
            self._logger.debug("headset %s not reachable: %s", headset.address, e)  # switched off or out of range

    # --- search and pairing ---------------------------------------------------------------------------------------

    async def _pair(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._search_time
        result = PairingResult.FAILED
        try:
            await self._start_discovery(deadline)
            found = await self._find(deadline)
            if found is not None and found.paired:
                # paired with the box already (e.g. the same headset after it has been reset): pair it anew
                self._logger.info("%s is paired already - removing the old pairing", found.address)
                if found.path == self._headset:
                    self._headset = None
                await self._bluez.remove_device(found.path)
                found = await self._find(deadline, found.address)
            if found is None:
                result = PairingResult.NOT_FOUND
                return
            await self._bluez.stop_discovery()  # pairing while searching is unreliable
            self._logger.info("pairing %s (%s), signal %s dBm", found.name, found.address, found.rssi)
            self._candidate = found.path
            await self._bluez.pair(found.path)
            await self._bluez.set_trusted(found.path)
            try:
                await self._bluez.connect_device(found.path)
            except (BluezError, TimeoutError) as e:
                self._logger.warning("paired, but not connected yet: %s", e)  # the reconnect tries again
            # only now the old pairing goes - and everything else that has ever been paired (one headset only)
            for device in self._bluez.devices():
                if device.paired and device.path != found.path:
                    self._logger.info("removing the pairing of %s (%s)", device.name, device.address)
                    with contextlib.suppress(BluezError, TimeoutError):
                        await self._bluez.remove_device(device.path)
            self._headset = found.path
            result = PairingResult.PAIRED
        except (BluezError, TimeoutError) as e:
            self._logger.error("pairing failed: %s", e)
        finally:
            with contextlib.suppress(BluezError, TimeoutError, OSError):
                await self._bluez.stop_discovery()
            self._candidate = None
            self._pairing = False
            self._result = result
            self._logger.info("search ended: %s", result.value)
            self._update()

    async def _start_discovery(self, deadline: float) -> None:
        """Start the search - again and again until ``deadline`` if BlueZ refuses (e.g. while it still tries to reach
        the old headset)."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self._bluez.start_discovery()
                return
            except BluezError as e:
                if loop.time() + DISCOVERY_RETRY >= deadline:
                    raise
                self._logger.info("the search cannot start yet: %s", e)
                await asyncio.sleep(DISCOVERY_RETRY)

    async def _find(self, deadline: float, address: str | None = None) -> Device | None:
        """The audio output in pairing mode with the strongest signal (or the one with ``address``) - ``None`` if
        none shows up until ``deadline``."""
        loop = asyncio.get_running_loop()
        first_seen: float | None = None
        while True:
            self._changed.clear()
            now = loop.time()
            candidates = [
                device
                for device in self._bluez.devices()
                if device.discovered and device.is_audio_sink and address in (None, device.address)
            ]
            if candidates:
                first_seen = now if first_seen is None else first_seen
                if address is not None or now - first_seen >= self._collect_time or now >= deadline:
                    return max(candidates, key=lambda device: device.rssi if device.rssi is not None else -1000)
            if now >= deadline:
                return None
            wake = deadline if first_seen is None else min(deadline, first_seen + self._collect_time)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), max(0.0, wake - now))
