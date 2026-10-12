"""Time source of the players - replaced by a manually advanced clock in tests."""

import threading
import time
from collections.abc import Callable
from typing import Protocol

__all__ = ["SYSTEM_CLOCK", "Clock", "SystemClock", "TimerHandle"]


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic time in seconds."""
        ...

    def call_later(self, delay: float, callback: Callable[[], object]) -> TimerHandle:
        """Call ``callback`` (in another thread) after ``delay`` seconds unless the handle is cancelled before."""
        ...


class SystemClock:
    """Real time: ``time.monotonic`` and ``threading.Timer`` (daemon threads)."""

    def now(self) -> float:
        return time.monotonic()

    def call_later(self, delay: float, callback: Callable[[], object]) -> TimerHandle:
        timer = threading.Timer(max(0.0, delay), callback)
        timer.name = "tinyjukebox.Timer"
        timer.daemon = True
        timer.start()
        return timer


SYSTEM_CLOCK = SystemClock()
