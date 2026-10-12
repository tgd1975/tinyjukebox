"""The manifest of an album (docs/developer/library.md, section 4.8).

A manifest lists the files of the album folder with size and checksum, and the tracks in the order of the playlist.
Each side compares it with the files it has; a file is complete only if its checksum fits.

    {"files": [{"name": "01.mp3", "size": 123, "sha256": "..."}, ...], "order": ["01.mp3", ...]}

Names are relative to the album folder, with ``/`` between folders. Files that are not in ``order`` - the playlist
itself, a cover - are fetched before the tracks.
"""

import hashlib
import os
from typing import Any

from tinyjukebox.data.target import read_playlist
from tinyjukebox.sync.util import sha256_file

__all__ = ["checksum", "create", "extras", "identity", "missing", "path", "skipped", "valid", "verified"]

SKIPPED_SUFFIXES = (".bak", ".part", ".tmp")


def skipped(name: str) -> bool:
    """Files and folders that are no part of an album: hidden ones, leftovers, and ``@eaDir`` of the NAS."""
    return name.startswith((".", "@")) or name.endswith(SKIPPED_SUFFIXES)


def path(folder: str, name: str) -> str:
    """The file ``name`` of a manifest in ``folder``."""
    return os.path.join(folder, *name.split("/"))


def create(folder: str, playlist: str) -> dict[str, Any]:
    """The manifest of the album in ``folder``. :raises OSError: if the folder or its playlist cannot be read."""
    names = []
    for root, folders, files in os.walk(folder):
        folders[:] = sorted(name for name in folders if not skipped(name))
        relative = os.path.relpath(root, folder)
        for name in sorted(files):
            if not skipped(name):
                names.append(name if relative == "." else "/".join([*relative.split(os.sep), name]))
    files_ = [
        {"name": name, "size": os.path.getsize(path(folder, name)), "sha256": sha256_file(path(folder, name))}
        for name in names
    ]
    tracks = [entry.replace(os.sep, "/") for entry in read_playlist(os.path.join(folder, playlist), playlist)]
    return {"files": files_, "order": [name for name in dict.fromkeys(tracks) if name in names]}


def _valid_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not os.path.isabs(name)
        and "\\" not in name
        and all(part and part != ".." and not skipped(part) for part in name.split("/"))
    )


def valid(manifest: Any) -> bool:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or not manifest["files"]:
        return False
    names = []
    for entry in manifest["files"]:
        if (
            not isinstance(entry, dict)
            or not _valid_name(entry.get("name"))
            or not isinstance(entry.get("size"), int)
            or isinstance(entry.get("size"), bool)
            or not isinstance(entry.get("sha256"), str)
        ):
            return False
        names.append(entry["name"])
    order = manifest.get("order")
    return (
        len(set(names)) == len(names)
        and isinstance(order, list)
        and len(set(order)) == len(order)
        and all(name in names for name in order)
    )


def identity(manifest: dict[str, Any]) -> str:
    """Two albums with the same identity have the same tracks: identical files, whatever their names and whatever
    the playlist looks like (two rips of one record have not)."""
    played = [entry for entry in manifest["files"] if entry["name"] in manifest["order"]] or manifest["files"]
    checksums = sorted(entry["sha256"] for entry in played)
    return hashlib.sha256("\n".join(checksums).encode()).hexdigest()


def checksum(manifest: dict[str, Any]) -> str:
    """Changes whenever a file of the album or the order of its tracks changes."""
    lines = sorted(f"{entry['sha256']} {entry['name']}" for entry in manifest["files"])
    return hashlib.sha256("\n".join([*lines, "", *manifest["order"]]).encode()).hexdigest()


def extras(manifest: dict[str, Any]) -> list[str]:
    """The files that are not played: the playlist, a cover. They are small and needed before the first track."""
    return [entry["name"] for entry in manifest["files"] if entry["name"] not in manifest["order"]]


def verified(folder: str, entry: dict[str, Any]) -> bool:
    """Whether the file of ``entry`` is in ``folder`` with the right size and checksum."""
    filename = path(folder, entry["name"])
    return (
        os.path.isfile(filename)
        and os.path.getsize(filename) == entry["size"]
        and sha256_file(filename) == entry["sha256"]
    )


def missing(folder: str, manifest: dict[str, Any], thorough: bool = False) -> list[str]:
    """Names of the files of the manifest that ``folder`` lacks: first the files that are not played, then the
    tracks in the order of the playlist.

    Files are renamed into the folder only after their checksum was verified, so by default name and size are enough;
    ``thorough`` computes the checksums again.
    """
    entries = {entry["name"]: entry for entry in manifest["files"]}
    result = []
    for name in [*extras(manifest), *manifest["order"]]:
        entry = entries[name]
        filename = path(folder, name)
        present = os.path.isfile(filename) and os.path.getsize(filename) == entry["size"]
        if not present or (thorough and sha256_file(filename) != entry["sha256"]):
            result.append(name)
    return result
