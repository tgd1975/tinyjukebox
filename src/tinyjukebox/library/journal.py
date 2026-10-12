"""The journal of the library (docs/developer/library.md, section 7): what the boxes delivered, and what
the library noted itself.

SQLite, part of Python. A line of a box is taken exactly once and never changed; it can only be flagged as discarded.
"""

import json
import os
import sqlite3
import tempfile
import threading
from typing import Any

from tinyjukebox.sync.util import timestamp

__all__ = ["Journal"]


class Journal:
    def __init__(self, filename: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(filename, check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS lines (box TEXT, n INTEGER, received TEXT, version TEXT, line TEXT, "
                "discarded INTEGER DEFAULT 0, PRIMARY KEY (box, n))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, time TEXT, kind TEXT, box TEXT, data TEXT)"
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, box: str, confirmed: int, lines: list[dict[str, Any]]) -> int:
        """Take the lines that follow the number ``confirmed`` without a gap; return the number confirmed now."""
        with self._lock, self._db:
            for line in sorted(lines, key=lambda line: int(line["n"])):
                if line["n"] <= confirmed:
                    continue  # delivered before: the confirmation got lost
                if line["n"] != confirmed + 1:
                    break
                self._db.execute(
                    "INSERT OR IGNORE INTO lines (box, n, received, version, line) VALUES (?, ?, ?, ?, ?)",
                    (box, line["n"], timestamp(), line.get("version"), json.dumps(line)),
                )
                confirmed = line["n"]
        return confirmed

    def snapshot(self) -> bytes:
        """The database as a file of its own, for a backup: a copy made by SQLite, never the file that is written."""
        with self._lock, tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, "journal.sqlite")
            copy = sqlite3.connect(filename)
            try:
                self._db.backup(copy)
            finally:
                copy.close()
            with open(filename, "rb") as infile:
                return infile.read()

    def discard(self, box: str, version: str) -> int:
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE lines SET discarded = 1 WHERE box = ? AND version = ?", (box, version)
            ).rowcount

    def lines(self, box: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT box, discarded, line FROM lines WHERE (? IS NULL OR box = ?) ORDER BY box, n", (box, box)
            ).fetchall()
        return [{**json.loads(line), "box": row_box, "discarded": bool(discarded)} for row_box, discarded, line in rows]

    def record(self, kind: str, box: str | None, data: dict[str, Any]) -> None:
        """Something the library notes itself: a conflict, a rejected change, a switch of version of a box."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO records (time, kind, box, data) VALUES (?, ?, ?, ?)",
                (timestamp(), kind, box, json.dumps(data)),
            )

    def records(self, kind: str | None = None, box: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT time, kind, box, data FROM records WHERE (? IS NULL OR kind = ?) AND (? IS NULL OR box = ?) "
                "ORDER BY id",
                (kind, kind, box, box),
            ).fetchall()
        return [
            {"time": time, "kind": row_kind, "box": row_box, **json.loads(data)}
            for time, row_kind, row_box, data in rows
        ]
