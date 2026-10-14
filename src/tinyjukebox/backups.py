"""Backups that the library or a box without a library makes by itself and keeps in a folder
(docs/developer/operations.md, section 10.2).

There are two schedules that are set apart from each other: one for the backups without the albums (``plain``) and one
for those with them (``albums``). Each says how often and at what time of day a backup is written, how many backups
of its kind are kept, and into which folder they go; until a folder is set it is the one of the config. A backup can
also be asked for at once. What a backup contains is described in :mod:`tinyjukebox.backup`; this module only says
when and where, and looks after the folders.
"""

import calendar
import contextlib
import datetime
import logging
import os
import re
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.backup import NAME, PART, Backup
from tinyjukebox.core.logging import get_child
from tinyjukebox.errors import BadRequest, Conflict, NotFound
from tinyjukebox.sync.util import read_json, timestamp, write_json

__all__ = ["DEFAULTS", "KINDS", "UNITS", "Backups", "next_due"]

KINDS = ("plain", "albums")
"""The two schedules: backups without the albums and backups with them. In this order they are made if both are due
at the same time."""
UNITS = ("days", "weeks", "months")
DEFAULTS: dict[str, dict[str, Any]] = {
    "plain": {"enabled": False, "every": 1, "unit": "days", "time": "03:00", "keep": 7},
    "albums": {"enabled": False, "every": 1, "unit": "months", "time": "03:00", "keep": 2},
}
_KEPT = {"folder": None, "last": None, "scheduled": None}  # what a schedule keeps beside its settings


def _time_of_day(value: object) -> datetime.time:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise BadRequest("time must be of the form HH:MM.")
    return datetime.time(int(value[:2]), int(value[3:]))


def _later(day: datetime.date, every: int, unit: str) -> datetime.date:
    """``every`` days, weeks or months after ``day``. Months: the same day of the month; a day that the month does
    not have is its last day."""
    if unit != "months":
        return day + datetime.timedelta(days=every * (7 if unit == "weeks" else 1))
    months = day.year * 12 + day.month - 1 + every
    year, month = divmod(months, 12)
    return datetime.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def next_due(now: datetime.datetime, settings: dict[str, Any], last: datetime.date | None) -> datetime.datetime:
    """When the next backup of a schedule is to be made: ``every`` days, weeks or months (``unit``) after the day of
    the last one (``last``), at ``time`` - or today, if there was none. A time that has passed, e.g. because the
    program did not run, moves to the same time of the next day: a backup is not made up for in the middle of the
    day."""
    at = _time_of_day(settings["time"])
    day = now.date() if last is None else _later(last, int(settings["every"]), str(settings.get("unit", "days")))
    due = datetime.datetime.combine(max(day, now.date()), at)
    return due if due > now else due + datetime.timedelta(days=1)


def _loaded(stored: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The schedules as the settings file has them. Until 0.33 there was one schedule with a tick "with the albums":
    it becomes the schedule of its kind, and the other one is off with its defaults."""
    if stored and not any(isinstance(stored.get(kind), dict) for kind in KINDS):
        kind = "albums" if stored.get("albums") else "plain"
        stored = {kind: {**{key: value for key, value in stored.items() if key != "albums"}, "unit": "days"}}
    state = {}
    for kind in KINDS:
        known = {**DEFAULTS[kind], **_KEPT}
        state[kind] = {**known, **{key: value for key, value in stored.get(kind, {}).items() if key in known}}
    return state


class Backups:
    def __init__(
        self,
        folder: str,
        settings_file: str,
        create: Callable[[bool], Backup],
        logger: logging.Logger,
        kind: str = "library",
        active: Callable[[], bool] = lambda: True,
    ) -> None:
        """``folder``: where the backups are kept unless a schedule names another folder; ``settings_file``: the
        schedules with their folders and what their last backups were; ``create``: takes the data for a backup, with
        the albums or without; ``kind``: whose backups these are, ``library`` or ``box`` - only files with that name
        are listed, sent or removed; ``active``: while it says no, nothing is made - a box that works with a library
        leaves the backups to it and keeps its schedules for the day it is unlinked."""
        self._default_folder = folder
        self._settings_file = settings_file
        self._create = create
        self._kind = kind
        self._active = active
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.Lock()
        self._state = _loaded(read_json(settings_file, {}))
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._asked: bool | None = None  # a backup asked for at once: with the albums or without
        self._running = False
        self._thread: threading.Thread | None = None

    # --- what the page shows and sets -------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active()

    def folder(self, kind: str = "plain") -> str:
        """Where the backups of a schedule are kept: the folder that was set, else the one of the config."""
        return str(self._state[kind]["folder"] or self._default_folder)

    def _checked_folder(self, value: object) -> str | None:
        """The folder to set: None for the one of the config, else an absolute path that the program can write to -
        it is created if it is missing."""
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not os.path.isabs(os.path.expanduser(value)):
            raise BadRequest(
                "folder must be an absolute path, e.g. /volume1/tinyjukebox/backups.", code="backup.folder_absolute"
            )
        folder = os.path.normpath(os.path.expanduser(value))
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            raise BadRequest(
                f"the folder {folder} cannot be created: {error.strerror or error}",
                code="backup.folder_create",
                folder=folder,
            ) from None
        if not os.access(folder, os.W_OK | os.X_OK):
            raise BadRequest(f"the folder {folder} cannot be written to.", code="backup.folder_write", folder=folder)
        return None if folder == os.path.normpath(self._default_folder) else folder

    def _files_in(self, folder: str) -> list[dict[str, Any]]:
        """The backups in ``folder``, newest first. Only files with the name of a backup of this program count."""
        found = []
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            for name in sorted(os.listdir(folder), reverse=True):
                match = NAME.fullmatch(name)
                if match is None or match.group(1) != self._kind:
                    continue
                day, clock = match.group(2), match.group(3)
                found.append(
                    {
                        "name": name,
                        "when": f"{day[:4]}-{day[4:6]}-{day[6:]} {clock[:2]}:{clock[2:4]}",
                        "size": os.path.getsize(os.path.join(folder, name)),
                        "albums": match.group(4).startswith("-albums"),
                        "folder": folder,
                    }
                )
        return found

    def _files(self) -> list[dict[str, Any]]:
        """The backups of both folders in one list, newest first. The name says when a backup was made and of which
        kind it is."""
        found: dict[str, dict[str, Any]] = {}
        for folder in dict.fromkeys(self.folder(kind) for kind in KINDS):
            for entry in self._files_in(folder):
                found.setdefault(entry["name"], entry)
        return sorted(found.values(), key=lambda entry: (entry["when"], entry["name"]), reverse=True)

    def status(self) -> dict[str, Any]:
        """Per schedule (``plain``, ``albums``): its settings, its folder and the one of the config
        (``default_folder``), its last backup (``when``, ``ok``, ``message``) and when the next one is due (local
        time, None without a schedule). Then whether a backup is being made, whether backups are made here at all
        (``active``), and the backups of both folders, newest first."""
        with self._lock:
            state = {kind: dict(self._state[kind]) for kind in KINDS}
            running = self._running or self._asked is not None
        active = self.active
        result: dict[str, Any] = {}
        for kind in KINDS:
            due = self._due(state[kind]) if active and state[kind]["enabled"] else None
            result[kind] = {
                **{key: state[kind][key] for key in DEFAULTS[kind]},
                "folder": self.folder(kind),
                "default_folder": self._default_folder,
                "last": state[kind]["last"],
                "next": due.strftime("%Y-%m-%d %H:%M") if due else None,
            }
        result["running"] = running
        result["active"] = active
        result["backups"] = [{key: value for key, value in entry.items() if key != "folder"} for entry in self._files()]
        return result

    def _checked(self, kind: str, data: object) -> dict[str, Any]:
        """The settings of a schedule with what ``data`` changes; fields that are left out stay."""
        if not isinstance(data, dict):
            raise BadRequest(f"{kind} must be the settings of a schedule.")
        new = dict(self._state[kind])
        new.update({key: data[key] for key in DEFAULTS[kind] if key in data})
        if "folder" in data:
            new["folder"] = self._checked_folder(data["folder"])
        _time_of_day(new["time"])
        if not isinstance(new["enabled"], bool):
            raise BadRequest("enabled must be true or false.")
        if new["unit"] not in UNITS:
            raise BadRequest(f"unit must be one of {', '.join(UNITS)}.")
        for key, most in (("every", 365), ("keep", 999)):
            if not isinstance(new[key], int) or isinstance(new[key], bool) or not 1 <= new[key] <= most:
                raise BadRequest(f"{key} must be a number from 1 to {most}.")
        return new

    def set(self, data: dict[str, Any]) -> dict[str, Any]:
        """Change the schedules: ``{"plain": {…}, "albums": {…}}``; a schedule and fields that are left out stay. An
        empty ``folder`` is the one of the config again. Backups that lie in a folder before stay there; the page
        lists those of the folders that count."""
        if not isinstance(data, dict) or not all(kind in KINDS for kind in data):
            raise BadRequest(f"the schedules are {' and '.join(KINDS)}.")
        new = {kind: self._checked(kind, data[kind]) for kind in data}
        with self._lock:
            moved = any(new[kind]["folder"] != self._state[kind]["folder"] for kind in new)
            if moved and (self._running or self._asked is not None):
                raise Conflict("a backup is being made: the folder cannot be changed now.", code="backup.busy_folder")
            for kind, settings in new.items():
                # what the thread noted about the last backup meanwhile stays
                self._state[kind].update({key: settings[key] for key in (*DEFAULTS[kind], "folder")})
            self._save()
        self._wake.set()
        return self.status()

    def ask(self, albums: bool) -> None:
        """Make a backup now, in the background. It goes into the folder of its kind and counts for its number.

        :raises Conflict: while a backup is being made, and where none are made.
        """
        if not self.active:
            raise Conflict("this box works with a library, which makes the backups.", code="backup.library")
        with self._lock:
            if self._running or self._asked is not None:
                raise Conflict("a backup is being made.", code="backup.busy")
            if self._thread is None:
                raise Conflict("the program is stopping.")
            self._asked = albums
        self._wake.set()

    def wake(self) -> None:
        """Look again at what is due, e.g. because backups are made here from now on."""
        self._wake.set()

    def path(self, name: str) -> str:
        """The file of a backup in one of the folders. Only names of the listing are ever opened."""
        for entry in self._files():
            if entry["name"] == name:
                return os.path.join(entry["folder"], name)
        raise NotFound(f"backup '{name}' does not exist.", code="backup.unknown")

    def remove(self, name: str) -> None:
        os.remove(self.path(name))
        self._logger.info("backup %s removed", name)

    # --- making them ------------------------------------------------------------------------------------------------

    def _save(self) -> None:
        write_json(self._settings_file, self._state)

    def _due(self, settings: dict[str, Any]) -> datetime.datetime:
        last = datetime.date.fromisoformat(settings["scheduled"]) if settings["scheduled"] else None
        return next_due(datetime.datetime.now(), settings, last)

    def make(self, albums: bool) -> str:
        """Write a backup into the folder of its kind and remove the oldest ones of that kind beyond ``keep``;
        returns its name. A backup that fails leaves nothing in the folder."""
        kind = "albums" if albums else "plain"
        folder = self.folder(kind)
        os.makedirs(folder, exist_ok=True)
        backup = self._create(albums)
        _, result = backup.save(folder)
        for name in result["skipped"]:
            self._logger.warning("backup %s: %s disappeared while it was read", backup.filename, name)
        self._logger.info("backup %s: %d files, %d bytes", backup.filename, result["files"], result["bytes"])
        with self._lock:
            keep = int(self._state[kind]["keep"])
        # only the backups of this kind count, also when both kinds are kept in one folder
        for entry in [entry for entry in self._files_in(folder) if entry["albums"] == albums][keep:]:
            os.remove(os.path.join(folder, entry["name"]))
            self._logger.info("backup %s removed: %d are kept", entry["name"], keep)
        return backup.filename

    def _make(self, kind: str, scheduled: bool) -> None:
        with self._lock:
            self._running = True
        try:
            last = {"when": timestamp(), "ok": True, "message": self.make(kind == "albums")}
        except Exception as error:
            self._logger.exception("the backup failed")
            last = {"when": timestamp(), "ok": False, "message": str(error) or type(error).__name__}
        with self._lock:
            self._running = False
            self._state[kind]["last"] = last
            if scheduled:  # also one that failed: the next try is the next day that is due, not at once again
                self._state[kind]["scheduled"] = datetime.date.today().isoformat()
            self._save()

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.clear()  # before the look at what is asked for: what is asked after it wakes the wait below
            with self._lock:
                state = {kind: dict(self._state[kind]) for kind in KINDS}
                asked, self._asked = self._asked, None
            if asked is not None:
                self._make("albums" if asked else "plain", scheduled=False)
                continue
            active = self.active
            due = {kind: self._due(state[kind]) for kind in KINDS if active and state[kind]["enabled"]}
            wait = 3600.0  # looks at the clock again at least every hour: it may have been set
            if due:
                wait = min(wait, (min(due.values()) - datetime.datetime.now()).total_seconds())
            if self._wake.wait(max(wait, 0.0)):
                continue  # asked for a backup, a new schedule, or the end
            # what was due before the first one was made: a time that passes while it is written is not put off
            for kind in KINDS:
                if kind in due and datetime.datetime.now() >= due[kind] and not self._stopping.is_set():
                    self._make(kind, scheduled=True)

    def start(self) -> None:
        self._stopping.clear()
        for folder in dict.fromkeys(self.folder(kind) for kind in KINDS):
            with contextlib.suppress(FileNotFoundError, NotADirectoryError):
                for name in os.listdir(folder):
                    if name.endswith(PART):  # what a program left that was stopped in the middle of a backup
                        os.remove(os.path.join(folder, name))
        with self._lock:
            self._thread = threading.Thread(target=self._run, name="backups", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        with self._lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)
