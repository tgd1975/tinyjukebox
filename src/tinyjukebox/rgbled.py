"""RGB led of the box - colors and blinking are executed by the rgbled driver."""

import logging
import threading
from collections.abc import Sequence
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.messages import LED, Color, LedPattern

__all__ = ["ALARM", "WAITING", "Color", "RGBLed"]

WAITING = (Color.GREEN, Color.AQUA, Color.BLUE, Color.AQUA)
"""The playback is halted until a file has arrived from the library, or until the sound of a radio station is heard:
the colors of "waiting for a tag", "starting" and "playing" flow back and forth - with every step one of the three
lights of the led changes."""

ALARM = (Color.RED, Color.WHITE, Color.MAGENTA, Color.YELLOW)
"""The library is too old for this box, which plays nothing until a person repairs that: meant to be unpleasant -
with every step two of the three lights change. Not faster than three changes per second: no strobe in a child's
room."""

SEQUENCE_INTERVAL = 0.4


class RGBLed:
    """Publishes the :class:`~tinyjukebox.messages.LedPattern` for the rgbled driver to
    :data:`~tinyjukebox.messages.LED`.

    :meth:`get_state` describes the shown pattern for the web page:

    * static color: ``{"color": "MAGENTA"}``
    * alternating colors: ``{"color_a": "MAGENTA", "color_b": "AQUA", "delay": 1}``

    An *overlay* (:meth:`show_overlay`, e.g. the search for a bluetooth headset) is shown instead of the pattern of
    the state machines until it expires or is cleared; the state machines keep setting their patterns meanwhile -
    afterwards the led shows the last one again.

    The lock keeps the order of state and published patterns for concurrent callers (state machine threads).
    After :meth:`stop` nothing changes the led any more.
    """

    def __init__(self, logger: logging.Logger, pubsub_client: PubSubClient) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._pubsub_client = pubsub_client
        self._lock = threading.Lock()
        self._base: tuple[LedPattern, dict[str, Any]] | None = None
        self._overlay: tuple[LedPattern, dict[str, Any]] | None = None
        self._overlay_timer: threading.Timer | None = None
        self._stopped = False

    def set_color(self, color: Color) -> None:
        self._set_base(*_static(color))

    def set_blink_color(self, color: Color, interval: float) -> None:
        self.set_alternate_colors(color, Color.BLACK, interval)

    def set_alternate_colors(self, color_a: Color, color_b: Color, interval: float) -> None:
        self._set_base(*_alternate(color_a, color_b, interval))

    def set_sequence(self, colors: Sequence[Color], interval: float = SEQUENCE_INTERVAL) -> None:
        """Show ``colors`` in turn, ``interval`` seconds each."""
        self._set_base(*_sequence(colors, interval))

    def show_overlay_sequence(self, colors: Sequence[Color], interval: float = SEQUENCE_INTERVAL) -> None:
        """Show ``colors`` in turn instead of the pattern of the state machines, until :meth:`clear_overlay`."""
        pattern = _sequence(colors, interval)
        with self._lock:
            if self._stopped:
                return
            self._cancel_timer()
            self._overlay = pattern
            self._publish(*pattern)

    def show_overlay(
        self, color_a: Color, color_b: Color | None = None, interval: float = 1, duration: float | None = None
    ) -> None:
        """Show ``color_a`` (alternating with ``color_b`` every ``interval`` seconds) instead of the pattern of the
        state machines - for ``duration`` seconds or until :meth:`clear_overlay` (``None``). Replaces an overlay."""
        pattern = _static(color_a) if color_b is None else _alternate(color_a, color_b, interval)
        with self._lock:
            if self._stopped:  # its timer would publish after the pubsub client has been disconnected
                return
            self._cancel_timer()
            self._overlay = pattern
            if duration is not None:
                timer = threading.Timer(duration, self._expire, args=(pattern,))
                timer.name = "RGBLed.overlay"
                timer.daemon = True
                self._overlay_timer = timer
                timer.start()
            self._publish(*pattern)

    def clear_overlay(self) -> None:
        """End the overlay - the led shows the last pattern of the state machines again."""
        with self._lock:
            self._end_overlay()

    def stop(self, color: Color) -> None:
        """The box stops: the led shows ``color`` - also instead of an overlay - and nothing changes it any more."""
        with self._lock:
            self._cancel_timer()
            self._overlay = None
            self._base = _static(color)
            if not self._stopped:
                self._stopped = True
                self._publish(*self._base)

    def get_state(self) -> dict[str, Any] | None:
        """The pattern that is shown - e.g. to show the led on the web page."""
        shown = self._overlay or self._base
        return shown[1] if shown is not None else None

    def _expire(self, pattern: tuple[LedPattern, dict[str, Any]]) -> None:
        with self._lock:
            if self._overlay is pattern:  # not replaced meanwhile
                self._overlay_timer = None
                self._end_overlay()

    def _end_overlay(self) -> None:
        self._cancel_timer()
        if self._overlay is None:
            return
        self._overlay = None
        if self._base is not None:
            self._publish(*self._base)

    def _cancel_timer(self) -> None:
        if self._overlay_timer is not None:
            self._overlay_timer.cancel()
            self._overlay_timer = None

    def _set_base(self, pattern: LedPattern, state: dict[str, Any]) -> None:
        with self._lock:
            if self._stopped:
                return
            self._base = (pattern, state)
            if self._overlay is None:
                self._publish(pattern, state)

    def _publish(self, pattern: LedPattern, state: dict[str, Any]) -> None:
        self._logger.debug("_publish - %s", state)
        self._pubsub_client.publish(LED, pattern)


def _static(color: Color) -> tuple[LedPattern, dict[str, Any]]:
    return LedPattern.static(color), {"color": color.name}


def _alternate(color_a: Color, color_b: Color, interval: float) -> tuple[LedPattern, dict[str, Any]]:
    pattern = LedPattern.blink(color_a, color_b, interval, interval)
    return pattern, {"color_a": color_a.name, "color_b": color_b.name, "delay": interval}


def _sequence(colors: Sequence[Color], interval: float) -> tuple[LedPattern, dict[str, Any]]:
    pattern = LedPattern(tuple(color.value for color in colors), (interval,) * len(colors))
    return pattern, {"colors": [color.name for color in colors], "delay": interval}
