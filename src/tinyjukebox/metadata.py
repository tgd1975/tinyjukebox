"""Track metadata (id3 tags, length) and the images of an album folder, read with mutagen."""

import os
import re
import threading
from pathlib import Path
from typing import Any

import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3

__all__ = [
    "COVER_FILES",
    "TrackCache",
    "TrackInfo",
    "find_cover",
    "mp3_info",
    "read_track",
    "stored_cover",
]

COVER_FILES = ["cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"]
_STORED_COVERS = COVER_FILES[:3]  # the names a cover was stored under until 0.31 (:func:`stored_cover`)

TrackInfo = dict[str, Any]
"""filename, title, artist, album, tracknumber, discnumber, length (None if unknown)."""


def _first(tags: Any, key: str) -> str | None:
    try:
        value = tags.get(key)
    except (KeyError, ValueError):
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value else None


def _number(value: str | None) -> int | None:
    """'3/12' -> 3"""
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else None


def mp3_info(filename: str) -> tuple[float, int]:
    """(length in seconds, sample rate) of an mp3 file.

    :raises mutagen.MutagenError: (e.g. ``HeaderNotFoundError``) if the file is not a valid mp3 file.
    """
    info: Any = MP3(filename).info  # type: ignore[no-untyped-call]  # mutagen.mp3 is untyped
    return float(info.length), int(info.sample_rate)


def read_track(filename: str) -> TrackInfo:
    """Metadata of an mp3 file - missing or unreadable values are ``None``."""
    info: TrackInfo = {
        "filename": os.path.basename(filename),
        "title": None,
        "artist": None,
        "album": None,
        "tracknumber": None,
        "discnumber": None,
        "length": None,
    }
    try:
        audio = mutagen.File(filename, easy=True)
    except Exception:  # mutagen raises all kinds of errors for garbage
        return info
    if audio is None:
        return info
    if audio.info is not None:
        info["length"] = round(audio.info.length, 2)
    if audio.tags is not None:
        info["title"] = _first(audio.tags, "title")
        info["artist"] = _first(audio.tags, "artist")
        info["album"] = _first(audio.tags, "album")
        info["tracknumber"] = _number(_first(audio.tags, "tracknumber"))
        info["discnumber"] = _number(_first(audio.tags, "discnumber"))
    return info


class TrackCache:
    """Reads the metadata once per file (and again if the file has been changed)."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, TrackInfo]] = {}
        self._lock = threading.Lock()

    def get(self, filename: str) -> TrackInfo:
        try:
            mtime = os.stat(filename).st_mtime_ns
        except OSError:
            return dict(read_track(filename), missing=True)
        with self._lock:
            cached = self._cache.get(filename)
        if cached is not None and cached[0] == mtime:
            return dict(cached[1])
        info = read_track(filename)
        with self._lock:
            self._cache[filename] = (mtime, info)
        return dict(info)


def find_cover(album_folder: str, tracks: list[str]) -> tuple[bytes, str] | None:
    """(image, mime type) of an image that comes with the album, or ``None``: what an album without a cover of its
    own shows (:mod:`tinyjukebox.images`). Cover files in the folder win over embedded images."""
    for name in COVER_FILES:
        path = Path(album_folder, name)
        if path.is_file():
            return path.read_bytes(), "image/png" if name.endswith(".png") else "image/jpeg"
    for track in tracks:
        try:
            pictures = ID3(track).getall("APIC")  # type: ignore[no-untyped-call]  # mutagen.id3 is untyped
        except (ID3NoHeaderError, OSError, mutagen.MutagenError):
            continue
        if pictures:
            front = [p for p in pictures if p.type == 3] or pictures  # 3: front cover
            mime = front[0].mime if front[0].mime in ("image/jpeg", "image/png") else "image/jpeg"
            return bytes(front[0].data), str(mime)
    return None


def stored_cover(album_folder: str) -> str | None:
    """Until 0.31 the cover of an album was kept in its folder, as ``cover.jpg`` or ``cover.png``: the file, or
    ``None`` if the folder has none. Now the cover is an image of the images folder (:mod:`tinyjukebox.images`)."""
    for name in _STORED_COVERS:
        path = Path(album_folder, name)
        if path.is_file():
            return str(path)
    return None
