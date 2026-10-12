"""Keeps the latest log records in memory - shown on the web page."""

import collections
import itertools
import logging
import threading
from typing import Any

__all__ = ["LEVELS", "LogBuffer"]

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogBuffer(logging.Handler):
    """Log handler with the newest ``capacity`` records; every record gets an increasing id."""

    def __init__(self, capacity: int = 5000) -> None:
        super().__init__(logging.DEBUG)
        self._records: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self._counter = itertools.count(1)
        self._lock_records = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # a broken log call must not break the logging
            message = str(record.msg)
        with self._lock_records:
            self._records.append(
                {
                    "id": next(self._counter),
                    "time": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "thread": record.threadName,
                    "message": message,
                }
            )

    @property
    def last_id(self) -> int:
        """Id of the newest record (0: none yet)."""
        with self._lock_records:
            return self._records[-1]["id"] if self._records else 0

    def get(self, after: int = 0, level: str = "DEBUG", text: str | None = None, limit: int = 500) -> dict[str, Any]:
        """Records with id > after, at least ``level``, containing ``text`` (case insensitive) - the newest
        ``limit``."""
        minimum = LEVELS.index(level) if level in LEVELS else 0
        needle = text.lower() if text else None
        with self._lock_records:
            records = list(self._records)
        result = [
            r
            for r in records
            if r["id"] > after
            and (r["level"] not in LEVELS or LEVELS.index(r["level"]) >= minimum)
            and (needle is None or needle in r["message"].lower() or needle in r["logger"].lower())
        ]
        return {"entries": result[-limit:], "last_id": records[-1]["id"] if records else after}
