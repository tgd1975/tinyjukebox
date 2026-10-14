"""Putting a backup back (docs/developer/operations.md, section 10.3).

A restore replaces the data of a box or of the library by the data of a backup, in two steps:

1. :meth:`Restore.stage` reads the archive to its end and unpacks it into new folders beside the current ones
   (``json.new``, ``images.new``, ``albums.new``, ``data.new``). A backup of the other kind, of a newer format, one that
   was cut off, or one with the albums for which the space is missing is refused; what was unpacked is removed again.
   Nothing of the current data is touched, and the program goes on as before.
2. :meth:`Restore.apply`, after the program has stopped: every current folder becomes ``….before`` and the new folder
   takes its place. The program starts again with the restored data and removes the ``….before`` folders
   (:meth:`Restore.clean`). A swap that a power cut interrupted is finished at the next start (:meth:`Restore.finish`).

Nothing secret is in a backup, so nothing secret is replaced: what a folder keeps (:attr:`Place.keep`) is carried over
into the new folder. A backup without the albums leaves the albums folder as it is; an album that the restored data
names and whose folder is missing is logged and named on the page.
"""

import contextlib
import errno
import json
import logging
import os
import shutil
import tarfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO, Any

from tinyjukebox.backup import FIRST, FORMAT, JOURNAL, LAST, Incomplete
from tinyjukebox.core.logging import get_child
from tinyjukebox.errors import BadRequest, Conflict, InsufficientStorage
from tinyjukebox.sync.util import read_json, timestamp, write_json

__all__ = ["BEFORE", "NEW", "RESTART", "STATE", "Place", "Restore", "missing_albums"]

RESTART = 75
"""The exit status of a box that stops to start again (EX_TEMPFAIL): systemd starts it again, as after an update."""
NEW = ".new"
BEFORE = ".before"
STATE = "restore.json"  # in the state folder: a swap that is under way, then what the last restore did
KEEP_FREE = 100 * 2**20  # bytes that stay free beside the albums of a backup


@dataclass
class Place:
    """Where a folder of the archive is put back. ``keep``: names in the current folder that a restore does not
    replace; they are carried into the new folder."""

    name: str  # the folder in the archive: json, images, albums, data
    path: str
    keep: Sequence[str] = ()


def missing_albums(targets: dict[str, Any], albums_folder: str) -> list[dict[str, str]]:
    """The albums of ``targets`` (as in ``targets.json``) whose folder is not in ``albums_folder``."""
    missing = []
    for key, value in sorted(targets.items(), key=lambda item: (len(item[0]), item[0])):
        if isinstance(value, dict) and value.get("target-type") == "album":
            folder = str(value.get("album-folder") or "")
            if not os.path.isdir(os.path.join(albums_folder, folder)):
                title = value.get("title") or os.path.basename(os.path.normpath(folder))
                missing.append({"id": str(key), "title": str(title), "folder": folder})
    return missing


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.abspath(child), os.path.abspath(parent)
    return child != parent and os.path.commonpath([child, parent]) == parent


class _Limited:
    """The first ``size`` bytes of a stream - the body of a request."""

    def __init__(self, stream: IO[bytes], size: int) -> None:
        self._stream = stream
        self._left = size

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        data = self._stream.read(self._left if size < 0 else min(size, self._left))
        self._left -= len(data)
        if not data:
            raise Incomplete("the request ended early")
        return data


class Restore:
    def __init__(
        self,
        kind: str,
        places: Sequence[Place],
        state_folder: str,
        logger: logging.Logger,
        journal: Callable[[list[dict[str, Any]]], None] | None = None,
        missing: Callable[[], list[dict[str, str]] | None] | None = None,
    ) -> None:
        """``kind``: ``box`` or ``library`` - only a backup of this kind is put back; ``places``: where the folders of
        the archive go, the albums under the name ``albums``; ``state_folder``: a folder that a restore does not
        replace, for :data:`STATE`. ``journal(lines)`` puts the lines of the journal of a box without a library back,
        ``missing()`` names the albums of the restored data whose folder is missing (None: not asked)."""
        self.kind = kind
        self._places = {place.name: place for place in places}
        self._state_file = os.path.join(state_folder, STATE)
        self._logger = get_child(logger, type(self).__name__)
        self._journal = journal
        self._missing = missing
        self._lock = threading.Lock()
        self.state = "idle"  # staging, staged, confirmed
        self.about: dict[str, Any] | None = None
        self.error: str | None = None
        self._lines: list[dict[str, Any]] | None = None
        self.on_confirm: Callable[[], None] | None = None  # stops the program, which then calls apply()

    # --- where things go ---------------------------------------------------------------------------------------------

    def _outer(self, place: Place) -> Place | None:
        """The place whose folder holds the folder of ``place`` - the images folder of a box lies in its json folder."""
        return next((other for other in self._places.values() if _inside(place.path, other.path)), None)

    def _staged(self, place: Place) -> str:
        """Where the folder of ``place`` is unpacked: beside the current folder, or inside the new folder that holds
        it."""
        outer = self._outer(place)
        if outer is None:
            return place.path + NEW
        return os.path.join(outer.path + NEW, os.path.relpath(place.path, outer.path))

    def _swapped(self) -> list[Place]:
        """The places whose folders are swapped: those that are not inside another one and were unpacked."""
        return [p for p in self._places.values() if self._outer(p) is None and os.path.isdir(p.path + NEW)]

    # --- what the page shows -----------------------------------------------------------------------------------------

    def last(self) -> dict[str, Any] | None:
        """What the last restore did: when, which backup, and the albums whose folder is missing."""
        state = read_json(self._state_file, None)
        return state if isinstance(state, dict) and state.get("done") else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self.state, "about": self.about, "error": self.error, "last": self.last()}

    @property
    def confirmed(self) -> bool:
        return self.state == "confirmed"

    # --- 1. unpacking ------------------------------------------------------------------------------------------------

    def stage(self, source: str | IO[bytes], size: int | None = None) -> dict[str, Any]:
        """Read a backup to its end and unpack it beside the current data: a file, or a stream of ``size`` bytes
        (the body of an upload). Returns what the backup says about itself. A backup that was unpacked before and
        not put back is discarded.

        :raises Incomplete: no backup, or one that was cut off;
        :raises BadRequest: a backup of the other kind or of a newer format;
        :raises InsufficientStorage: a backup with the albums for which the space is missing;
        :raises Conflict: while a backup is being unpacked or put back.
        """
        self._begin_staging()
        return self._stage(source, size)

    def stage_in_background(self, filename: str) -> None:
        """:meth:`stage` for a backup in a folder of this computer, which may take a while: :meth:`status` says
        when it is unpacked, or why not."""
        self._begin_staging()

        def run() -> None:
            with contextlib.suppress(Exception):  # status() tells what went wrong
                self._stage(filename, None)

        threading.Thread(target=run, name="Restore.stage", daemon=True).start()

    def _begin_staging(self) -> None:
        with self._lock:
            if self.state in ("staging", "confirmed"):
                raise Conflict("a backup is being put back already.", code="restore.busy")
            self.state, self.about, self.error, self._lines = "staging", None, None, None

    def _stage(self, source: str | IO[bytes], size: int | None) -> dict[str, Any]:
        try:
            self._remove_new()
            if isinstance(source, str):
                about = self._unpack(source, os.path.getsize(source))
            else:
                about = self._unpack(_Limited(source, size or 0), size)
        except BaseException as error:
            self._remove_new()
            with self._lock:
                self.state, self.error = "idle", str(error) or type(error).__name__
            raise
        with self._lock:
            self.state, self.about = "staged", about
        self._logger.info(
            "backup of %s unpacked (%s, %d files): it is put back when this is confirmed",
            about.get("created"), "with the albums" if about.get("albums") else "without the albums",
            about.get("files", 0),
        )  # fmt: skip
        return about

    def _check(self, about: dict[str, Any], size: int | None) -> None:
        if about.get("kind") != self.kind:
            raise BadRequest(
                f"this is a backup of a {about.get('kind')}, not of a {self.kind}.",
                code="restore.kind", kind=str(about.get("kind")),
            )  # fmt: skip
        if not isinstance(about.get("format"), int) or about["format"] > FORMAT:
            raise BadRequest(
                "this backup was made by a newer version, which this one cannot read.",
                code="restore.format", version=str(about.get("version")),
            )  # fmt: skip
        albums = self._places.get("albums")
        if about.get("albums") and albums is not None and size:
            parent = os.path.dirname(os.path.abspath(albums.path))
            free = shutil.disk_usage(parent).free
            if free < size + KEEP_FREE:
                missing = size + KEEP_FREE - free
                raise InsufficientStorage(
                    f"for a moment the albums need their space twice: {missing // 2**20} MB are missing.",
                    code="restore.space", missing=missing,
                )  # fmt: skip

    def _unpack(self, source: Any, size: int | None) -> dict[str, Any]:
        about: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        kwargs: dict[str, Any] = {"name": source} if isinstance(source, str) else {"fileobj": source}
        try:
            with tarfile.open(mode="r|*", **kwargs) as archive:
                for index, member in enumerate(archive):
                    if index == 0:
                        about = self._begin(archive, member, size)
                    else:
                        result = self._member(archive, member, bool(about and about.get("albums")))
        except Incomplete:
            raise
        except OSError as error:
            if error.errno == errno.ENOSPC:
                raise InsufficientStorage("the disk is full.", code="restore.full") from None
            if error.errno is None:  # not the disk: what was read is no gzip, for example
                raise Incomplete(f"cannot be read to its end: {error}") from None
            raise
        except (tarfile.TarError, EOFError, ValueError) as error:
            raise Incomplete(f"cannot be read to its end: {error}") from None
        if about is None:
            raise Incomplete(f"no backup: it does not begin with {FIRST}")
        if result is None:
            raise Incomplete(f"cut off: it does not end with {LAST}")
        return {**about, **result}

    def _begin(self, archive: tarfile.TarFile, member: tarfile.TarInfo, size: int | None) -> dict[str, Any]:
        """The first file of a backup says what it is a backup of: checked before anything is unpacked."""
        if member.name != FIRST:
            raise Incomplete(f"no backup: it does not begin with {FIRST}")
        about: dict[str, Any] = json.load(archive.extractfile(member))  # type: ignore[arg-type]
        self._check(about, size)
        for place in self._places.values():
            if place.name != "albums" or about.get("albums"):
                os.makedirs(self._staged(place), exist_ok=True)
        return about

    def _member(self, archive: tarfile.TarFile, member: tarfile.TarInfo, albums: bool) -> dict[str, Any] | None:
        """A file of the archive after the first one; returns what ``complete.json`` says if it is that file - which
        counts only as the last one."""
        if member.name == LAST:
            result: dict[str, Any] = json.load(archive.extractfile(member))  # type: ignore[arg-type]
            return result
        if member.name == JOURNAL and member.isfile():
            content = archive.extractfile(member).read().decode("utf-8")  # type: ignore[union-attr]
            self._lines = [json.loads(line) for line in content.splitlines() if line.strip()]
        else:
            self._extract(archive, member, albums)
        return None

    def _extract(self, archive: tarfile.TarFile, member: tarfile.TarInfo, albums: bool) -> None:
        """One file of the archive into the new folder of its place. Only regular files below a known folder; a name
        that would lead out of it is no part of a backup."""
        top, _, inside = member.name.partition("/")
        place = self._places.get(top)
        if place is None or not inside or not member.isfile() or (top == "albums" and not albums):
            return
        parts = inside.split("/")
        if any(part in ("", ".", "..") for part in parts) or os.path.isabs(inside):
            raise Incomplete(f"no backup: the name {member.name!r} leads out of its folder")
        target = os.path.join(self._staged(place), *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        infile = archive.extractfile(member)
        if infile is None:
            return
        with open(target, "wb") as outfile:
            shutil.copyfileobj(infile, outfile, 1 << 20)
        os.utime(target, (member.mtime, member.mtime))

    def _remove_new(self) -> None:
        for place in self._places.values():
            if self._outer(place) is None:
                shutil.rmtree(place.path + NEW, ignore_errors=True)

    def discard(self) -> None:
        """Do not put the backup that was unpacked back."""
        with self._lock:
            if self.state in ("staging", "confirmed"):
                raise Conflict("a backup is being put back.", code="restore.busy")
            self.state, self.about, self._lines = "idle", None, None
        self._remove_new()

    def confirm(self) -> None:
        """Put the backup that was unpacked back: the program stops (``on_confirm``) and calls :meth:`apply`."""
        with self._lock:
            if self.state != "staged":
                raise Conflict("no backup is waiting to be put back.", code="restore.nothing")
            self.state = "confirmed"
        self._logger.warning(
            "the backup of %s is put back: the program starts again", (self.about or {}).get("created")
        )
        if self.on_confirm is not None:
            self.on_confirm()

    # --- 2. swapping -------------------------------------------------------------------------------------------------

    def apply(self) -> dict[str, Any]:
        """Swap the folders - only while the program does not use them, i.e. after it has stopped. Returns what
        :meth:`last` says from now on."""
        with self._lock:
            if self.state != "confirmed":
                raise Conflict("no backup is confirmed to be put back.", code="restore.nothing")
            about = dict(self.about or {})
        places = self._swapped()
        write_json(self._state_file, {"swapping": [place.name for place in places], "about": about})
        self._swap(places)
        if self._lines is not None and self._journal is not None:
            self._journal(self._lines)
        result = self._done(about)
        with self._lock:
            self.state, self.about, self._lines = "idle", None, None
        return result

    def _swap(self, places: Sequence[Place]) -> None:
        for place in places:
            new, before = place.path + NEW, place.path + BEFORE
            for name in place.keep:  # what a restore does not replace
                kept = os.path.join(place.path, name)
                if os.path.lexists(kept):
                    if os.path.isdir(os.path.join(new, name)):
                        shutil.rmtree(os.path.join(new, name))
                    os.replace(kept, os.path.join(new, name))
            if os.path.lexists(place.path):
                if os.path.lexists(before):
                    shutil.rmtree(before)
                os.replace(place.path, before)
            os.replace(new, place.path)
            self._logger.info("%s: the data of the backup is in place, the one of before in %s", place.path, before)

    def _done(self, about: dict[str, Any]) -> dict[str, Any]:
        missing = self._missing() if self._missing is not None else None
        for album in missing or []:
            self._logger.error(
                "restore: album '%s' (%s) is in the data, but its folder '%s' is missing.",
                album["title"], album["id"], album["folder"],
            )  # fmt: skip
        keys = ("created", "version", "albums", "files", "bytes")
        result = {"done": timestamp(), "backup": {key: about.get(key) for key in keys}, "missing": missing}
        write_json(self._state_file, result)
        return result

    def finish(self) -> None:
        """At the start, before the data is read: a swap that was interrupted - the power was cut - is finished; new
        folders that were unpacked and never put back are removed."""
        state = read_json(self._state_file, None)
        if isinstance(state, dict) and "swapping" in state:
            places = [self._places[name] for name in state["swapping"] if name in self._places]
            self._logger.warning("the restore was interrupted while the folders were swapped: it is finished now")
            self._swap([place for place in places if os.path.isdir(place.path + NEW)])
            self._done(state.get("about") or {})
        self._remove_new()

    def clean(self) -> None:
        """Remove the data of before, once the program runs with the restored data - in the background: the albums
        of before may take a while."""
        before = [place.path + BEFORE for place in self._places.values() if self._outer(place) is None]
        before = [folder for folder in before if os.path.lexists(folder)]
        if not before:
            return

        def remove() -> None:
            for folder in before:
                with contextlib.suppress(OSError):
                    shutil.rmtree(folder)
                self._logger.info("%s removed: the restored data runs", folder)

        threading.Thread(target=remove, name="Restore.clean", daemon=True).start()
