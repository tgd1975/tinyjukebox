"""The covers of the radio stations: images in one folder, the images folder.

The cover of an album is a file of the album (:mod:`tinyjukebox.metadata`). A radio station has no folder, so its cover
is kept here, under a generated name that begins with :data:`STATION_PREFIX`; the entry of the station names it. Only
these images count: any other file in the folder is ignored.
"""

import os
import secrets
from collections.abc import Callable

from tinyjukebox.errors import BadRequest, NotFound, PayloadTooLarge

__all__ = ["IMAGE_TYPES", "MAX_IMAGE_SIZE", "STATION_PREFIX", "Images", "image_extension", "is_cover", "take_over"]

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_IMAGE_SIZE = 10 * 2**20
STATION_PREFIX = "station-"


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
    """Whether ``name`` is the name of the cover of a station: its prefix and the extension of an image."""
    return name.startswith(STATION_PREFIX) and os.path.splitext(name)[1].lower() in IMAGE_TYPES


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
    """The covers of the stations in ``folder``. Only files listed in the folder are ever opened - names from requests
    are never used as paths directly."""

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

    def add(self, data: bytes) -> str:
        """Store a new cover (jpeg, png or webp) - returns its generated name."""
        if len(data) > MAX_IMAGE_SIZE:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        extension = image_extension(data)
        if extension is None:
            raise BadRequest("only jpeg, png and webp images are supported.", code="image.type")
        os.makedirs(self.folder, exist_ok=True)
        name = f"{STATION_PREFIX}{secrets.token_hex(4)}{extension}"
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(data)
        self._tell()
        return name

    def delete(self, name: str) -> None:
        self._check_name(name)
        os.remove(os.path.join(self.folder, name))
        self._tell()
