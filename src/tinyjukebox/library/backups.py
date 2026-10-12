"""Backups that the library makes by itself and keeps in a folder (docs/developer/library.md, section 4.19).

Every ``every`` days at ``time`` a backup is written into the folder, with the albums or without, and the oldest ones
beyond ``keep`` are removed. A backup can also be asked for at once. The folder is set like the schedule; until then
it is the one of the config. What a backup contains is described in :mod:`tinyjukebox.backup`; this module only says
when and where, and looks after the folder.
"""

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

__all__ = ["DEFAULTS", "Backups", "next_due"]

DEFAULTS: dict[str, Any] = {"enabled": False, "time": "03:00", "every": 1, "albums": False, "keep": 7}


def _time_of_day(value: object) -> datetime.time:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise BadRequest("time must be of the form HH:MM.")
    return datetime.time(int(value[:2]), int(value[3:]))


def next_due(now: datetime.datetime, settings: dict[str, Any], last: datetime.date | None) -> datetime.datetime:
    """When the next backup is to be made: ``every`` days after the day of the last one (``last``), at ``time`` - or
    today, if there was none. A time that has passed, e.g. because the library did not run, moves to the same time
    of the next day: a backup is not made up for in the middle of the day."""
    at = _time_of_day(settings["time"])
    day = now.date() if last is None else last + datetime.timedelta(days=int(settings["every"]))
    due = datetime.datetime.combine(max(day, now.date()), at)
    return due if due > now else due + datetime.timedelta(days=1)


class Backups:
    def __init__(self, folder: str, settings_file: str, create: Callable[[bool], Backup], logger: logging.Logger):
        """``folder``: where the backups are kept unless another folder is set; ``settings_file``: the folder, the
        schedule and what the last backup was; ``create``: takes the data for a backup, with the albums or without."""
        self._default_folder = folder
        self._settings_file = settings_file
        self._create = create
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            **DEFAULTS, "folder": None, "last": None, "scheduled": None, **read_json(settings_file, {}),
        }  # fmt: skip
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._asked: bool | None = None  # a backup asked for at once: with the albums or without
        self._running = False
        self._thread: threading.Thread | None = None

    # --- what the page shows and sets -------------------------------------------------------------------------------

    @property
    def folder(self) -> str:
        """Where the backups are kept: the folder that was set, else the one of the config."""
        return str(self._state["folder"] or self._default_folder)

    def _checked_folder(self, value: object) -> str | None:
        """The folder to set: None for the one of the config, else an absolute path that the library can write to -
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
                f"the library cannot create the folder {folder}: {error.strerror or error}",
                code="backup.folder_create",
                folder=folder,
            ) from None
        if not os.access(folder, os.W_OK | os.X_OK):
            raise BadRequest(
                f"the library may not write to the folder {folder}.", code="backup.folder_write", folder=folder
            )
        return None if folder == os.path.normpath(self._default_folder) else folder

    def _files(self) -> list[dict[str, Any]]:
        found = []
        with contextlib.suppress(FileNotFoundError):
            for name in sorted(os.listdir(self.folder), reverse=True):
                match = NAME.fullmatch(name)
                if match is None or match.group(1) != "library":
                    continue
                day, clock = match.group(2), match.group(3)
                found.append(
                    {
                        "name": name,
                        "when": f"{day[:4]}-{day[4:6]}-{day[6:]} {clock[:2]}:{clock[2:4]}",
                        "size": os.path.getsize(os.path.join(self.folder, name)),
                        "albums": match.group(4).startswith("-albums"),
                    }
                )
        return found

    def status(self) -> dict[str, Any]:
        """The schedule, the folder and the one of the config (``default_folder``), whether a backup is being made,
        the last one (``when``, ``ok``, ``message``), when the next one is due (local time, None without a schedule)
        and the backups in the folder, newest first."""
        with self._lock:
            state = dict(self._state)
            running = self._running or self._asked is not None
        due = self._due(state) if state["enabled"] else None
        return {
            **{key: state[key] for key in DEFAULTS},
            "folder": self.folder,
            "default_folder": self._default_folder,
            "running": running,
            "last": state["last"],
            "next": due.strftime("%Y-%m-%d %H:%M") if due else None,
            "backups": self._files(),
        }

    def set(self, data: dict[str, Any]) -> dict[str, Any]:
        """Change the schedule and the folder; fields that are left out stay. An empty ``folder`` is the one of the
        config again. Backups that lie in the folder before stay there; the page lists those of the new one."""
        folder = self._checked_folder(data["folder"]) if "folder" in data else self._state["folder"]
        with self._lock:
            if folder != self._state["folder"] and (self._running or self._asked is not None):
                raise Conflict("a backup is being made: the folder cannot be changed now.", code="backup.busy_folder")
            new = {key: data.get(key, self._state[key]) for key in DEFAULTS}
            new["folder"] = folder
            _time_of_day(new["time"])
            for key in ("enabled", "albums"):
                if not isinstance(new[key], bool):
                    raise BadRequest(f"{key} must be true or false.")
            for key, most in (("every", 365), ("keep", 999)):
                if not isinstance(new[key], int) or isinstance(new[key], bool) or not 1 <= new[key] <= most:
                    raise BadRequest(f"{key} must be a number from 1 to {most}.")
            self._state.update(new)
            self._save()
        self._wake.set()
        return self.status()

    def ask(self, albums: bool) -> None:
        """Make a backup now, in the background.

        :raises Conflict: while a backup is being made.
        """
        with self._lock:
            if self._running or self._asked is not None:
                raise Conflict("a backup is being made.", code="backup.busy")
            if self._thread is None:
                raise Conflict("the library is stopping.")
            self._asked = albums
        self._wake.set()

    def path(self, name: str) -> str:
        """The file of a backup in the folder. Only names of the listing are ever opened."""
        if not any(entry["name"] == name for entry in self._files()):
            raise NotFound(f"backup '{name}' does not exist.", code="backup.unknown")
        return os.path.join(self.folder, name)

    def remove(self, name: str) -> None:
        os.remove(self.path(name))
        self._logger.info("backup %s removed", name)

    # --- making them ------------------------------------------------------------------------------------------------

    def _save(self) -> None:
        write_json(self._settings_file, self._state)

    def _due(self, state: dict[str, Any]) -> datetime.datetime:
        last = datetime.date.fromisoformat(state["scheduled"]) if state["scheduled"] else None
        return next_due(datetime.datetime.now(), state, last)

    def make(self, albums: bool) -> str:
        """Write a backup into the folder and remove the oldest ones beyond ``keep``; returns its name. A backup that
        fails leaves nothing in the folder."""
        folder = self.folder
        os.makedirs(folder, exist_ok=True)
        backup = self._create(albums)
        _, result = backup.save(folder)
        for name in result["skipped"]:
            self._logger.warning("backup %s: %s disappeared while it was read", backup.filename, name)
        self._logger.info("backup %s: %d files, %d bytes", backup.filename, result["files"], result["bytes"])
        with self._lock:
            keep = int(self._state["keep"])
        for entry in self._files()[keep:]:
            os.remove(os.path.join(folder, entry["name"]))
            self._logger.info("backup %s removed: %d are kept", entry["name"], keep)
        return backup.filename

    def _make(self, albums: bool, scheduled: bool) -> None:
        with self._lock:
            self._running = True
        try:
            last = {"when": timestamp(), "ok": True, "message": self.make(albums)}
        except Exception as error:
            self._logger.exception("the backup failed")
            last = {"when": timestamp(), "ok": False, "message": str(error) or type(error).__name__}
        with self._lock:
            self._running = False
            self._state["last"] = last
            if scheduled:  # also one that failed: the next try is the next day that is due, not at once again
                self._state["scheduled"] = datetime.date.today().isoformat()
            self._save()

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.clear()  # before the look at what is asked for: what is asked after it wakes the wait below
            with self._lock:
                state = dict(self._state)
                asked, self._asked = self._asked, None
            if asked is not None:
                self._make(asked, scheduled=False)
                continue
            due = self._due(state) if state["enabled"] else None
            wait = 3600.0  # looks at the clock again at least every hour: it may have been set
            if due is not None:
                wait = min(wait, (due - datetime.datetime.now()).total_seconds())
            if self._wake.wait(max(wait, 0.0)):
                continue  # asked for a backup, a new schedule, or the end
            if due is not None and datetime.datetime.now() >= due:
                self._make(bool(state["albums"]), scheduled=True)

    def start(self) -> None:
        self._stopping.clear()
        with contextlib.suppress(FileNotFoundError):
            for name in os.listdir(self.folder):
                if name.endswith(PART):  # what a library left that was stopped in the middle of a backup
                    os.remove(os.path.join(self.folder, name))
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
