"""Virtual headset for running without hardware (``--no-hardware``): behaves like the bluetooth driver on the pubsub
side - the whole headset logic of the box can be tried on the pc."""

import dataclasses
import logging
import threading
from collections.abc import Callable

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.ui import Command
from tinyjukebox.core.ui.tools import parse
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_PRESSED,
    BluetoothAction,
    BluetoothCommand,
    BluetoothDevice,
    BluetoothState,
    Button,
    PairingResult,
)

__all__ = ["VIRTUAL_DEVICE", "VirtualHeadset"]

VIRTUAL_DEVICE = BluetoothDevice("00:00:00:00:00:01", "Virtual headset")
SEARCH_TIME = 2.0  # seconds a (always successful) search takes
VOLUME_STEP = 6.0  # % per press of a volume button of the headset


class VirtualHeadset:
    """Publishes :class:`~tinyjukebox.messages.BluetoothState` and the buttons of the headset like the bluetooth driver.

    * ``PAIR``: after :data:`SEARCH_TIME` seconds the virtual headset is paired and connected,
    * ``UNPAIR``: it is gone, ``VOLUME``: it takes the volume,
    * :meth:`switch`: the headset is switched on (connects if paired) or off,
    * :meth:`press`: a button of the headset - the volume buttons change its own volume (like a headset with
      absolute volume; the box follows and limits it).
    """

    def __init__(self, pubsub_client: PubSubClient, logger: logging.Logger, search_time: float = SEARCH_TIME) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._pubsub_client = pubsub_client
        self._search_time = search_time
        self._lock = threading.RLock()  # publishing may deliver a command at once (synchronous pubsub client)
        self._state = BluetoothState(virtual=True)
        self._switched_on = True
        self._timer: threading.Timer | None = None
        self.ui_commands: dict[str, Command] = {
            "virtual_headset": self._cmd_switch,
            "virtual_headset_button": self._cmd_button,
        }

    @property
    def state(self) -> BluetoothState:
        return self._state

    def start(self) -> None:
        self._pubsub_client.subscribe(BLUETOOTH_COMMAND, self._command_handler)
        self._publish(lambda state: state)

    def stop(self) -> None:
        self._pubsub_client.unsubscribe(BLUETOOTH_COMMAND, self._command_handler, ignore_not_found=True)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _publish(self, change: Callable[[BluetoothState], BluetoothState]) -> None:
        with self._lock:
            self._state = change(self._state)
            self._pubsub_client.publish(BLUETOOTH_STATE, self._state)

    def _command_handler(self, message: object) -> None:
        if not isinstance(message, BluetoothCommand):
            self._logger.error("expected a BluetoothCommand, received %r", message)
            return
        self._logger.info("command %s", message)
        if message.action is BluetoothAction.PAIR:
            with self._lock:
                if self._state.pairing:
                    return
                self._timer = threading.Timer(self._search_time, self._paired)
                self._timer.name = "VirtualHeadset.search"
                self._timer.daemon = True
                self._timer.start()
            self._publish(lambda state: dataclasses.replace(state, pairing=True, pairing_result=None))
        elif message.action is BluetoothAction.UNPAIR:
            self._publish(
                lambda state: dataclasses.replace(state, device=None, connected=False, battery=None, volume=None)
            )
        elif message.action is BluetoothAction.VOLUME and message.volume is not None:
            volume = message.volume
            self._publish(lambda state: dataclasses.replace(state, volume=volume) if state.connected else state)

    def _paired(self) -> None:
        self._switched_on = True
        self._publish(
            lambda state: dataclasses.replace(
                state, device=VIRTUAL_DEVICE, connected=True, battery=80, pairing=False,
                pairing_result=PairingResult.PAIRED,
            )
        )  # fmt: skip

    def switch(self, on: bool) -> None:
        """Switch the headset on or off - it connects if it is paired."""
        self._switched_on = on
        self._publish(
            lambda state: dataclasses.replace(
                state,
                connected=on and state.device is not None,
                battery=80 if on and state.device is not None else None,
                volume=state.volume if on and state.connected else None,
            )
        )

    def press(self, button: Button) -> None:
        """A button of the headset."""
        if not self._state.connected:
            self._logger.info("button %s ignored - the headset is not connected", button)
            return
        if button in (Button.VOLUME_UP, Button.VOLUME_DOWN):
            step = VOLUME_STEP if button is Button.VOLUME_UP else -VOLUME_STEP
            self._publish(
                lambda state: dataclasses.replace(state, volume=min(100.0, max(0.0, (state.volume or 0) + step)))
            )
        else:
            self._pubsub_client.publish(BUTTON_PRESSED, button)

    # --- console --------------------------------------------------------------------------------------------------

    def _cmd_switch(self, args: str) -> None:
        """virtual_headset - switch the virtual headset on or off: VIRTUAL_HEADSET on|off"""
        words = [word.lower() for word in parse(args)]
        if words not in (["on"], ["off"]):
            print("expected 'VIRTUAL_HEADSET on|off'.")
            return
        self.switch(words == ["on"])
        device = self._state.device
        print(f"virtual headset switched {words[0]}" + ("" if device else " - it is not paired (bluetooth_pair)."))

    def _cmd_button(self, args: str) -> None:
        """virtual_headset_button - press a button of the virtual headset:
        VIRTUAL_HEADSET_BUTTON play|pause|next|prev|volume-up|volume-down"""
        words = [word.lower() for word in parse(args)]
        buttons = {button.value: button for button in Button}
        if len(words) != 1 or words[0] not in buttons:
            print(f"expected 'VIRTUAL_HEADSET_BUTTON {'|'.join(buttons)}'.")
            return
        if not self._state.connected:
            print("the virtual headset is not connected.")
            return
        self.press(buttons[words[0]])
        print(f"pressed {words[0]}.")
