"""Latency of the reaction chains of the box (tag read → playback, button pressed → volume set)."""

import logging
import threading
import time

from tinyjukebox.core.logging import get_child

__all__ = ["ReactionTimer"]


class ReactionTimer:
    """Measures one reaction chain and logs one info line per step with the time since its trigger.

    :meth:`begin` starts a measurement (a pending one is dropped), :meth:`step` logs an intermediate step and
    :meth:`end` logs the last step and finishes the measurement. Steps without a pending measurement are ignored -
    e.g. the start of the next track is no reaction to a tag.
    """

    def __init__(self, name: str, logger: logging.Logger) -> None:
        self._name = name
        self._logger = get_child(logger, "latency")
        self._lock = threading.Lock()
        self._started: float | None = None

    def begin(self, started: float | None = None) -> None:
        """Start a measurement at ``started`` (``time.monotonic()``; default: now)."""
        with self._lock:
            self._started = time.monotonic() if started is None else started

    def step(self, what: str) -> None:
        self._log(what, finish=False)

    def end(self, what: str) -> None:
        self._log(what, finish=True)

    def _log(self, what: str, finish: bool) -> None:
        with self._lock:
            started = self._started
            if finish:
                self._started = None
        if started is not None:
            self._logger.info("%s: %s after %.0f ms", self._name, what, (time.monotonic() - started) * 1000)
