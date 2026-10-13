"""Targets a tag can be linked to: albums (a folder with mp3 files and an m3u playlist) and internet radio
stations."""

import logging
import os
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any

import mutagen

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import TargetType, UpdateJson, is_valid_timestamp, now
from tinyjukebox.metadata import mp3_info

__all__ = [
    "MAX_ALBUM_TITLE_LENGTH",
    "MAX_STATION_NAME_LENGTH",
    "MAX_STREAM_URL_LENGTH",
    "ATarget",
    "Target",
    "TargetAlbum",
    "TargetRadio",
    "check_album_title",
    "check_station_name",
    "check_stream_url",
    "is_mp3_filename",
    "read_playlist",
]

MAX_ALBUM_TITLE_LENGTH = 200
MAX_STATION_NAME_LENGTH = 100
MAX_STREAM_URL_LENGTH = 2000


def is_mp3_filename(name: str) -> bool:
    return name.lower().endswith(".mp3")


def check_album_title(title: object) -> str:
    """The title without surrounding blanks. :raises ValueError: if it is empty, too long or has control
    characters."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must not be empty.")
    title = title.strip()
    if len(title) > MAX_ALBUM_TITLE_LENGTH:
        raise ValueError(f"title must not be longer than {MAX_ALBUM_TITLE_LENGTH} characters.")
    if not title.isprintable():
        raise ValueError("title must not contain control characters.")
    return title


def read_playlist(filename: str, playlist_name: str | None = None) -> list[str]:
    """Entries of an m3u playlist: comments/directives (#EXTM3U, #EXTINF) and the playlist itself are skipped.

    :raises OSError: if the playlist cannot be read.
    """
    with open(filename, "rb") as infile:
        raw = infile.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1252", errors="replace")  # playlists saved on windows
    playlist_name = playlist_name or os.path.basename(filename)
    entries = (line.strip() for line in content.splitlines())
    # 'ls > playlist.m3u' lists the playlist itself
    return [entry for entry in entries if entry and not entry.startswith("#") and entry != playlist_name]


class ATarget(ABC):
    """Base class of all targets."""

    def __init__(self, target_id: str | int, target_type: str, logger: logging.Logger, update_json: UpdateJson) -> None:
        self._logger = get_child(logger, f"{type(self).__name__}.{target_id}")
        self.target_id: str | None = str(target_id)
        self.target_type: TargetType | None = TargetType[target_type]
        self._update_json = update_json
        self.created: str | None = None

    @abstractmethod
    def sanity_check(self) -> tuple[bool, str]:
        """(passed, error messages separated by newlines)"""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...

    @abstractmethod
    def to_text(self, compact: bool = True) -> str: ...


class TargetAlbum(ATarget):
    """Entry of ``targets.json``::

        {"target-id": "7", "target-type": "album", "album-folder": "Album A", "playlist": "playlist.m3u",
         "created": "...", "title": "Album A (Hörspiel)", "cover": "album-1a2b3c4d.jpg"}

    ``album-folder`` is relative to the albums folder (``root_folder``). ``title`` is missing in entries that were
    made before albums had one: their title comes from the tracks or the folder
    (:meth:`tinyjukebox.control.CatalogControl.album_title`). ``cover`` is missing for an album without a cover of
    its own; the image is kept in the images folder (:mod:`tinyjukebox.images`).
    """

    def __init__(
        self, target_dict: dict[str, Any], root_folder: str, logger: logging.Logger, update_json: UpdateJson
    ) -> None:
        for key in ("target-id", "target-type"):
            if key not in target_dict:
                logger.error("TargetAlbum.__init__ - '%s' not in dict: %s.", key, target_dict)
                raise ValueError(f"TargetAlbum.__init__ - '{key}' not in dict: {target_dict}.")
        super().__init__(target_dict["target-id"], target_dict["target-type"], logger, update_json)
        self.root_folder: str | None = os.path.normpath(root_folder)
        self.incomplete = False  # files of the album are still being fetched from the library (tinyjukebox.sync)
        self.load(target_dict)
        self._logger.debug("__init__ - full_album_folder: %s", self.full_album_folder)

    def load(self, target_dict: dict[str, Any]) -> None:
        """Take over id, folder, playlist, title, cover and time stamp of ``target_dict`` (an entry of
        ``targets.json``) - nothing is written."""
        self.target_id = str(target_dict["target-id"])
        title = target_dict.get("title")
        self.title: str | None = title.strip() or None if isinstance(title, str) else None
        self.cover: str | None = target_dict.get("cover") or None
        self.album_folder: str | None = os.path.normpath(target_dict["album-folder"])
        self.full_album_folder: str | None = os.path.join(str(self.root_folder), self.album_folder)
        self.playlist: str | None = target_dict["playlist"]
        self.created = target_dict.get("created") or now()
        self._tracks: list[str] | None = None
        self._tracks_of: tuple[int, int] | None = None  # the state of the playlist that ``_tracks`` was read from

    def set_title(self, title: object) -> None:
        """Change the title. :raises ValueError: for an invalid title - nothing is changed then."""
        new_title = check_album_title(title)
        if new_title == self.title:
            return
        self._logger.info("set_title - '%s' -> '%s'", self.title, new_title)
        self.title = new_title
        self._update_json()

    def set_cover(self, cover: str | None) -> None:
        """``cover``: name of an image in the images folder, None for no cover of its own."""
        if cover == self.cover:
            return
        self._logger.info("set_cover - '%s' -> '%s'", self.cover, cover)
        self.cover = cover
        self._update_json()

    def get_tracks(self, force_update: bool = False) -> list[str]:
        """Paths of all tracks in playlist order. The playlist is read again whenever it changed - an album may get
        its files after it was listed, e.g. from the library - and with ``force_update``."""
        state = self._playlist_state()
        if self._tracks is None or force_update or state != self._tracks_of:
            self._tracks = self._read_playlist()
            self._tracks_of = state
        return self._tracks

    def _playlist_state(self) -> tuple[int, int] | None:
        """Changes whenever the playlist is written; None without a playlist."""
        if self.full_album_folder is None or self.playlist is None:
            return None
        try:
            stat = os.stat(os.path.join(self.full_album_folder, self.playlist))
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _read_playlist(self) -> list[str]:
        if self.full_album_folder is None or self.playlist is None:
            self._logger.warning("_read_playlist - full_album_folder or playlist is None")
            return []
        try:
            entries = read_playlist(os.path.join(self.full_album_folder, self.playlist), self.playlist)
        except OSError as e:
            self._logger.warning("_read_playlist - failed to read playlist: %s", e)
            return []
        tracks = [os.path.join(self.full_album_folder, entry) for entry in entries]
        self._logger.debug("_read_playlist - %s", tracks)
        return tracks

    def sanity_check(self) -> tuple[bool, str]:
        fields_ok, fields_err = self.sanity_check_fields()
        files_ok, files_err = self.sanity_check_files()
        tracks_ok, tracks_err = self.sanity_check_tracks() if files_ok else (True, "")
        if fields_ok and files_ok and tracks_ok:
            return True, ""
        errors = [
            err for ok, err in ((fields_ok, fields_err), (files_ok, files_err), (tracks_ok, tracks_err)) if not ok
        ]
        return False, "\n".join(errors)

    def sanity_check_fields(self) -> tuple[bool, str]:
        errors = []
        if not is_valid_timestamp(self.created):
            errors.append(f"sanity_check - created timestamp error '{self.created}'")
        if self.target_type != TargetType.album:
            value = self.target_type.name if isinstance(self.target_type, TargetType) else self.target_type
            errors.append(f"sanity_check - target type not set or not expected value '{value}'")
        if not self.target_id:
            errors.append("sanity_check - target-id not set.")
        for message in errors:
            self._logger.warning(message)
        return not errors, "\n".join(errors)

    def sanity_check_files(self) -> tuple[bool, str]:
        # plain path checks - glob would treat '[...]' in album or file names as patterns
        folder = self.full_album_folder
        if folder is None or not os.path.isdir(folder):
            return False, f"sanity_check - full_album_folder '{folder}' not found."
        if self.playlist is not None and not os.path.isfile(os.path.join(folder, self.playlist)):
            return False, f"sanity_check - playlist not found in full_album_folder '{folder}'."
        if not any(is_mp3_filename(name) for name in os.listdir(folder)):
            return False, f"sanity_check - no mp3-files found in full_album_folder '{folder}'."
        return True, ""

    def sanity_check_tracks(self) -> tuple[bool, str]:
        tracks = self.get_tracks()
        if not tracks:
            return False, f"sanity_check - playlist {self.playlist} contains no tracks."
        errors = []
        for track in tracks:
            if not os.path.exists(track):
                errors.append(f"sanity_check - file {track} not found.")
                continue
            try:
                mp3_info(track)
            except mutagen.MutagenError:
                errors.append(f"sanity_check - file {track} not valid MP3.")
        return not errors, "\n".join(errors)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "target-id": self.target_id,
            "target-type": self.target_type.name if self.target_type is not None else None,
            "created": self.created,
            "album-folder": self.album_folder,
            "playlist": self.playlist,
        }
        if self.title is not None:
            result["title"] = self.title
        if self.cover is not None:
            result["cover"] = self.cover
        return result

    def to_text(self, compact: bool = True) -> str:
        target_type = self.target_type.name if self.target_type is not None else "None"
        text = (
            f"id: {self.target_id}, type: {target_type}, created: {self.created}, "
            f"album-folder: {self.album_folder}, playlist: {self.playlist}"
        )
        if self.title is not None:
            text += f", title: {self.title}"
        if not compact:
            tracks = [os.path.relpath(t, self.root_folder) if self.root_folder else t for t in self.get_tracks()]
            text += f", track-count: {len(tracks)}, tracks: {tracks}"
        return text

    @classmethod
    def create_target(
        cls, target_dict: dict[str, Any], root_folder: str, logger: logging.Logger, update_json: UpdateJson | None
    ) -> "TargetAlbum":
        """A new album - :raises ValueError: if the dict is not an album or the album fails the sanity check."""
        if target_dict.get("target-type") != TargetType.album.name:
            logger.error("TargetAlbum.create_target - wrong target type in %s.", target_dict)
            raise ValueError(f"TargetAlbum.create_target - wrong target type in {target_dict}.")
        if update_json is None:
            logger.error("TargetAlbum.create_target - update_json method is None")
            raise ValueError("TargetAlbum.create_target - update_json method is None")
        if "title" in target_dict:
            target_dict["title"] = check_album_title(target_dict["title"])
        album = TargetAlbum(target_dict, root_folder, logger, update_json)
        sane, err = album.sanity_check()
        if not sane:
            message = f"TargetAlbum.create_target - sanity check failed on dict '{target_dict}':  {err}"
            album._logger.error(message)
            raise ValueError(message)
        return album


def check_station_name(name: object) -> str:
    """The name without surrounding blanks. :raises ValueError: if it is empty or too long."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must not be empty.")
    if len(name.strip()) > MAX_STATION_NAME_LENGTH:
        raise ValueError(f"name must not be longer than {MAX_STATION_NAME_LENGTH} characters.")
    return name.strip()


def check_stream_url(url: object) -> str:
    """The url without surrounding blanks. :raises ValueError: if it is no http(s) url with a host."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must not be empty.")
    url = url.strip()
    if len(url) > MAX_STREAM_URL_LENGTH:
        raise ValueError(f"url must not be longer than {MAX_STREAM_URL_LENGTH} characters.")
    if any(c.isspace() or not c.isprintable() for c in url):
        raise ValueError("url must not contain blanks or control characters.")
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise ValueError(f"invalid url '{url}'.") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise ValueError("url must start with http:// or https:// and contain a host.")
    return url


class TargetRadio(ATarget):
    """Entry of ``targets.json``::

        {"target-id": "12", "target-type": "radio", "name": "FM4", "url": "https://...", "created": "...",
         "cover": "station-1a2b3c4d.jpg"}

    ``url`` is the stream itself or an m3u/pls playlist that lists the stream (see
    :func:`tinyjukebox.albumplayer.radioplayer.resolve_stream_url`). ``cover`` is missing for a station without a
    cover; the image is kept in the images folder (:mod:`tinyjukebox.images`).
    """

    def __init__(self, target_dict: dict[str, Any], logger: logging.Logger, update_json: UpdateJson) -> None:
        for key in ("target-id", "target-type"):
            if key not in target_dict:
                logger.error("TargetRadio.__init__ - '%s' not in dict: %s.", key, target_dict)
                raise ValueError(f"TargetRadio.__init__ - '{key}' not in dict: {target_dict}.")
        super().__init__(target_dict["target-id"], target_dict["target-type"], logger, update_json)
        self.load(target_dict)

    def load(self, target_dict: dict[str, Any]) -> None:
        """Take over id, name, url, cover and time stamp of ``target_dict`` (an entry of ``targets.json``) - nothing
        is written."""
        self.target_id = str(target_dict["target-id"])
        self.name: str = target_dict.get("name", "")
        self.url: str = target_dict.get("url", "")
        self.cover: str | None = target_dict.get("cover") or None
        self.created = target_dict.get("created") or now()

    def update(self, name: str | None = None, url: str | None = None) -> None:
        """Change name and/or url (``None``: unchanged). :raises ValueError: for invalid values - nothing is
        changed then."""
        new_name = self.name if name is None else check_station_name(name)
        new_url = self.url if url is None else check_stream_url(url)
        if (new_name, new_url) == (self.name, self.url):
            return
        self._logger.info("update - name: '%s' -> '%s', url: '%s' -> '%s'", self.name, new_name, self.url, new_url)
        self.name, self.url = new_name, new_url
        self._update_json()

    def set_cover(self, cover: str | None) -> None:
        """``cover``: name of an image in the images folder, None for no cover."""
        if cover == self.cover:
            return
        self._logger.info("set_cover - '%s' -> '%s'", self.cover, cover)
        self.cover = cover
        self._update_json()

    def sanity_check(self) -> tuple[bool, str]:
        errors = []
        if not is_valid_timestamp(self.created):
            errors.append(f"sanity_check - created timestamp error '{self.created}'")
        if self.target_type != TargetType.radio:
            value = self.target_type.name if isinstance(self.target_type, TargetType) else self.target_type
            errors.append(f"sanity_check - target type not set or not expected value '{value}'")
        if not self.target_id:
            errors.append("sanity_check - target-id not set.")
        for check, value in ((check_station_name, self.name), (check_stream_url, self.url)):
            try:
                check(value)
            except ValueError as e:
                errors.append(f"sanity_check - {e}")
        for message in errors:
            self._logger.warning(message)
        return not errors, "\n".join(errors)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "target-id": self.target_id,
            "target-type": self.target_type.name if self.target_type is not None else None,
            "created": self.created,
            "name": self.name,
            "url": self.url,
        }
        if self.cover is not None:
            result["cover"] = self.cover
        return result

    def to_text(self, compact: bool = True) -> str:
        target_type = self.target_type.name if self.target_type is not None else "None"
        return f"id: {self.target_id}, type: {target_type}, created: {self.created}, name: {self.name}, url: {self.url}"

    @classmethod
    def create_target(
        cls, target_dict: dict[str, Any], logger: logging.Logger, update_json: UpdateJson | None
    ) -> "TargetRadio":
        """A new radio station - :raises ValueError: if the dict is not a radio station or name/url are invalid."""
        if target_dict.get("target-type") != TargetType.radio.name:
            logger.error("TargetRadio.create_target - wrong target type in %s.", target_dict)
            raise ValueError(f"TargetRadio.create_target - wrong target type in {target_dict}.")
        if update_json is None:
            logger.error("TargetRadio.create_target - update_json method is None")
            raise ValueError("TargetRadio.create_target - update_json method is None")
        target_dict = dict(
            target_dict, name=check_station_name(target_dict.get("name")), url=check_stream_url(target_dict.get("url"))
        )
        return TargetRadio(target_dict, logger, update_json)


Target = TargetAlbum | TargetRadio
"""Every target that can be linked to a tag."""
