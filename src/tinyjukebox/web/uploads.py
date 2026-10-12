"""Upload of new albums: mp3 files are collected in a staging folder; on 'finish' the playlist is written, the
folder is moved into the albums folder and the album is added to the targets."""

import contextlib
import logging
import os
import re
import secrets
import shutil
import threading
import time
from typing import Any, Protocol

from tinyjukebox.core.logging import get_child
from tinyjukebox.data import targetalbumtools
from tinyjukebox.data.target import TargetAlbum, check_album_title, is_mp3_filename
from tinyjukebox.data.targetalbumtools import natural_key, playlist_order
from tinyjukebox.data.targets import Targets
from tinyjukebox.errors import BadRequest, Conflict, NotFound, PayloadTooLarge
from tinyjukebox.metadata import TrackInfo, mp3_info, read_track

__all__ = ["MAX_FILES", "MIN_FREE_SPACE", "STAGING_FOLDER", "UPLOAD_EXPIRY", "Upload", "Uploads", "safe_name"]

STAGING_FOLDER = ".tinyjukebox-uploads"
UPLOAD_EXPIRY = 3600  # uploads without changes for this many seconds are discarded
MAX_FILES = 500  # files per upload
MIN_FREE_SPACE = 200 * 2**20  # bytes that must stay free on the albums file system after an upload
# no path separators, no '#' (comment in m3u files), no shell or url specials
_UNSAFE = re.compile(r"[^\w \-–—.,()&'!+]")


def safe_name(name: str | None, what: str = "name") -> str:
    """File or folder name without path separators or other surprising characters."""
    name = _UNSAFE.sub("_", (name or "").strip()).strip(" .")
    if not name:
        raise BadRequest(f"invalid {what}.", code="upload.name_invalid")
    if len(name.encode("utf-8")) > 200:
        raise BadRequest(f"{what} is too long.", code="upload.name_too_long")
    return name


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class Upload:
    def __init__(self, upload_id: str, folder_name: str, staging_folder: str, title: str | None = None) -> None:
        self.upload_id = upload_id
        self.folder_name = folder_name
        self.title = title or folder_name
        self.staging_folder = staging_folder
        self.files: dict[str, TrackInfo] = {}
        self.last_change = time.monotonic()
        self.lock = threading.Lock()

    def to_dict(self) -> dict[str, Any]:
        files = list(self.files.values())
        return {
            "id": self.upload_id,
            "folder": self.folder_name,
            "title": self.title,
            "files": sorted(files, key=lambda f: natural_key(f["filename"])),
            "playlist": playlist_order(files),
        }


class Uploads:
    """Uploads in progress (in memory - leftovers of a previous run are removed on start)."""

    def __init__(
        self,
        targets: Targets,
        playlist_filename: str,
        max_file_size: int,
        logger: logging.Logger,
        min_free_space: int = MIN_FREE_SPACE,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._targets = targets
        self._albums_folder = os.path.abspath(targets.root_folder)
        self._playlist_filename = playlist_filename
        self._max_file_size = max_file_size
        self._min_free_space = min_free_space
        self._staging_root = os.path.join(self._albums_folder, STAGING_FOLDER)
        self._uploads: dict[str, Upload] = {}
        self._lock = threading.Lock()

    def remove_stale_uploads(self) -> None:
        if os.path.isdir(self._staging_root):
            self._logger.info("removing unfinished uploads in '%s'.", self._staging_root)
            shutil.rmtree(self._staging_root, ignore_errors=True)

    def _remove_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [u for u in self._uploads.values() if now - u.last_change > UPLOAD_EXPIRY]
        for upload in expired:
            self._logger.info("upload %s expired.", upload.upload_id)
            with contextlib.suppress(NotFound):  # aborted meanwhile
                self.abort(upload.upload_id)

    def _album_folder(self, folder_name: str) -> str:
        return os.path.join(self._albums_folder, folder_name)

    def create(self, name: object) -> dict[str, Any]:
        """A new upload. ``name`` is the title of the album; its folder gets the same name, without the characters
        that a folder must not have."""
        try:
            title = check_album_title(name)
        except ValueError as e:
            raise BadRequest(f"album name: {e}", code="upload.name_invalid") from None
        folder_name = safe_name(title, "album name")
        self._remove_expired()
        with self._lock:
            if os.path.exists(self._album_folder(folder_name)):
                raise Conflict(
                    f"album folder '{folder_name}' exists already.", code="upload.folder_exists", folder=folder_name
                )
            if any(u.folder_name == folder_name for u in self._uploads.values()):
                raise Conflict(
                    f"an upload for '{folder_name}' is already in progress.",
                    code="upload.in_progress",
                    folder=folder_name,
                )
            upload_id = secrets.token_hex(8)
            staging_folder = os.path.join(self._staging_root, upload_id)
            os.makedirs(staging_folder)
            upload = Upload(upload_id, folder_name, staging_folder, title)
            self._uploads[upload_id] = upload
        self._logger.info("upload %s for album '%s' created.", upload_id, folder_name)
        return upload.to_dict()

    def _get(self, upload_id: str) -> Upload:
        with self._lock:
            upload = self._uploads.get(upload_id)
        if upload is None:
            raise NotFound(f"unknown upload '{upload_id}'.", code="upload.unknown")
        return upload

    def get(self, upload_id: str) -> dict[str, Any]:
        upload = self._get(upload_id)
        with upload.lock:
            return upload.to_dict()

    def list(self) -> list[dict[str, Any]]:
        self._remove_expired()
        with self._lock:
            uploads = list(self._uploads.values())
        return [u.to_dict() for u in uploads]

    def _check_space(self, length: int) -> None:
        free = shutil.disk_usage(self._albums_folder).free
        if free - length < self._min_free_space:
            raise PayloadTooLarge(
                f"not enough free space on the box ({free // 2**20} MB free).",
                code="upload.no_space",
                free=free // 2**20,
            )

    def add_file(self, upload_id: str, filename: str, stream: Readable, length: int) -> TrackInfo:
        """Copy ``length`` bytes from ``stream`` into the upload. Only valid mp3 files are accepted."""
        upload = self._get(upload_id)
        filename = safe_name(filename, "file name")
        if not is_mp3_filename(filename):
            raise BadRequest(f"only mp3 files are supported ('{filename}').", code="upload.only_mp3", filename=filename)
        if filename.lower() == self._playlist_filename.lower():
            raise BadRequest(f"'{filename}' is reserved for the playlist.", code="upload.reserved", filename=filename)
        if length > self._max_file_size:
            raise PayloadTooLarge(
                f"'{filename}' is larger than {self._max_file_size // 2**20} MB.",
                code="upload.file_too_large",
                filename=filename,
                limit=self._max_file_size // 2**20,
            )
        if len(upload.files) >= MAX_FILES and filename not in upload.files:
            raise BadRequest(f"an album may have at most {MAX_FILES} files.", code="upload.too_many", limit=MAX_FILES)
        self._check_space(length)

        target = os.path.join(upload.staging_folder, filename)
        partial = os.path.join(upload.staging_folder, "." + secrets.token_hex(4) + ".part")
        try:
            with open(partial, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise BadRequest(
                            f"upload of '{filename}' incomplete.", code="upload.incomplete", filename=filename
                        )
                    f.write(chunk)
                    remaining -= len(chunk)
            try:
                mp3_info(partial)
            except Exception:  # HeaderNotFoundError and everything mutagen raises for garbage
                raise BadRequest(
                    f"'{filename}' is not a valid mp3 file.", code="upload.not_mp3", filename=filename
                ) from None
            info = read_track(partial)
            info["filename"] = filename
            info["size"] = length
            with upload.lock:
                if self._uploads.get(upload_id) is not upload:  # aborted meanwhile
                    raise NotFound(f"unknown upload '{upload_id}'.", code="upload.unknown")
                os.replace(partial, target)
                upload.files[filename] = info
                upload.last_change = time.monotonic()
        finally:
            if os.path.exists(partial):
                os.remove(partial)
        self._logger.info("upload %s: received '%s' (%s bytes).", upload_id, filename, length)
        return info

    def remove_file(self, upload_id: str, filename: str) -> None:
        upload = self._get(upload_id)
        with upload.lock:
            if filename not in upload.files:  # only names from the list are used as path
                raise NotFound(f"unknown file '{filename}'.")
            os.remove(os.path.join(upload.staging_folder, filename))
            del upload.files[filename]

    def abort(self, upload_id: str) -> None:
        with self._lock:
            upload = self._uploads.pop(upload_id, None)
        if upload is None:
            raise NotFound(f"unknown upload '{upload_id}'.", code="upload.unknown")
        with upload.lock:
            shutil.rmtree(upload.staging_folder, ignore_errors=True)
        self._logger.info("upload %s aborted.", upload_id)

    def finish(self, upload_id: str, order: object = None) -> TargetAlbum:
        """Write the playlist (given order or :func:`playlist_order`), move the folder to the albums folder and add
        the album to the targets."""
        upload = self._get(upload_id)
        with upload.lock:
            if not upload.files:
                raise BadRequest("no files uploaded.", code="upload.empty")
            if order is None:
                order = playlist_order(list(upload.files.values()))
            elif not isinstance(order, list) or sorted(map(str, order)) != sorted(upload.files):
                raise BadRequest("order must contain every uploaded file exactly once.")
            album_folder = self._album_folder(upload.folder_name)
            if os.path.exists(album_folder):
                raise Conflict(
                    f"album folder '{upload.folder_name}' exists already.",
                    code="upload.folder_exists",
                    folder=upload.folder_name,
                )
            os.rename(upload.staging_folder, album_folder)
            # the playlist is written after moving - a scan of the albums folder never sees a half finished album
            targetalbumtools.write_playlist(album_folder, self._playlist_filename, [str(name) for name in order])
            try:
                album = targetalbumtools.add_album_to_targets(
                    self._targets, self._logger, upload.folder_name, self._playlist_filename, upload.title
                )
            except ValueError as e:
                os.remove(os.path.join(album_folder, self._playlist_filename))
                os.rename(album_folder, upload.staging_folder)
                raise BadRequest(f"album cannot be added: {e}", code="upload.add_failed", reason=str(e)) from None
            with self._lock:
                del self._uploads[upload_id]
        self._logger.info("upload %s finished - album [%s] '%s'.", upload_id, album.target_id, upload.folder_name)
        return album
