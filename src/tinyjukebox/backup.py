"""A backup of the data of a box or of the library: one tar archive (docs/developer/operations.md, section 10).

The archive begins with ``backup.json``, which says what it is a backup of, and ends with ``complete.json``: an
archive without it was cut off. In between lie the data files, each below the name of the folder it belongs to
(``json``, ``data``, ``images``, ``albums``) - restoring is unpacking into these folders. With the albums the archive is
not compressed, because audio files do not get smaller; without them it is gzip.

Nothing secret is part of a backup: no certificate, no user of the page, no password and no token.
"""

import contextlib
import io
import json
import os
import re
import tarfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import IO, Any, Protocol

from tinyjukebox import __version__

__all__ = ["FIRST", "FORMAT", "LAST", "NAME", "PART", "Backup", "Folder", "Incomplete", "verify"]

FORMAT = 1
FIRST = "backup.json"
LAST = "complete.json"
NAME = re.compile(r"tinyjukebox-(box|library)-(\d{8})-(\d{6})(-albums\.tar|\.tar\.gz)")
"""The file names of backups; they sort by the time the backup was made."""
PART = ".part"  # a backup that is being written
UNFINISHED = (".tmp", PART)  # files that are being written
NO_DATA = (".", "@")  # how the names of hidden files and of the folders of a NAS (@eaDir) begin


class Incomplete(ValueError):  # noqa: N818
    """The file is no backup, or one that was cut off."""


class _Out(Protocol):
    def write(self, data: bytes, /) -> Any: ...


@dataclass
class Folder:
    """A folder whose files go into the archive below ``name``, read while the archive is written. ``skip``: names
    directly in the folder that are left out. Always left out: hidden files and folders, the folders ``@eaDir`` that a
    NAS puts everywhere, and files that are being written."""

    name: str
    path: str
    skip: Sequence[str] = ()

    def files(self) -> Iterator[tuple[str, str]]:
        """(name in the archive, file) in the order of the names."""
        for root, folders, names in os.walk(self.path):
            top = os.path.abspath(root) == os.path.abspath(self.path)
            folders[:] = sorted(f for f in folders if not f.startswith(NO_DATA) and not (top and f in self.skip))
            for name in sorted(names):
                if name.startswith(NO_DATA) or name.endswith(UNFINISHED) or (top and name in self.skip):
                    continue
                filename = os.path.join(root, name)
                inside = os.path.relpath(filename, self.path).replace(os.sep, "/")
                yield f"{self.name}/{inside}", filename


@dataclass
class Backup:
    """What a backup is made of. ``data`` was taken at one moment and is kept in memory; the files of ``folders`` are
    read while the archive is written."""

    kind: str  # "box" or "library"
    albums: bool  # with the albums, i.e. the audio files
    data: list[tuple[str, bytes]] = field(default_factory=list)  # (name in the archive, content)
    folders: list[Folder] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    @property
    def filename(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.created))
        return f"tinyjukebox-{self.kind}-{stamp}{'-albums.tar' if self.albums else '.tar.gz'}"

    @property
    def content_type(self) -> str:
        return "application/x-tar" if self.albums else "application/gzip"

    def write(self, out: _Out) -> dict[str, Any]:
        """Write the archive to ``out``; returns what ``complete.json`` says. A file that disappears meanwhile is
        left out and named there; any other failure leaves an archive without ``complete.json``."""
        result: dict[str, Any] = {"files": 0, "bytes": 0, "skipped": []}

        def add(archive: tarfile.TarFile, name: str, content: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = int(self.created)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))

        about = {
            "format": FORMAT,
            "kind": self.kind,
            "version": __version__,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created)),
            "albums": self.albums,
        }
        # "w|": a stream that is only written forward - the answer to a request cannot be rewound
        with tarfile.open(fileobj=out, mode="w|" if self.albums else "w|gz") as archive:  # type: ignore[call-overload]
            add(archive, FIRST, _json(about))
            for name, content in self.data:
                add(archive, name, content)
                result["files"] += 1
                result["bytes"] += len(content)
            for folder in self.folders:
                for name, filename in folder.files():
                    try:
                        infile = open(filename, "rb")  # noqa: SIM115 - closed below
                    except FileNotFoundError:
                        result["skipped"].append(name)
                        continue
                    with infile:
                        info = archive.gettarinfo(arcname=name, fileobj=infile)
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, infile)
                    result["files"] += 1
                    result["bytes"] += info.size
            add(archive, LAST, _json(result))
        return result

    def save(self, folder: str) -> tuple[str, dict[str, Any]]:
        """Write the archive into ``folder``; returns its file and what :meth:`write` returns. A backup that fails
        leaves nothing in the folder."""
        target = os.path.join(folder, self.filename)
        try:
            with open(target + PART, "wb") as outfile:
                result = self.write(outfile)
                outfile.flush()
                os.fsync(outfile.fileno())
            os.replace(target + PART, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.remove(target + PART)
            raise
        return target, result


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=True) + "\n").encode("utf-8")


def verify(source: str | IO[bytes]) -> dict[str, Any]:
    """Read a backup from its first to its last byte; returns ``backup.json`` with what ``complete.json`` says.

    :raises Incomplete: if it is no backup or was cut off.
    """
    about: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    try:
        kwargs: dict[str, Any] = {"name": source} if isinstance(source, str) else {"fileobj": source}
        with tarfile.open(mode="r|*", **kwargs) as archive:
            for index, member in enumerate(archive):
                if index == 0 and member.name == FIRST:
                    about = json.load(archive.extractfile(member))  # type: ignore[arg-type]
                elif index == 0:
                    break
                result = json.load(archive.extractfile(member)) if member.name == LAST else None  # type: ignore[arg-type]
    except (tarfile.TarError, EOFError, OSError, ValueError) as error:
        raise Incomplete(f"cannot be read to its end: {error}") from None
    if about is None:
        raise Incomplete(f"no backup: it does not begin with {FIRST}")
    if result is None:
        raise Incomplete(f"cut off: it does not end with {LAST}")
    return {**about, **result}
