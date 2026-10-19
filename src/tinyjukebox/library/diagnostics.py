"""What the library keeps of the logs of the boxes (docs/developer/library.md, section 4.22): the records a box
delivers, and the last startup failure of a box that cannot start.

SQLite of its own, apart from the journal: a listening record and a technical log are different things, and this one
is thrown away by age and by number. A record is taken once - a box that sends a batch again after a lost answer
changes nothing.
"""

import datetime
import json
import sqlite3
import threading
from typing import Any

from tinyjukebox.sync.util import timestamp

__all__ = ["DAYS", "PER_BOX", "Diagnostics"]

DAYS = 30
PER_BOX = 20000
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
COLUMNS = ("box", "boot", "seq", "received", "time", "uptime", "level", "component", "code", "message", "version")
INSERT = "INSERT OR IGNORE INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
FILTERS = ("box", "level", "component", "version", "code", "since", "until", "text")


class Diagnostics:
    def __init__(self, filename: str, days: int = DAYS, per_box: int = PER_BOX) -> None:
        self._days = days
        self._per_box = per_box
        self._lock = threading.Lock()
        self._db = sqlite3.connect(filename, check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS records (box TEXT, boot INTEGER, seq INTEGER, received TEXT, time TEXT, "
                "uptime REAL, level TEXT, component TEXT, code TEXT, message TEXT, version TEXT, data TEXT, "
                "PRIMARY KEY (box, boot, seq))"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS records_time ON records (box, time)")
            self._db.execute("CREATE TABLE IF NOT EXISTS startup (box TEXT PRIMARY KEY, received TEXT, report TEXT)")
        self._added = 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- what a box delivers -----------------------------------------------------------------------------------------

    def add(self, box: str, envelope: dict[str, Any]) -> int:
        """Records of one start of a box; returns how many were new."""
        boot, version = int(envelope.get("boot") or 0), str(envelope.get("version") or "")
        received, new = timestamp(), 0
        rows = []
        for record in envelope.get("records") or []:
            if not isinstance(record, dict) or not isinstance(record.get("seq"), int):
                continue
            extra = {key: value for key, value in record.items() if key not in COLUMNS}
            rows.append((
                box, boot, record["seq"], received, str(record.get("time") or received),
                float(record.get("uptime") or 0), str(record.get("level") or "INFO"),
                str(record.get("component") or ""), str(record.get("code") or "")[:200],
                str(record.get("message") or "")[:4000], version, json.dumps(extra),
            ))  # fmt: skip
        dropped = {str(level): int(count) for level, count in (envelope.get("dropped") or {}).items() if count}
        if dropped:  # the box had to throw records away: the library says how many
            rows.append((
                box, boot, _own_number(), received, received, 0.0,
                "WARNING", "diagnostics", "diagnostics.dropped",
                "the box dropped records it could not deliver: "
                + ", ".join(f"{count} {level}" for level, count in sorted(dropped.items())),
                version, json.dumps({"params": dropped}),
            ))  # fmt: skip
        with self._lock, self._db:
            for row in rows:
                new += self._db.execute(INSERT, row).rowcount
            self._added += new
            if self._added > 500:  # now and then, not with every batch
                self._added = 0
                self._prune()
        return new

    def _prune(self) -> None:
        """Called with the lock held: the records older than the days, and per box beyond the number."""
        limit = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=self._days)
        self._db.execute("DELETE FROM records WHERE time < ?", (limit.strftime("%Y-%m-%dT%H:%M:%SZ"),))
        for (box,) in self._db.execute("SELECT DISTINCT box FROM records").fetchall():
            self._db.execute(
                "DELETE FROM records WHERE box = ? AND rowid NOT IN "
                "(SELECT rowid FROM records WHERE box = ? ORDER BY time DESC, seq DESC LIMIT ?)",
                (box, box, self._per_box),
            )

    def prune(self) -> None:
        with self._lock, self._db:
            self._prune()

    # --- a box that cannot start -------------------------------------------------------------------------------------

    def startup(self, box: str, report: dict[str, Any]) -> None:
        """The last startup failure of a box replaces the one before: a restart loop does not fill the library."""
        clean: dict[str, Any] = {
            key: str(report[key])[:limit]
            for key, limit in (("version", 50), ("slot", 20), ("since", 40), ("error", 8000), ("recovery", 2000))
            if report.get(key) not in (None, "")
        }
        clean["failures"] = int(report.get("failures") or 0)
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO startup VALUES (?, ?, ?)", (box, timestamp(), json.dumps(clean)))

    def started(self, box: str) -> None:
        """The application of the box runs again: its last startup failure becomes a record of its history."""
        with self._lock, self._db:
            row = self._db.execute("SELECT received, report FROM startup WHERE box = ?", (box,)).fetchone()
            if row is None:
                return
            received, report = row[0], json.loads(row[1])
            self._db.execute("DELETE FROM startup WHERE box = ?", (box,))
            message = (
                f"the box could not start ({report.get('failures', 0)} failed starts since {report.get('since', '?')})"
                + (f": {report['error'].strip().splitlines()[-1]}" if report.get("error", "").strip() else "")
            )
            self._db.execute(
                INSERT,
                (box, 0, _own_number(), received, received, 0.0, "ERROR", "start", "startup.failed", message[:4000],
                 report.get("version", ""), json.dumps({"traceback": report.get("error", ""), "params": report})),
            )  # fmt: skip

    def startups(self) -> dict[str, dict[str, Any]]:
        """The boxes that cannot start, with their last report."""
        with self._lock:
            rows = self._db.execute("SELECT box, received, report FROM startup").fetchall()
        return {box: {**json.loads(report), "received": received} for box, received, report in rows}

    # --- for the page ------------------------------------------------------------------------------------------------

    def query(self, limit: int = 1000, **filters: str) -> list[dict[str, Any]]:
        """The newest records that match, oldest first. ``level``: at least this level; ``text``: in the message, the
        code or the component; ``since``/``until``: times as the records carry them."""
        where, values = ["1"], []
        for key in ("box", "component", "version", "code"):
            if filters.get(key):
                where.append(f"{key} = ?")
                values.append(filters[key])
        if filters.get("level") in LEVELS:
            levels = LEVELS[LEVELS.index(filters["level"]) :]
            where.append(f"level IN ({', '.join('?' * len(levels))})")
            values.extend(levels)
        if filters.get("since"):
            where.append("time >= ?")
            values.append(filters["since"])
        if filters.get("until"):
            where.append("time <= ?")
            values.append(filters["until"])
        if filters.get("text"):
            where.append("(message LIKE ? OR code LIKE ? OR component LIKE ?)")
            values.extend([f"%{filters['text']}%"] * 3)
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(COLUMNS)}, data FROM records WHERE {' AND '.join(where)} "  # noqa: S608 - fixed names
                "ORDER BY time DESC, boot DESC, seq DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [{**dict(zip(COLUMNS, row[:-1], strict=True)), **json.loads(row[-1])} for row in reversed(rows)]

    def choices(self, box: str | None = None) -> dict[str, list[str]]:
        """What the filters of the page offer: the components, versions and codes there are."""
        result = {}
        with self._lock:
            for key in ("component", "version", "code"):
                rows = self._db.execute(
                    f"SELECT DISTINCT {key} FROM records WHERE (? IS NULL OR box = ?) ORDER BY {key}",  # noqa: S608
                    (box, box),
                ).fetchall()
                result[key] = [row[0] for row in rows if row[0]]
        return result


def _own_number() -> int:
    """The number of a record the library writes itself: negative, so that it never meets one of a box."""
    return -int(datetime.datetime.now(datetime.UTC).timestamp() * 1_000_000)
