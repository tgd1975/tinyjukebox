"""The open days of the advent calendars: whether the figure of a day may play.

Day n of a locked calendar opens at 00:00 on n December of its year, by the date of this box in its time zone. Once
open, a day stays open: the box keeps the highest day it opened per calendar in a small file of its own (not
synchronized). The Pi has no real-time clock; without network it starts with an older time, so it may open a day
late, never early - and a day it opened once stays open even if its clock is behind after a power cut
(docs/developer/architecture.md, section 4.2).
"""

import datetime
import json
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.target import CALENDAR_DAYS, TargetCalendar

__all__ = ["CALENDAR_DAYS_FILE", "OpenDays", "local_today"]

CALENDAR_DAYS_FILE = "calendar-days.json"
"""The file of the open days, next to the json files of the box."""


def local_today() -> datetime.date:
    """The date of the box in its time zone."""
    return datetime.datetime.now().astimezone().date()


class OpenDays:
    """``filename``: where the opened days are kept (``None``: only in memory - the library, tests)."""

    def __init__(
        self, filename: str | None, logger: logging.Logger, today: Callable[[], datetime.date] = local_today
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._filename = filename
        self._today = today
        self._lock = threading.Lock()
        self._opened: dict[str, dict[str, int]] = self._read()

    def today(self) -> datetime.date:
        return self._today()

    def is_open(self, calendar: TargetCalendar, day: int) -> bool:
        """Whether the figure of ``day`` may play now. A day that the date opens is remembered."""
        if not calendar.locked:
            return True
        reached = self._reached(calendar, self._today())
        key = str(calendar.target_id)
        with self._lock:
            known = self._opened.get(key, {})
            kept = known.get("day", 0) if known.get("year") == calendar.year else 0
            if reached > kept:
                self._opened[key] = {"year": calendar.year, "day": reached}
                self._write()
            return day <= max(reached, kept)

    @staticmethod
    def _reached(calendar: TargetCalendar, today: datetime.date) -> int:
        """The highest day the date has opened: 0 before December of the year, 24 after the 24th."""
        if today < calendar.opens(1):
            return 0
        if today >= calendar.opens(CALENDAR_DAYS):
            return CALENDAR_DAYS
        return today.day

    def _read(self) -> dict[str, dict[str, int]]:
        if self._filename is None:
            return {}
        try:
            with open(self._filename, encoding="utf-8") as f:
                data: Any = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            self._logger.warning("cannot read '%s' (%s) - the open days come from the date alone.", self._filename, e)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): {"year": value["year"], "day": value["day"]}
            for key, value in data.items()
            if isinstance(value, dict) and isinstance(value.get("year"), int) and isinstance(value.get("day"), int)
        }

    def _write(self) -> None:
        """In one step, so that a power cut leaves the old file or the new one."""
        if self._filename is None:
            return
        temp = self._filename + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(self._opened, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self._filename)
        except OSError as e:
            self._logger.warning("cannot write '%s': %s", self._filename, e)
