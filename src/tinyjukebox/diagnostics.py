"""Central diagnostics (docs/developer/library.md, section 4.22): what the box logs that is worth a look from the
library - warnings and errors, and the structured events that a component marks with :func:`diagnostic`.

The records are collected by a log handler that never waits: it redacts the record, puts it into a bounded buffer and
returns. The synchronization takes them from there in its own thread and delivers them to the library (``take`` and
``delivered``); without a connection they wait. Under pressure the buffer drops INFO before WARNING before ERROR and
counts what it dropped. The log file of the box stays the full record; the library gets what helps to find a fault.

Nothing that identifies a person or opens a door leaves the box: :func:`redact` removes passwords, keys, tokens,
certificates, the query of addresses, tag UIDs and the ids of Spotify.
"""

import collections
import logging
import re
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from typing import Any

from tinyjukebox.sync.util import timestamp

__all__ = ["CAPACITY", "Collector", "diagnostic", "redact"]

CAPACITY = 2000  # records a box keeps for the library at most
MAX_MESSAGE = 2000  # characters of a message
MAX_TRACEBACK = 8000
STARTED = time.monotonic()

_REDACTIONS = [
    # certificates and keys
    (re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S), "<pem>"),
    # Authorization: Box id:secret, Bearer ..., token ...
    (
        re.compile(r"(?i)\b(authorization|bearer|token|box)\b(\s*[:=]?\s*)(\S+:)?[A-Za-z0-9_\-.~+/]{16,}=*"),
        r"\1\2<secret>",
    ),
    # password=..., "secret": "...", key: ...
    (
        re.compile(
            r"""(?i)(["']?\b(?:password|passwd|secret|key|token|bootstrap|client_secret)["']?\s*[:=]\s*)(["']?)"""
            r"""[^\s"',;}]+"""
        ),
        r"\1\2<secret>",
    ),
    # the query of an address may carry a token or who listens
    (re.compile(r"(https?://[^\s?#'\"]+)\?[^\s'\"]*"), r"\1?<query>"),
    # user:password@ in an address
    (re.compile(r"(https?://)[^\s/@'\"]+@"), r"\1<user>@"),
    # ids of Spotify (the kind stays)
    (re.compile(r"\bspotify:(album|playlist|track|episode|show|audiobook|artist):[A-Za-z0-9]+"), r"spotify:\1:<id>"),
    (re.compile(r"(open\.spotify\.com/(?:[a-z]+/)?)[A-Za-z0-9]{10,}"), r"\1<id>"),
    # UIDs of tags: hex bytes with colons
    (re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){3,9}\b"), "<uid>"),
]


def redact(text: str) -> str:
    """``text`` without credentials, certificate material, queries of addresses, tag UIDs and ids of Spotify."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def diagnostic(code: str, text: str | None = None, **params: Any) -> dict[str, Any]:
    """``extra`` for a log call that is a structured event: a stable code and safe parameters, e.g.
    ``logger.info("radio started in %d ms", ms, extra=diagnostic("radio.startup", resolve_ms=12))``. The library gets
    such a record whatever its level - with ``text`` instead of the message of the log file, if that says more than
    the library should know (e.g. the whole address of a stream)."""
    return {"diagnostic": {"code": code, "params": params, "text": text}}


_PRIORITY = {"DEBUG": 0, "INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 2}


class Collector(logging.Handler):
    """Collects records for the library. ``level``: from which level an ordinary record is collected; ``urgent()`` is
    called for an error (it must not block - e.g. it wakes the synchronization)."""

    def __init__(
        self,
        level: int = logging.WARNING,
        capacity: int = CAPACITY,
        urgent: Callable[[], None] | None = None,
        root: str = "",
    ) -> None:
        super().__init__(logging.DEBUG)
        self._level = level
        self._capacity = capacity
        self.urgent = urgent
        self._root = root + "." if root else ""
        self._lock = threading.Lock()
        self._records: collections.deque[dict[str, Any]] = collections.deque()
        self._number = 0
        self._taken = 0  # the highest number handed out by take(): such a record is not changed any more
        self.dropped: dict[str, int] = collections.defaultdict(int)  # by level, since the last delivery

    def emit(self, record: logging.LogRecord) -> None:
        marked = getattr(record, "diagnostic", None)
        if record.levelno < self._level and marked is None:
            return
        try:
            entry = self._entry(record, marked)
        except Exception:  # a broken log call must not break the logging - nor the box
            return
        with self._lock:
            last = self._records[-1] if self._records else None
            if (
                last is not None
                and last["seq"] > self._taken
                and (last["code"], last["message"], last["level"]) == (entry["code"], entry["message"], entry["level"])
            ):
                last["repeated"] = last.get("repeated", 1) + 1  # a fault that repeats is one record
                last["last"] = entry["time"]
            else:
                self._number += 1
                self._records.append({"seq": self._number, **entry})
                if len(self._records) > self._capacity:
                    self._drop()
        if record.levelno >= logging.ERROR and self.urgent is not None:
            self.urgent()

    def _drop(self) -> None:
        """The oldest record of the lowest priority goes; what is dropped is counted."""
        lowest = min(_PRIORITY.get(entry["level"], 0) for entry in self._records)
        for index, entry in enumerate(self._records):
            if _PRIORITY.get(entry["level"], 0) == lowest:
                del self._records[index]
                self.dropped[entry["level"]] += entry.get("repeated", 1)
                return

    def _entry(self, record: logging.LogRecord, marked: Mapping[str, Any] | None) -> dict[str, Any]:
        component = record.name[len(self._root) :] if record.name.startswith(self._root) else record.name
        message = redact(marked["text"] if marked and marked.get("text") else record.getMessage())[:MAX_MESSAGE]
        entry: dict[str, Any] = {
            "time": timestamp(), "uptime": round(record.created - time.time() + time.monotonic() - STARTED, 1),
            "level": record.levelname, "component": component,
            # a stable code: the one of a structured event, else the text of the call before its values
            "code": marked["code"] if marked else redact(str(record.msg))[:200],
            "message": message,
        }  # fmt: skip
        if marked and marked.get("params"):
            entry["params"] = {key: _safe(value) for key, value in marked["params"].items()}
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = type(record.exc_info[1]).__name__
            entry["traceback"] = redact("".join(traceback.format_exception(*record.exc_info)))[-MAX_TRACEBACK:]
        return entry

    def take(self, limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """The oldest records that wait, and what was dropped - they stay until :meth:`delivered`."""
        with self._lock:
            taken = [dict(entry) for entry in list(self._records)[:limit]]
            if taken:
                self._taken = max(self._taken, taken[-1]["seq"])
            return taken, dict(self.dropped)

    def delivered(self, last: int, dropped: Mapping[str, int]) -> None:
        """The library has the records up to number ``last`` and knows of what was dropped until then."""
        with self._lock:
            while self._records and self._records[0]["seq"] <= last:
                self._records.popleft()
            for level, count in dropped.items():
                self.dropped[level] -= count
                if self.dropped[level] <= 0:
                    del self.dropped[level]

    def waiting(self) -> int:
        with self._lock:
            return len(self._records)


def _safe(value: Any) -> Any:
    if isinstance(value, bool | int | float) or value is None:
        return value
    return redact(str(value))[:500]
