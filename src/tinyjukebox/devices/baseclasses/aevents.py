"""Mixin for drivers that react to edges on GPIO pins."""

import logging
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from RPi import GPIO

__all__ = ["AEvents", "EdgeCallback"]

EdgeCallback = Callable[[int], None]


class _Event(NamedTuple):
    callback: EdgeCallback
    bounce_time: int


class AEvents:
    """Mixin for drivers that must react to changing pin states (``GPIO.setmode(GPIO.BCM)``).

    Events are registered for both edges. The callback receives the channel and has to read the pin state itself to
    distinguish rising and falling edges. Callbacks run in the callback thread of the GPIO library.
    """

    def __init__(self, config: Mapping[str, Any], logger: logging.Logger) -> None:
        self._events: dict[int, _Event] = {}
        self._logger_aevents = logger
        GPIO.setmode(GPIO.BCM)

    def _add_event(self, pin: int, func: EdgeCallback, bounce_time: int = 50) -> None:
        """Remember an edge callback for ``pin``; it becomes active with :meth:`_register_events`."""
        self._logger_aevents.info("AEvents._add_event - pin:%s, func:%s, bounce:%s.", pin, func, bounce_time)
        self._events[int(pin)] = _Event(func, bounce_time)

    def _register_events(self) -> None:
        """Activate all edge callbacks - usually in ``_driver_start``."""
        GPIO.setmode(GPIO.BCM)
        for pin, event in self._events.items():
            self._logger_aevents.debug("AEvents._register_events - pin:%s, bounce_time:%s", pin, event.bounce_time)
            GPIO.add_event_detect(pin, GPIO.BOTH, callback=event.callback, bouncetime=event.bounce_time)

    def _unregister_events(self) -> None:
        """Deactivate all edge callbacks - usually in ``_driver_stop``."""
        for pin in self._events:
            self._logger_aevents.debug("AEvents._unregister_events - pin:%s", pin)
            GPIO.remove_event_detect(pin)
