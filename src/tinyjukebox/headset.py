"""The bluetooth headset as seen by the box: output and volume follow the connection, a lost headset pauses the
playback, next + prev held start the pairing. Bluetooth itself is the business of the bluetooth driver (or of the
virtual headset) - they talk via :data:`~tinyjukebox.messages.BLUETOOTH_STATE` and
:data:`~tinyjukebox.messages.BLUETOOTH_COMMAND`."""

import logging
import threading
from typing import Any, Protocol

from tinyjukebox.albumplayer.simpleplayer import AudioOutput, bluealsa_device
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_HELD,
    BUTTON_PRESSED,
    BUTTON_RELEASED,
    BluetoothAction,
    BluetoothCommand,
    BluetoothState,
    Button,
    PairingResult,
)
from tinyjukebox.rgbled import Color, RGBLed
from tinyjukebox.stm.stmoperation import AlbumControl
from tinyjukebox.volumemanager import VolumeProfile, Volumes

__all__ = ["PAIRING_BUTTONS", "BluetoothVolumeControl", "Headset"]

PAIRING_BUTTONS = frozenset({Button.NEXT, Button.PREV})
"""Held together (``hold_timeout`` of their input drivers): search a headset and pair it."""
SEARCH_LED_TIMEOUT = 120.0  # the search led ends by itself at the latest - the driver ends a search after 60 s
SEARCH_BLINK = 0.2
PAIRED_LED = 2.0  # seconds white after a successful pairing
FAILED_LED = 3.0  # seconds red blinking after a failed search
SPEAKER_AFTER_LOSS = 3.0
"""Seconds after the loss of the headset after which the speaker is the output at the latest - normally it is as soon
as the playback has paused."""


def _address(state: BluetoothState) -> str | None:
    return state.device.address if state.device is not None else None


class BluetoothVolumeControl:
    """Volume of the headset: set by the bluetooth driver (:data:`~tinyjukebox.messages.BLUETOOTH_COMMAND`) - only while
    the headset is connected."""

    def __init__(self, pubsub_client: PubSubClient, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._pubsub_client = pubsub_client
        self.connected = False

    def set_volume(self, volume: float) -> None:
        if self.connected:
            self._pubsub_client.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, volume))


class Albums(Protocol):
    def get_album(self) -> AlbumControl | None: ...


class Headset:
    """Reacts on the :class:`~tinyjukebox.messages.BluetoothState` of the driver:

    * connected: the players play on the headset (a playing track continues there), the buttons change its volume,
      which starts at its start value,
    * disconnected: a playing album or radio station pauses (it continues if the headset comes back within 10 min),
      the speaker is the output again once the playback has paused - and plays only after a button press,
    * volume changed on the headset: taken over, limited to its maximum,
    * search: led overlay (blue blinking fast, then 2 s white or 3 s red blinking).

    The handlers run in the pubsub dispatch thread - they only queue events and publish messages.
    """

    def __init__(
        self,
        pubsub_client: PubSubClient,
        logger: logging.Logger,
        rgbled: RGBLed,
        output: AudioOutput,
        volumes: Volumes,
        volume_control: BluetoothVolumeControl,
        albums: Albums,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._pubsub_client = pubsub_client
        self._rgbled = rgbled
        self._output = output
        self._volumes = volumes
        self._volume_control = volume_control
        self._albums = albums
        self._lock = threading.RLock()  # the handlers publish - a synchronous pubsub client calls them again
        self._state: BluetoothState | None = None
        self._held: set[Button] = set()
        self._losses = (
            0  # counts the losses of the headset: a switch to the speaker asked for before a reconnect is void
        )
        self._speaker_timer: threading.Timer | None = None

    @property
    def state(self) -> BluetoothState | None:
        """The last state of the driver - ``None``: there is no bluetooth driver (or it has not reported yet)."""
        return self._state

    def info(self) -> dict[str, Any]:
        state = self._state
        if state is None:
            return {"available": False, "virtual": False, "device": None, "connected": False, "battery": None,
                    "volume": None, "pairing": False, "pairing_result": None}  # fmt: skip
        device = state.device
        return {
            "available": True,
            "virtual": state.virtual,
            "device": {"address": device.address, "name": device.name} if device is not None else None,
            "connected": state.connected,
            "battery": state.battery,
            "volume": state.volume,
            "pairing": state.pairing,
            "pairing_result": state.pairing_result.value if state.pairing_result is not None else None,
        }

    # --- commands -------------------------------------------------------------------------------------------------

    def pair(self) -> None:
        """Search a headset in pairing mode and pair it (the driver ignores it while a search runs). Without
        bluetooth the led shows the failure at once."""
        if self._state is None:
            self._logger.warning("pair - bluetooth is not available")
            self._rgbled.show_overlay(Color.RED, Color.BLACK, SEARCH_BLINK, FAILED_LED)
            return
        self._logger.info("pair - starting the search")
        self._pubsub_client.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.PAIR))

    def unpair(self) -> None:
        self._logger.info("unpair")
        self._pubsub_client.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.UNPAIR))

    # --- handlers -------------------------------------------------------------------------------------------------

    def _state_handler(self, message: object) -> None:
        if not isinstance(message, BluetoothState):
            self._logger.error("_state_handler - expected a BluetoothState, received %r", message)
            return
        with self._lock:
            previous = self._state or BluetoothState()
            self._state = message
            self._logger.info("_state_handler - %s", message)
            # the address counts: BlueZ may learn the name of the headset only after it has connected
            same_device = _address(previous) == _address(message)
            if message.connected and (not previous.connected or not same_device):
                self._connected(message)
            elif previous.connected and not message.connected:
                self._disconnected()
            elif message.connected and message.volume is not None and message.volume != previous.volume:
                # changed on the headset (absolute volume) - or the report of an own change, which is ignored
                self._volumes.manager(VolumeProfile.HEADPHONES).mixer_changed(message.volume)
            self._show_search(previous, message)

    def _connected(self, state: BluetoothState) -> None:
        assert state.device is not None  # noqa: S101 - connected implies a paired headset
        address = state.device.address
        self._logger.info("headset %s connected - playing on it", address)
        self._losses += 1  # a switch to the speaker that waits for a paused playback is void now
        self._cancel_speaker_timer()
        volume = self._volumes.manager(VolumeProfile.HEADPHONES)
        self._volume_control.connected = True
        self._volumes.use(VolumeProfile.HEADPHONES)
        volume.reset()  # never as loud as the last time
        # the players open the headset with the current volume - the volume set above may arrive after the first sound
        self._output.use_headset(None if state.virtual else lambda: bluealsa_device(address, volume.get_volume()))
        album = self._albums.get_album()
        if album is not None:
            album.cmd_output_changed()

    def _disconnected(self) -> None:
        self._volume_control.connected = False
        self._volumes.use(VolumeProfile.SPEAKER)
        album = self._albums.get_album()
        if album is None:
            self._logger.info("headset disconnected - the speaker is the output again")
            self._output.use_speaker()
            return
        self._logger.info("headset disconnected - pausing, the speaker is the output again once the playback paused")
        # until the playback has paused the output stays on the headset that has gone: what starts meanwhile - a
        # track that was due, a button pressed just before - fails there and pauses instead of playing on the speaker
        self._losses += 1
        loss = self._losses

        def speaker() -> None:
            self._speaker_after(loss)

        self._cancel_speaker_timer()
        timer = threading.Timer(SPEAKER_AFTER_LOSS, speaker)  # if the playback ends without handling it
        timer.name, timer.daemon = "Headset.speaker", True
        self._speaker_timer = timer
        timer.start()
        album.cmd_output_lost(then=speaker)

    def _speaker_after(self, loss: int) -> None:
        """The playback has handled the loss of the headset (or the time is up): the speaker is the output - unless
        the headset has come back meanwhile."""
        with self._lock:
            if loss != self._losses:
                return
            self._losses += 1  # done: the other one of the two calls does nothing
            self._cancel_speaker_timer()
            self._output.use_speaker()
            self._logger.info("the speaker is the output again")

    def _cancel_speaker_timer(self) -> None:
        timer, self._speaker_timer = self._speaker_timer, None
        if timer is not None:
            timer.cancel()

    def _show_search(self, previous: BluetoothState, state: BluetoothState) -> None:
        if state.pairing and not previous.pairing:
            self._rgbled.show_overlay(Color.BLUE, Color.BLACK, SEARCH_BLINK, SEARCH_LED_TIMEOUT)
        elif previous.pairing and not state.pairing:
            if state.pairing_result is PairingResult.PAIRED:
                self._rgbled.show_overlay(Color.WHITE, duration=PAIRED_LED)
            else:
                self._rgbled.show_overlay(Color.RED, Color.BLACK, SEARCH_BLINK, FAILED_LED)

    def _button_handler(self, topic: str, message: object) -> None:
        """Next + prev held together start the search. A press or release ends the hold of a button."""
        if not isinstance(message, Button):
            return
        with self._lock:
            if topic != BUTTON_HELD:
                self._held.discard(message)
                return
            self._held.add(message)
            if not self._held >= PAIRING_BUTTONS:
                return
            self._held -= PAIRING_BUTTONS
        self._logger.info("next and prev held - pairing")
        self.pair()

    def _pressed(self, message: object) -> None:
        self._button_handler(BUTTON_PRESSED, message)

    def _held_handler(self, message: object) -> None:
        self._button_handler(BUTTON_HELD, message)

    def _released(self, message: object) -> None:
        self._button_handler(BUTTON_RELEASED, message)

    # --- start / stop ---------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._pubsub_client.subscribe(BLUETOOTH_STATE, self._state_handler)
        self._pubsub_client.subscribe(BUTTON_PRESSED, self._pressed)
        self._pubsub_client.subscribe(BUTTON_HELD, self._held_handler)
        self._pubsub_client.subscribe(BUTTON_RELEASED, self._released)

    def stop(self) -> None:
        self._pubsub_client.unsubscribe(BLUETOOTH_STATE, self._state_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._pressed, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_HELD, self._held_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_RELEASED, self._released, ignore_not_found=True)
        with self._lock:
            self._cancel_speaker_timer()
        self._rgbled.clear_overlay()
