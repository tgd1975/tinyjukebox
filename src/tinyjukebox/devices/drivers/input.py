"""GPIO input of a button of the box."""

import logging
import threading
from collections.abc import Mapping
from typing import Any

from RPi import GPIO

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.baseclasses.aevents import AEvents
from tinyjukebox.devices.schema.input import get_schema
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, Button

__all__ = ["Input"]


class Input(ADriver, AEvents):
    """Publishes the edges of a button (internal pull-up: pressed = low) with the configured
    :class:`~tinyjukebox.messages.Button` as payload:

    * pressed: :data:`~tinyjukebox.messages.BUTTON_PRESSED`; after ``hold_timeout`` seconds
      :data:`~tinyjukebox.messages.BUTTON_HELD` - with ``hold_repeat`` again every ``hold_timeout`` seconds for as long
      as the button stays pressed
    * released: :data:`~tinyjukebox.messages.BUTTON_RELEASED`; cancels a pending hold event

    Example::

        driver:
            type: input
            pin: 21
            hold_timeout: 1
            hold_repeat: false  # optional
            button: volume-up   # volume-up, volume-down, next, prev
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
    ) -> None:
        ADriver.__init__(self, config, pubsub_client, logger)
        AEvents.__init__(self, self._config, self._logger)

        self._pin = int(self._config["pin"])
        self._hold_timeout = float(self._config["hold_timeout"])
        self._hold_repeat = bool(self._config.get("hold_repeat", False))
        self._button = Button(self._config["button"])
        self._hold_timer: threading.Timer | None = None
        self._hold_lock = threading.Lock()
        self._add_event(self._pin, self._callback_pin)
        self._add_ui_command("gpio_state", self._cmd_gpio_state)

    def _cmd_gpio_state(self, args: str) -> None:
        """gpio_state - displays if the gpio is closed or open: GPIO_STATE"""
        state = "open" if GPIO.input(self._pin) else "closed"
        print(f"[{self._name}] gpio {self._pin} is {state}")

    def _new_hold_timer(self) -> threading.Timer:
        timer = threading.Timer(self._hold_timeout, self._hold_event)
        timer.name = f"input.{self._name}.hold"
        timer.daemon = True
        timer.start()
        return timer

    def _start_hold_timer(self) -> None:
        with self._hold_lock:
            if self._hold_timer is not None:
                self._hold_timer.cancel()
            self._hold_timer = self._new_hold_timer()

    def _cancel_hold_timer(self) -> None:
        with self._hold_lock:
            if self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None

    def _hold_event(self) -> None:
        """Called by the hold timer ``hold_timeout`` seconds after the button has been pressed (``hold_repeat``:
        and after the previous hold event)."""
        if GPIO.input(self._pin):
            # the edge of the release has been lost (e.g. within the bounce time of the press) - without this check
            # a repeating button would never stop
            self._logger.info("Input._hold_event - not pressed any more.")
            self._cancel_hold_timer()
            self._publish_value(BUTTON_RELEASED, self._button)
            return
        self._logger.debug("Input._hold_event - pressed for longer than %s seconds.", self._hold_timeout)
        self._publish_value(BUTTON_HELD, self._button)
        if self._hold_repeat:
            with self._hold_lock:
                if self._hold_timer is threading.current_thread():  # neither released nor pressed again meanwhile
                    self._hold_timer = self._new_hold_timer()

    def _callback_pin(self, channel: int) -> None:
        """Edge callback of the GPIO library."""
        state = GPIO.input(self._pin)
        self._logger.info("Input._callback_pin - received event. pin state: %s.", state)
        if not state:
            self._start_hold_timer()
            self._publish_value(BUTTON_PRESSED, self._button)
        else:
            self._cancel_hold_timer()
            self._publish_value(BUTTON_RELEASED, self._button)

    def _driver_start(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._register_events()

    def _driver_stop(self) -> None:
        self._unregister_events()
        self._cancel_hold_timer()
        GPIO.cleanup(self._pin)

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
