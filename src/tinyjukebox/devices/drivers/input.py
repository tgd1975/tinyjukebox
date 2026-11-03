"""GPIO input of a button of the box."""

import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

from RPi import GPIO

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.baseclasses.aevents import AEvents
from tinyjukebox.devices.schema.input import get_schema
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, Button

__all__ = ["Input"]

_EVENTS = {BUTTON_PRESSED: "pressed", BUTTON_HELD: "held", BUTTON_RELEASED: "released"}


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
        self._holds = False  # hold events only between _driver_start and _driver_stop (guarded by _hold_lock)
        self._last_event: tuple[str, float] | None = None  # what was published last and when (time.time())
        self._presses = 0
        self._add_event(self._pin, self._callback_pin)
        self._add_ui_command("gpio_state", self._cmd_gpio_state)

    def _cmd_gpio_state(self, args: str) -> None:
        """gpio_state - displays if the gpio is closed or open: GPIO_STATE"""
        state = "open" if GPIO.input(self._pin) else "closed"
        print(f"[{self._name}] gpio {self._pin} is {state}")

    def _publish_event(self, topic: str) -> None:
        self._last_event = (_EVENTS[topic], time.time())
        if topic == BUTTON_PRESSED:
            self._presses += 1
        self._publish_value(topic, self._button)

    def _driver_state(self) -> dict[str, Any]:
        """``pressed``: the level of the pin right now (``None``: it cannot be read) - next to the last event it
        tells a button that does not arrive at the pin from one whose edges get lost."""
        pressed: bool | None = None
        if self.is_started:
            try:
                pressed = not GPIO.input(self._pin)
            except Exception as e:  # the view must not fail with the pin
                self._logger.debug("Input._driver_state - gpio %s cannot be read: %s", self._pin, e)
        event, at = self._last_event or (None, None)
        return {
            "button": self._button.value,
            "pin": self._pin,
            "pressed": pressed,
            "hold_timeout": self._hold_timeout,
            "hold_repeat": self._hold_repeat,
            "last_event": event,
            "last_event_at": at,
            "presses": self._presses,
        }

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
            # an edge callback may still run while the driver stops
            self._hold_timer = self._new_hold_timer() if self._holds else None

    def _cancel_hold_timer(self) -> None:
        with self._hold_lock:
            if self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None

    def _hold_event(self) -> None:
        """Called by the hold timer ``hold_timeout`` seconds after the button has been pressed (``hold_repeat``:
        and after the previous hold event)."""
        with self._hold_lock:
            if self._hold_timer is not threading.current_thread():  # released, pressed again or stopped meanwhile
                return
        if GPIO.input(self._pin):
            # the edge of the release has been lost (e.g. within the bounce time of the press) - without this check
            # a repeating button would never stop
            self._logger.info("Input._hold_event - not pressed any more.")
            self._cancel_hold_timer()
            self._publish_event(BUTTON_RELEASED)
            return
        self._logger.debug("Input._hold_event - pressed for longer than %s seconds.", self._hold_timeout)
        self._publish_event(BUTTON_HELD)
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
            self._publish_event(BUTTON_PRESSED)
        else:
            self._cancel_hold_timer()
            self._publish_event(BUTTON_RELEASED)

    def _driver_start(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        with self._hold_lock:
            self._holds = True
        self._register_events()

    def _driver_stop(self) -> None:
        self._unregister_events()
        with self._hold_lock:
            self._holds = False
            timer, self._hold_timer = self._hold_timer, None
        if timer is not None:
            timer.cancel()
            if timer is not threading.current_thread():
                timer.join()  # a hold event that runs right now reads the pin before it is released
        GPIO.cleanup(self._pin)

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
