"""The covers of the albums, the radio stations and the entries of Spotify: images in one folder, the images folder.

A cover is kept here under a generated name that begins with :data:`ALBUM_PREFIX`, :data:`STATION_PREFIX` or
:data:`SPOTIFY_PREFIX`; the entry names it. Only these images count: any other file in the folder is ignored. An album
without such a cover shows an image of its folder or of its tracks (:func:`tinyjukebox.metadata.find_cover`).
"""

import os
import re
import secrets
from collections.abc import Callable

from tinyjukebox.errors import BadRequest, NotFound, PayloadTooLarge

__all__ = [
    "ALBUM_PREFIX",
    "IMAGE_TYPES",
    "MAX_IMAGE_SIZE",
    "SPOTIFY_PREFIX",
    "STATION_PREFIX",
    "Images",
    "image_extension",
    "is_cover",
    "take_over",
]

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_IMAGE_SIZE = 10 * 2**20
STATION_PREFIX = "station-"
SPOTIFY_PREFIX = "spotify-"
ALBUM_PREFIX = "album-"
# the generated name of the cover of an album. Not every "album-…": until 0.28 the folder held the card images of
# tags, and the images that tinyjukebox-import-covers reads are named "album-<id>"
_ALBUM_COVER = re.compile(r"album-[0-9a-f]{8}")


def image_extension(data: bytes) -> str | None:
    """File extension for the image type detected from the content - ``None`` for unsupported data."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def is_cover(name: str) -> bool:
    """Whether ``name`` is the name of a cover: the prefix of a station or an entry of Spotify, or the generated name
    of the cover of an album, and the extension of an image."""
    base, extension = os.path.splitext(name)
    if extension.lower() not in IMAGE_TYPES:
        return False
    return name.startswith((STATION_PREFIX, SPOTIFY_PREFIX)) or _ALBUM_COVER.fullmatch(base) is not None


def take_over(old_folder: str, folder: str) -> int:
    """Until 0.28 the covers of the stations were in the folder ``cards``, among the card images of tags. Move them
    from ``old_folder`` to ``folder``; whatever else is in the old folder stays there and is not used any more.
    Returns the number of covers moved."""
    try:
        names = [name for name in os.listdir(old_folder) if is_cover(name)]
    except (FileNotFoundError, NotADirectoryError):
        return 0
    os.makedirs(folder, exist_ok=True)
    moved = 0
    for name in names:
        if not os.path.exists(os.path.join(folder, name)):
            os.replace(os.path.join(old_folder, name), os.path.join(folder, name))
            moved += 1
    return moved


class Images:
    """The covers of the albums, stations and entries of Spotify in ``folder``. Only files listed in the folder are ever opened - names
    from requests are never used as paths directly."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self._listener: Callable[[], None] | None = None

    def listen(self, listener: Callable[[], None]) -> None:
        """``listener()`` is called whenever an image was added or removed - for the synchronization
        (:mod:`tinyjukebox.sync`)."""
        self._listener = listener

    def _tell(self) -> None:
        if self._listener is not None:
            self._listener()

    def names(self) -> list[str]:
        try:
            names = os.listdir(self.folder)
        except FileNotFoundError:
            return []
        return sorted(name for name in names if is_cover(name))

    def has(self, name: str) -> bool:
        """Whether the image ``name`` is in the folder."""
        return name in self.names()

    def _check_name(self, name: str) -> str:
        if name not in self.names():
            raise NotFound(f"unknown image '{name}'.")
        return name

    def image(self, name: str) -> tuple[bytes, str]:
        """(content, mime type) of an image."""
        name = self._check_name(name)
        with open(os.path.join(self.folder, name), "rb") as f:
            return f.read(), IMAGE_TYPES[os.path.splitext(name)[1].lower()]

    def add(self, data: bytes, prefix: str = STATION_PREFIX) -> str:
        """Store a new cover (jpeg, png or webp) of a station or, with :data:`ALBUM_PREFIX` or :data:`SPOTIFY_PREFIX`,
        of an album or an entry of Spotify - returns its generated name."""
        if len(data) > MAX_IMAGE_SIZE:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        extension = image_extension(data)
        if extension is None:
            raise BadRequest("only jpeg, png and webp images are supported.", code="image.type")
        os.makedirs(self.folder, exist_ok=True)
        name = f"{prefix}{secrets.token_hex(4)}{extension}"
        temp = os.path.join(self.folder, f".{name}.tmp")  # hidden: no cover while it is written
        with open(temp, "wb") as f:
            f.write(data)
        os.replace(temp, os.path.join(self.folder, name))
        self._tell()
        return name

    def delete(self, name: str) -> None:
        self._check_name(name)
        os.remove(os.path.join(self.folder, name))
        self._tell()
