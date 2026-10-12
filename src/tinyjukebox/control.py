"""Everything that can be done with the box - used by the rest api and the command line; the counterpart of the
hardware buttons and the rfid reader."""

import contextlib
import logging
import os
import re
import secrets
import shutil
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from tinyjukebox import __version__
from tinyjukebox.backup import Backup, Folder
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.data import targetalbumtools
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.common import TargetType, is_unmapped
from tinyjukebox.data.sanitycheck import SanityCheck
from tinyjukebox.data.tag import Tag
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.target import (
    TargetAlbum,
    TargetRadio,
    check_album_title,
    check_station_name,
    check_stream_url,
    is_mp3_filename,
    read_playlist,
)
from tinyjukebox.data.targets import Targets
from tinyjukebox.devices.bluetooth.virtual import VirtualHeadset
from tinyjukebox.errors import BadRequest, Conflict, NotFound, PayloadTooLarge
from tinyjukebox.headset import Headset
from tinyjukebox.images import MAX_IMAGE_SIZE, Images
from tinyjukebox.library.statistics import statistics
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, RFID_STATE, Button, RfidState
from tinyjukebox.metadata import TrackCache, find_cover, read_track, store_cover
from tinyjukebox.rgbled import RGBLed
from tinyjukebox.slots import COMMANDS, Slots
from tinyjukebox.stm.stmoperation import AlbumControl
from tinyjukebox.sync.box import BoxSync
from tinyjukebox.sync.client import HttpError, Unreachable, WrongCertificate
from tinyjukebox.sync.data import JOURNAL_LIMIT
from tinyjukebox.volumemanager import VolumeProfile, Volumes, check_limits

__all__ = ["BUTTONS", "BUTTON_ACTIONS", "BoxControl", "CatalogControl", "is_valid_uid"]

# button name -> button; action -> topic - the same messages the input drivers (and the bluetooth driver) publish
BUTTONS = {button.value: button for button in Button}
BUTTON_ACTIONS = {"press": BUTTON_PRESSED, "hold": BUTTON_HELD, "release": BUTTON_RELEASED}
_VALID_UID = re.compile(r"^[\w:.\-]{1,64}$")


def is_valid_uid(uid: object) -> bool:
    """uid of a real or virtual tag: letters, digits, ':', '.', '-', '_' ('current' is reserved)."""
    return isinstance(uid, str) and bool(_VALID_UID.match(uid)) and uid != "current"


class Operation(Protocol):
    """What the box control needs of :class:`~tinyjukebox.stm.stmoperation.STMOperation`."""

    def get_current_uid(self) -> str | None: ...

    def get_album(self) -> AlbumControl | None: ...

    def get_active_state(self) -> Any: ...

    def recheck_tag(self) -> None: ...

    def test_radio(self, station: TargetRadio) -> None: ...

    def stop_radio_test(self) -> None: ...


def _noop() -> None:
    pass


class _CollectWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class CatalogControl:
    """Operations on tags, albums, radio stations and activities - what a box and the library
    (:mod:`tinyjukebox.library`) have in common.

    Errors are raised as :class:`~tinyjukebox.errors.ApiError` (message for the user). Changes of tags, targets and
    activities are done one at a time (``_lock``); the collections themselves are thread safe as well.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        tags: Tags,
        targets: Targets,
        activities: Activities,
        logger: logging.Logger,
        images: Images | None = None,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._root_logger = logger
        self._config = config
        self._tags = tags
        self._targets = targets
        self._activities = activities
        self.images = images
        self._tracks = TrackCache()
        self._lock = threading.RLock()

    # --- what a box adds -------------------------------------------------------------------------------------------

    def _current_uid(self) -> str | None:
        """uid of the tag on the box."""
        return None

    def _recheck_tag(self) -> None:
        """Evaluate the tag on the box again - what it is linked to changed."""

    def _is_playing(self, target_id: object) -> bool:
        return False

    def _album_changed(self, album_id: str) -> None:
        """The files of an album changed (its playlist was written anew, it got another cover)."""

    def _described(self, album_id: str) -> dict[str, Any]:
        """What the catalog of the library says about the files of an album: ``tracks`` and ``length`` are their
        number and playing time. Empty without a library, or as long as the library does not know."""
        return {}

    def status(self) -> dict[str, Any]:
        """What the page shows all the time."""
        return {"state": None, "tag": None, "album": None, "radio": None}

    @property
    def playlist_filename(self) -> str:
        return str(self._config["playlist-filename"])

    # --- helpers -----------------------------------------------------------------------------------------------------

    def _tag(self, uid: str) -> Tag:
        """Tag by uid - 'current' is the tag on the box."""
        if uid == "current":
            current = self._current_uid()
            if current is None:
                raise NotFound("no tag present.", code="tag.none_present")
            uid = current
        tag = self._tags.tags.get(uid)
        if tag is None:
            raise NotFound(f"unknown tag '{uid}'.", code="tag.unknown", uid=uid)
        return tag

    def _album(self, album_id: object) -> TargetAlbum:
        # no Targets.get_target - it logs every unknown id and the web page asks for the status every second
        album = self._targets.targets.get(str(album_id))
        if not isinstance(album, TargetAlbum):
            raise NotFound(f"unknown album '{album_id}'.", code="album.unknown", id=album_id)
        return album

    def _radio(self, radio_id: object) -> TargetRadio:
        radio = self._targets.targets.get(str(radio_id))
        if not isinstance(radio, TargetRadio):
            raise NotFound(f"unknown radio station '{radio_id}'.", code="radio.unknown", id=radio_id)
        return radio

    @staticmethod
    def _target_id(tag: Tag, target_type: TargetType) -> str | None:
        """id of the linked target if it is of ``target_type``."""
        return None if is_unmapped(tag.target_id) or tag.target_type != target_type else str(tag.target_id)

    def _target_exists(self, target_id: str | None, target_type: TargetType) -> bool:
        target = self._targets.targets.get(target_id) if target_id is not None else None
        return target is not None and target.target_type == target_type

    def _tag_dict(self, tag: Tag) -> dict[str, Any]:
        album_id = self._target_id(tag, TargetType.album)
        radio_id = self._target_id(tag, TargetType.radio)
        result: dict[str, Any] = {
            "uid": tag.tag_uid,
            "name": tag.tag_name,
            "album_id": album_id,
            "album_exists": self._target_exists(album_id, TargetType.album),
            "radio_id": radio_id,
            "radio_exists": self._target_exists(radio_id, TargetType.radio),
            "created": tag.created,
            "present": tag.tag_uid == self._current_uid(),
            "activity": None,
        }
        activity = self._activities.activities.get(str(tag.tag_uid))
        if activity is not None and album_id is not None and str(activity.target_id) == album_id:
            result["activity"] = {
                "track_id": activity.track_id,
                "track_time": activity.track_time,
                "finished": activity.finished,
            }
        return result

    def _linked_tags(self, target_id: object, target_type: TargetType = TargetType.album) -> list[dict[str, Any]]:
        return [
            {"uid": t.tag_uid, "name": t.tag_name}
            for t in self._tags.all()
            if t.target_type == target_type and str(t.target_id) == str(target_id)
        ]

    def _radio_cover(self, radio: TargetRadio) -> str | None:
        """Name of the cover image of the station; None without a cover, also while the image is not here yet."""
        if self.images is None or radio.cover is None:
            return None
        return radio.cover if self.images.has(radio.cover) else None

    def _radio_dict(self, radio: TargetRadio) -> dict[str, Any]:
        return {
            "id": str(radio.target_id),
            "name": radio.name,
            "url": radio.url,
            "cover": self._radio_cover(radio),
            "created": radio.created,
            "tags": self._linked_tags(radio.target_id, TargetType.radio),
        }

    def _album_summary(self, album: TargetAlbum) -> dict[str, Any]:
        tracks = album.get_tracks()
        first = self._tracks.get(tracks[0]) if tracks else {}
        folder = str(album.album_folder)
        return {
            "id": str(album.target_id),
            "folder": folder,
            "playlist": album.playlist,
            "created": album.created,
            "title": album.title or first.get("album") or os.path.basename(folder),
            "artist": first.get("artist"),
            **self._album_size(album, tracks),
            "tags": self._linked_tags(album.target_id),
        }

    def _album_size(self, album: TargetAlbum, tracks: list[str]) -> dict[str, Any]:
        """``track_count`` and ``length`` (playing time in seconds, None if unknown) of an album: as the library
        says, which also knows an album that is not on this box, otherwise read from the files that are here."""
        described = self._described(str(album.target_id))
        if "tracks" in described and "length" in described:
            return {"track_count": described["tracks"], "length": described["length"]}
        lengths = [length for length in (self._tracks.get(track)["length"] for track in tracks) if length is not None]
        return {"track_count": len(tracks), "length": round(sum(lengths), 2) if lengths else None}

    def _album_details(self, album: TargetAlbum) -> dict[str, Any]:
        result = self._album_summary(album)
        tracks = []
        for index, filename in enumerate(album.get_tracks()):
            track = self._tracks.get(filename)
            track["index"] = index
            track["path"] = os.path.relpath(filename, album.full_album_folder)
            tracks.append(track)
        result["tracks"] = tracks
        return result

    def _inside_albums_folder(self, path: str) -> bool:
        """True if ``path`` (symlinks resolved) is a folder below the albums folder - not the albums folder itself."""
        root = os.path.realpath(self._targets.root_folder)
        folder = os.path.realpath(path)
        return folder != root and os.path.commonpath([root, folder]) == root

    # --- tags --------------------------------------------------------------------------------------------------------

    def list_tags(self, unmapped_only: bool = False) -> list[dict[str, Any]]:
        tags = [self._tag_dict(t) for t in self._tags.all()]
        if unmapped_only:
            tags = [t for t in tags if t["album_id"] is None and t["radio_id"] is None]
        return sorted(tags, key=lambda t: ((t["name"] or "").lower(), t["uid"]))

    def get_tag(self, uid: str) -> dict[str, Any]:
        return self._tag_dict(self._tag(uid))

    def current_unlinked_tag(self) -> str | None:
        """uid of the tag on the box if it is known and not linked yet."""
        uid = self._current_uid()
        tag = self._tags.tags.get(uid) if uid is not None else None
        return uid if tag is not None and is_unmapped(tag.target_id) else None

    def create_tag(
        self,
        uid: str | None = None,
        name: str | None = None,
        album_id: str | None = None,
        radio_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a tag without placing it on the reader (e.g. for tests without hardware). A random uid in the format
        of the rc522 reader is generated if none is given. The tag is linked to the album or radio station if
        given."""
        with self._lock:
            if not uid:
                uid = ":".join(str(secrets.randbelow(256)) for _ in range(4))
                while uid in self._tags.tags:
                    uid = ":".join(str(secrets.randbelow(256)) for _ in range(4))
            if not is_valid_uid(uid):
                raise BadRequest("invalid uid (letters, digits, ':', '.', '-', '_').", code="tag.invalid_uid")
            if uid in self._tags.tags:
                raise Conflict(f"tag '{uid}' exists already.", code="tag.exists", uid=uid)
            if album_id not in (None, "") and radio_id not in (None, ""):
                raise BadRequest("a tag is linked either to an album or to a radio station.")
            if album_id not in (None, ""):
                self._album(album_id)
            if radio_id not in (None, ""):
                self._radio(radio_id)
            tag = self._tags.add_tag(uid)
            if name:
                tag.set_tag_name(str(name).strip())
            if album_id not in (None, ""):
                self._tags.assign_target(uid, TargetType.album, str(album_id))
            if radio_id not in (None, ""):
                self._tags.assign_target(uid, TargetType.radio, str(radio_id))
            self._logger.info("created tag '%s'.", uid)
            return self._tag_dict(tag)

    def set_tag_name(self, uid: str, name: object) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise BadRequest("name must not be empty.", code="name.empty")
        with self._lock:
            tag = self._tag(uid)
            tag.set_tag_name(name.strip())
            return self._tag_dict(tag)

    def link_tag_album(self, uid: str, album_id: object) -> dict[str, Any]:
        """Link the tag (uid or 'current') with the album. If this tag is on the box, the new album starts right
        away - no need to remove and replace the tag."""
        with self._lock:
            tag = self._tag(uid)
            album = self._album(album_id)
            self._logger.info("link tag '%s' with album '%s'.", tag.tag_uid, album.target_id)
            self._tags.assign_target(str(tag.tag_uid), TargetType.album, str(album.target_id))
            if tag.tag_uid == self._current_uid():
                self._recheck_tag()
            return self._tag_dict(tag)

    def link_tag_radio(self, uid: str, radio_id: object) -> dict[str, Any]:
        """Link the tag (uid or 'current') with the radio station - it starts right away if the tag is on the box."""
        with self._lock:
            tag = self._tag(uid)
            radio = self._radio(radio_id)
            self._logger.info("link tag '%s' with radio station '%s'.", tag.tag_uid, radio.target_id)
            self._tags.assign_target(str(tag.tag_uid), TargetType.radio, str(radio.target_id))
            if tag.tag_uid == self._current_uid():
                self._recheck_tag()
            return self._tag_dict(tag)

    def unlink_tag(self, uid: str) -> dict[str, Any]:
        with self._lock:
            tag = self._tag(uid)
            tag.set_target_id(-1)
            if tag.tag_uid == self._current_uid():
                self._recheck_tag()
            return self._tag_dict(tag)

    def remove_tag(self, uid: str) -> None:
        """Remove the tag with its activity."""
        with self._lock:
            tag = self._tag(uid)
            tag_uid = str(tag.tag_uid)
            if tag_uid in self._activities.activities:
                self._activities.delete_activity(tag_uid)
            self._tags.delete_tag(tag_uid)

    def _images(self) -> Images:
        if self.images is None:
            raise NotFound("no images folder.")
        return self.images

    # --- albums ------------------------------------------------------------------------------------------------------

    def list_albums(self, unmapped_only: bool = False) -> list[dict[str, Any]]:
        albums = [self._album_summary(a) for a in self._targets.albums()]
        if unmapped_only:
            albums = [a for a in albums if not a["tags"]]
        return sorted(albums, key=lambda a: (len(a["id"]), a["id"]))

    def get_album(self, album_id: object) -> dict[str, Any]:
        album = self._album(album_id)
        result = self._album_details(album)
        passed, problems = album.sanity_check()
        prefix = f"{album.full_album_folder}/"
        result["problems"] = (
            [] if passed else [p.replace("sanity_check - ", "").replace(prefix, "") for p in problems.splitlines()]
        )
        return result

    def album_text(self, album_id: str) -> str:
        """One line description of the album (command line)."""
        return self._album(album_id).to_text()

    def rebuild_playlist(self, album_id: object) -> dict[str, Any]:
        """Write a new playlist with all mp3 files of the album folder (order: id3 track numbers, otherwise natural
        file name order). The old playlist is kept as <playlist>.bak."""
        with self._lock:
            album = self._album(album_id)
            folder = str(album.full_album_folder)
            try:
                files = sorted(f for f in os.listdir(folder) if is_mp3_filename(f))
            except FileNotFoundError:
                raise NotFound(
                    f"album folder '{album.album_folder}' not found.",
                    code="album.folder_missing",
                    folder=album.album_folder,
                ) from None
            if not files:
                raise BadRequest("album folder contains no mp3 files.", code="album.no_mp3")
            order = targetalbumtools.playlist_order(
                [dict(read_track(os.path.join(folder, f)), filename=f) for f in files]
            )
            playlist = os.path.join(folder, str(album.playlist))
            if os.path.exists(playlist):
                os.replace(playlist, playlist + ".bak")
            targetalbumtools.write_playlist(folder, str(album.playlist), order)
            album.get_tracks(force_update=True)
            self._logger.info("rebuilt playlist of album '%s': %s tracks.", album.target_id, len(order))
            self._album_changed(str(album.target_id))
            return self.get_album(album_id)

    def get_cover(self, album_id: object) -> tuple[bytes, str]:
        album = self._album(album_id)
        cover = find_cover(str(album.full_album_folder), album.get_tracks())
        if cover is None:
            raise NotFound(f"album '{album_id}' has no cover.")
        return cover

    def set_cover(self, album_id: object, data: bytes) -> dict[str, Any]:
        """Make the image ``data`` (jpeg or png) the cover of the album. It is stored in the album folder, so it
        belongs to the files of the album."""
        if len(data) > MAX_IMAGE_SIZE:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        with self._lock:
            album = self._album(album_id)
            folder = str(album.full_album_folder)
            if not os.path.isdir(folder):
                raise NotFound(
                    f"album folder '{album.album_folder}' not found.",
                    code="album.folder_missing",
                    folder=album.album_folder,
                )
            try:
                name = store_cover(folder, data)
            except ValueError as e:
                raise BadRequest(str(e)) from None
            self._logger.info("album '%s' has a new cover: %s (%s bytes).", album.target_id, name, len(data))
            self._album_changed(str(album.target_id))
            return self._album_summary(album)

    def update_album(self, album_id: object, title: object) -> dict[str, Any]:
        """Change the title of the album."""
        with self._lock:
            album = self._album(album_id)
            try:
                album.set_title(title)
            except ValueError as e:
                raise BadRequest(str(e)) from None
            return self._album_summary(album)

    def proposed_title(self, folder: str) -> str:
        """A title for the album in ``folder`` (relative to the albums folder), which is not added yet: the album
        that its first track names, otherwise the name of the folder."""
        path = os.path.join(self._targets.root_folder, folder)
        try:
            entries = read_playlist(os.path.join(path, self.playlist_filename), self.playlist_filename)
        except OSError:
            entries = []
        album = self._tracks.get(os.path.join(path, entries[0])).get("album") if entries else None
        try:
            return check_album_title(album)
        except ValueError:
            return os.path.basename(os.path.normpath(folder))

    def add_album(self, folder: object, title: object = None) -> dict[str, Any]:
        """Add the album in ``folder`` (relative to the albums folder) with the title ``title``."""
        if not isinstance(folder, str) or not folder.strip():
            raise BadRequest("folder must be provided.", code="album.folder_required")
        try:
            title = check_album_title(title)
        except ValueError:
            raise BadRequest("title must be provided.", code="album.title_required") from None
        folder = os.path.normpath(folder.strip())
        if os.path.isabs(folder) or not self._inside_albums_folder(os.path.join(self._targets.root_folder, folder)):
            raise BadRequest("folder must be relative to the albums folder.", code="album.folder_relative")
        with self._lock:
            if any(a.album_folder == folder for a in self._targets.albums()):
                raise Conflict(f"album folder '{folder}' is already known.", code="album.folder_known", folder=folder)
            try:
                album = targetalbumtools.add_album_to_targets(
                    self._targets, self._logger, folder, self.playlist_filename, title
                )
            except ValueError as e:
                raise BadRequest(f"adding album failed: {e}", code="album.add_failed", reason=str(e)) from None
            return self._album_summary(album)

    def album_folders(self) -> list[str]:
        """All folders with a playlist in the albums folder - known albums and new ones."""
        return targetalbumtools.scan_for_album_folders(self._logger, self._targets.root_folder, self.playlist_filename)

    def new_album_folders(self) -> list[str]:
        return targetalbumtools.scan_for_new_albums(
            self._targets, self._logger, self._targets.root_folder, self.playlist_filename
        )

    def new_albums(self) -> list[dict[str, str]]:
        """The folders that are not added as album yet, each with a proposal for its title."""
        return [{"folder": folder, "title": self.proposed_title(folder)} for folder in self.new_album_folders()]

    def add_new_albums(self, titles: object = None) -> dict[str, list[dict[str, Any]]]:
        """Add all folders that are not added as album yet. ``titles`` maps folders to their titles; a folder that
        it does not name gets the proposed title."""
        if titles is not None and not isinstance(titles, dict):
            raise BadRequest("titles must map folders to titles.")
        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        with self._lock:
            for folder in self.new_album_folders():
                try:
                    title = check_album_title((titles or {}).get(folder) or self.proposed_title(folder))
                    album = targetalbumtools.add_album_to_targets(
                        self._targets, self._logger, folder, self.playlist_filename, title
                    )
                    added.append(self._album_summary(album))
                except ValueError as e:
                    skipped.append({"folder": folder, "error": str(e)})
        return {"added": added, "skipped": skipped}

    def delete_album(self, album_id: object, delete_files: bool = False) -> None:
        with self._lock:
            album = self._album(album_id)
            linked = self._linked_tags(album.target_id)
            if linked:
                raise Conflict("album is linked to tags - unlink them first.", tags=linked, code="album.linked")
            if self._is_playing(album.target_id):
                raise Conflict("album is playing.", code="album.playing")
            self._targets.delete_target(str(album.target_id))
            # positions in the deleted album (of tags unlinked before) - nothing can resume them any more
            for uid, activity in self._activities.all().items():
                if str(activity.target_id) == str(album.target_id):
                    self._activities.delete_activity(uid)
            folder = str(album.full_album_folder)
            if delete_files and self._inside_albums_folder(folder) and not os.path.islink(folder):
                shutil.rmtree(os.path.realpath(folder), ignore_errors=True)
                self._logger.info("deleted album folder '%s'.", folder)

    # --- radio stations ----------------------------------------------------------------------------------------------

    def list_radios(self) -> list[dict[str, Any]]:
        radios = [self._radio_dict(r) for r in self._targets.radios()]
        return sorted(radios, key=lambda r: (r["name"].casefold(), len(r["id"]), r["id"]))

    def get_radio(self, radio_id: object) -> dict[str, Any]:
        return self._radio_dict(self._radio(radio_id))

    def add_radio(self, name: object, url: object) -> dict[str, Any]:
        """Add a radio station (``url``: http(s) url of the stream or of an m3u/pls playlist)."""
        with self._lock:
            try:
                radio = self._targets.add_target({"target-type": TargetType.radio.name, "name": name, "url": url})
            except ValueError as e:
                raise BadRequest(str(e), code="radio.invalid") from None
            self._logger.info("added radio station %s.", radio.to_text())
            assert isinstance(radio, TargetRadio)  # noqa: S101 - created from a radio dict
            return self._radio_dict(radio)

    def update_radio(self, radio_id: object, name: object = None, url: object = None) -> dict[str, Any]:
        """Change name and/or url (``None``: unchanged). A station that is playing starts again with the new url."""
        with self._lock:
            radio = self._radio(radio_id)
            old_url = radio.url
            try:
                radio.update(
                    None if name is None else check_station_name(name), None if url is None else check_stream_url(url)
                )
            except ValueError as e:
                raise BadRequest(str(e), code="radio.invalid") from None
            if radio.url != old_url and self._is_playing(radio.target_id):
                self._recheck_tag()
            return self._radio_dict(radio)

    def get_radio_cover(self, radio_id: object) -> tuple[bytes, str]:
        radio = self._radio(radio_id)
        cover = self._radio_cover(radio)
        if cover is None:
            raise NotFound(f"radio station '{radio_id}' has no cover.")
        return self._images().image(cover)

    def set_radio_cover(self, radio_id: object, data: bytes) -> dict[str, Any]:
        """Make the image ``data`` (jpeg, png or webp) the cover of the station. It is stored in the images folder
        and replaces the cover the station had."""
        with self._lock:
            radio = self._radio(radio_id)
            old = radio.cover
            radio.set_cover(self._images().add(data))
            self._logger.info("radio station '%s' has a new cover: %s.", radio.target_id, radio.cover)
            self._drop_radio_cover(old)
            return self._radio_dict(radio)

    def _drop_radio_cover(self, cover: str | None) -> None:
        if cover is not None and self.images is not None:
            with contextlib.suppress(NotFound):
                self.images.delete(cover)

    def delete_radio(self, radio_id: object) -> None:
        with self._lock:
            radio = self._radio(radio_id)
            linked = self._linked_tags(radio.target_id, TargetType.radio)
            if linked:
                raise Conflict("radio station is linked to tags - unlink them first.", tags=linked, code="radio.linked")
            if self._is_playing(radio.target_id):
                raise Conflict("radio station is playing.", code="radio.playing")
            self._targets.delete_target(str(radio.target_id))
            self._drop_radio_cover(radio.cover)
            self._logger.info("deleted radio station %s.", radio.to_text())

    # --- activities / maintenance ------------------------------------------------------------------------------------

    def list_activities(self) -> list[dict[str, Any]]:
        return [
            {
                "tag_uid": uid,
                "album_id": str(a.target_id),
                "track_id": a.track_id,
                "track_time": a.track_time,
                "finished": a.finished,
            }
            for uid, a in sorted(self._activities.all().items())
        ]

    def rebuild_activities(self) -> list[dict[str, Any]]:
        with self._lock:
            self._activities.rebuild_activities()
        return self.list_activities()

    def sanity_checks(self) -> dict[str, Any]:
        """Run all sanity checks - returns the result and all warnings."""
        collector = _CollectWarnings()
        self._root_logger.addHandler(collector)
        try:
            passed = SanityCheck(self._logger, self._targets, self._tags, self._activities).sanity_check()
        finally:
            self._root_logger.removeHandler(collector)
        return {"passed": passed, "messages": collector.messages}

    def backup(self, albums: bool = False) -> Backup:
        """A backup of the data as it is right now, with the audio files of the albums or without
        (:mod:`tinyjukebox.backup`). Nothing secret is part of it."""
        raise NotImplementedError


class BoxControl(CatalogControl):
    """Everything that can be done with the box: the catalog, and status, player, volume, headset, virtual reader
    and virtual buttons."""

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        volumes: Volumes,
        stmoperation: Operation,
        tags: Tags,
        targets: Targets,
        activities: Activities,
        logger: logging.Logger,
        rgbled: RGBLed | None = None,
        images: Images | None = None,
        headset: Headset | None = None,
        virtual_headset: VirtualHeadset | None = None,
        sync: BoxSync | None = None,
        slots: Slots | None = None,
        data_files: Sequence[str] = (),
    ) -> None:
        """``headset``: the bluetooth headset of the box (``None``: no bluetooth); ``virtual_headset``: the headset
        of a box without hardware; ``sync``: the synchronization with the library; ``slots``: which version runs;
        ``data_files``: the json files of the box, for its backup."""
        super().__init__(config, tags, targets, activities, logger, images)
        self._data_files = list(data_files)
        self._pubsub_client = pubsub_client
        self._volumes = volumes
        self._headset = headset
        self._virtual_headset = virtual_headset
        self._stmoperation = stmoperation
        self._rgbled = rgbled
        self.sync = sync
        self._slots = slots

    def _current_uid(self) -> str | None:
        return self._stmoperation.get_current_uid()

    def _recheck_tag(self) -> None:
        self._stmoperation.recheck_tag()

    def _album_changed(self, album_id: str) -> None:
        if self.sync is not None:
            self.sync.album_changed(album_id)

    def _described(self, album_id: str) -> dict[str, Any]:
        if self.sync is None or not self.sync.attached:
            return {}
        return self.sync.data.album(self.sync.data.final(album_id)) or {}

    # --- status ------------------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        uid = self._stmoperation.get_current_uid()
        tag = self._tags.tags.get(uid) if uid is not None else None
        state = self._stmoperation.get_active_state()
        result: dict[str, Any] = {
            "state": getattr(state, "value", state),
            "tag": self._tag_dict(tag) if tag is not None else ({"uid": uid, "name": None} if uid else None),
            "volume": self.get_volume(),
            "led": self._rgbled.get_state() if self._rgbled is not None else None,
            "bluetooth": self.bluetooth_info(),
            "album": None,
            "radio": None,
        }
        if self.sync is not None and self.sync.attached:
            result["sync"] = {
                "connected": self.sync.link.connected,
                "blocked": self.sync.blocked,
                "fetching": self.sync.files.fetching(),
            }
        player = self._stmoperation.get_album()
        if player is not None and player.get_info().get("target_type") == "radio":
            info = player.get_info()
            station = self._targets.targets.get(str(info["target_id"]))  # none for a test
            result["radio"] = {
                "id": str(info["target_id"]),
                "name": info["name"],
                "url": info["url"],
                "cover": self._radio_cover(station) if isinstance(station, TargetRadio) else None,
                "state": info["state"],
                "error": info["error"],
                "test": getattr(state, "value", state) == "test_radio",
            }
        elif player is not None:
            info = player.get_info()
            try:
                album = self._album_details(self._album(info["target_id"]))
            except NotFound:
                album = {"id": str(info["target_id"]), "folder": info["album_folder"], "tracks": []}
            track = info["track"]
            album["playback"] = {
                "state": info["state"],
                "track_id": track["track_id"],
                "position": track["pos"],
                "length": track["length"],
            }
            result["album"] = album
        return result

    def album_info(self) -> dict[str, Any] | None:
        """Raw information of the playing album or radio station (see STMPlayAlbum.get_info, PlayRadio.get_info) or
        None."""
        player = self._stmoperation.get_album()
        return player.get_info() if player is not None else None

    def state(self) -> Any:
        return self._stmoperation.get_active_state()

    # --- library ---------------------------------------------------------------------------------------------------

    def _sync(self) -> BoxSync:
        if self.sync is None:
            raise NotFound("this box has no synchronization.")
        return self.sync

    def library_info(self) -> dict[str, Any]:
        """The state of the synchronization with the library (docs/developer/library.md)."""
        return self._sync().info()

    def set_library(self, url: object, bootstrap: object = None) -> dict[str, Any]:
        """Enter the address of the library (``https://host:port``) and, for the first contact, the bootstrap
        password. A box that has not worked with a library before does so from its next start."""
        if not isinstance(url, str) or not url.strip().startswith("https://"):
            raise BadRequest("the address of the library must start with https://.", code="library.address_https")
        if bootstrap is not None and not isinstance(bootstrap, str):
            raise BadRequest("bootstrap must be the bootstrap password.")
        self._sync().set_library(url.strip().rstrip("/"), bootstrap or None)
        self._logger.info("library: %s", url.strip())
        return self.library_info()

    def unlink_library(self) -> dict[str, Any]:
        """Leave the library: the box keeps what is on its card and works for itself. Changes that the library has
        not confirmed are not delivered any more, and an album that is not on the card cannot be fetched. The library
        is not asked and need not be reachable."""
        if not self._sync().library and not self._sync().attached:
            raise Conflict("this box is not linked to a library.", code="box.not_linked")
        self._sync().unlink()
        self._logger.info("library: unlinked")
        return self.library_info()

    def set_box_name(self, name: object) -> dict[str, Any]:
        """The name of this box for people - the pages show it; nothing else depends on it."""
        if not isinstance(name, str) or not name.strip():
            raise BadRequest("name must not be empty.", code="name.empty")
        if not self._sync().attached:
            raise Conflict("the box has a name as soon as it works with a library.", code="box.name_needs_library")
        self._sync().set_name(name.strip())
        return self.library_info()

    def statistics(self) -> dict[str, Any]:
        """What was played on this box (docs/developer/library.md, section 7). The library keeps the journal of a
        box and counts it (``origin``: library); a box without a library counts the lines it has kept itself, the
        last ``limit`` ones (``origin``: box). ``missing`` says why there is nothing to show: the library cannot be
        reached, or it is older than this box and does not answer this question yet."""
        sync = self._sync()
        if sync.attached:
            try:
                return {"origin": "library", "missing": None, **sync.link.statistics()}
            except HttpError as error:
                return {"origin": "library", "missing": "library_too_old" if error.status == 404 else "unreachable"}
            except (Unreachable, WrongCertificate):
                return {"origin": "library", "missing": "unreachable"}
        box = sync.data.box_id
        lines = [{**line, "box": box, "discarded": False} for line in sync.data.journal_lines(None)]
        with self._lock:
            tags = {str(tag.tag_uid): {"tag": {"name": tag.tag_name}} for tag in self._tags.all()}
            targets: dict[str, Any] = {
                str(album.target_id): {"title": album.title, "album-folder": os.path.basename(str(album.album_folder))}
                for album in self._targets.albums()
            }
            targets.update({str(radio.target_id): {"name": radio.name} for radio in self._targets.radios()})
        counted = statistics(lines, [], tags, targets, {box: box})
        return {"origin": "box", "missing": None, "limit": JOURNAL_LIMIT, "box": box, **counted}

    # --- versions of the box software ----------------------------------------------------------------------------

    def backup(self, albums: bool = False) -> Backup:
        """The json files, the covers of the stations and, with ``albums``, the albums folder. Not what the box
        exchanges with the library and not its identity: a box that is set up again is a new box for the library."""
        data = []
        with self._lock:
            for filename in self._data_files:
                with contextlib.suppress(FileNotFoundError), open(filename, "rb") as infile:
                    data.append((f"json/{os.path.basename(filename)}", infile.read()))
        folders = [Folder("images", self.images.folder)] if self.images is not None else []
        if albums:
            folders.append(Folder("albums", self._targets.root_folder))
        return Backup("box", albums, data, folders)

    def system_info(self) -> dict[str, Any]:
        """Running version, slots, update mode, states of the versions and the last switches
        (docs/developer/library.md, section 6). ``available``: the box was set up with slots."""
        available = self._slots is not None and self._slots.available
        status = self._slots.status() if self._slots is not None and available else {}
        library = self.sync.library if self.sync is not None else ""
        return {"available": available, "version": __version__, "library": library, **status}

    def system_command(self, command: object, arguments: object, by: str) -> dict[str, Any]:
        """``check``, ``update``, ``revert``, ``use``, ``fallback``, ``promote``, ``mode`` or ``skip`` of
        ``tinyjukebox-slot`` with its arguments, e.g. ``update --now`` - returns its exit status and what it printed."""
        if self._slots is None or not self._slots.available:
            raise Conflict("this box was not set up with slots - its version is changed with the playbook.")
        if command not in COMMANDS:
            raise NotFound(f"unknown command '{command}' - valid: {', '.join(COMMANDS)}.")
        if not isinstance(arguments, list) or not all(isinstance(argument, str) for argument in arguments):
            raise BadRequest("arguments must be a list of texts.")
        if any(argument in ("--root", "--by") for argument in arguments):
            raise BadRequest("--root and --by are not for the web page.")
        self._logger.info("tinyjukebox-slot %s %s (%s)", command, " ".join(arguments), by)
        result = self._slots.command(str(command), ["--yes", *arguments], by)
        if self.sync is not None:
            self.sync.tell()  # the library learns at once what changed, e.g. the update mode
        return result

    # --- virtual rfid reader -----------------------------------------------------------------------------------------

    def _publish_rfid(self, uid: str | None) -> None:
        self._pubsub_client.publish(RFID_STATE, RfidState(uid))

    def place_tag(self, uid: object) -> None:
        """Publish the message of the rfid driver - the box reacts as if the tag had been placed. Unknown uids are
        added as unmapped tags by the box - exactly like new real tags."""
        if not is_valid_uid(uid):
            raise BadRequest("invalid uid.")
        self._logger.info("virtual tag '%s' placed.", uid)
        self._publish_rfid(str(uid))

    def remove_placed_tag(self) -> None:
        self._logger.info("virtual tag removed.")
        self._publish_rfid(None)

    # --- radio test --------------------------------------------------------------------------------------------------

    def test_radio(self, url: object, name: object = None) -> dict[str, Any]:
        """Play ``url`` (a stream or m3u/pls playlist - saved as station or not) for a test. Only while no tag is on
        the box - a tag placed during the test ends it; the test also ends by itself after a minute."""
        try:
            url = check_stream_url(url)
            name = url if name in (None, "") else check_station_name(name)
        except ValueError as e:
            raise BadRequest(str(e), code="radio.invalid") from None
        station = TargetRadio(
            {"target-id": "test", "target-type": TargetType.radio.name, "name": name, "url": url}, self._logger, _noop
        )
        try:
            self._stmoperation.test_radio(station)
        except RuntimeError as e:
            raise Conflict(f"radio test not possible: {e}.", code="radio.test_impossible") from None
        self._logger.info("radio test of '%s' (%s).", name, url)
        return {"name": name, "url": url}

    def stop_radio_test(self) -> None:
        if getattr(self._stmoperation.get_active_state(), "value", None) != "test_radio":
            raise Conflict("no radio test running.")
        self._stmoperation.stop_radio_test()

    def _is_playing(self, target_id: object) -> bool:
        player = self._stmoperation.get_album()
        return player is not None and str(player.get_info()["target_id"]) == str(target_id)

    # --- player / volume / buttons -----------------------------------------------------------------------------------

    def _player(self) -> AlbumControl:
        player = self._stmoperation.get_album()
        if player is None:
            raise Conflict(
                "no album is playing.", code="player.no_album"
            )  # a radio station only ends a pause with next/prev
        return player

    def track_next(self) -> None:
        self._player().cmd_next_handler()

    def track_prev(self) -> None:
        self._player().cmd_prev_handler()

    def restart_album(self) -> None:
        self._player().cmd_restart_album()

    def pause(self) -> None:
        """Pause the album (the position is kept) or the radio station - the tag stays on the box."""
        self._player().cmd_pause()

    def play(self) -> None:
        """End the pause."""
        self._player().cmd_play()

    def get_volume(self) -> dict[str, Any]:
        """The active volume (``output``: what the buttons change) and both profiles with start value and maximum."""
        active = self._volumes.manager()
        profiles = {}
        for profile in VolumeProfile:
            manager = self._volumes.manager(profile)
            profiles[profile.value] = {
                "volume": manager.get_volume(),
                "default_volume": manager.default_volume,
                "max_volume": manager.max_volume,
            }
        return {
            "volume": active.get_volume(),
            "max_volume": active.max_volume,
            "default_volume": active.default_volume,
            "step": active.volume_step,
            "output": self._volumes.active.value,
            "profiles": profiles,
        }

    def set_volume(self, volume: object) -> dict[str, Any]:
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 100:
            raise BadRequest("volume must be a number between 0 and 100.")
        self._volumes.set_volume(volume)
        return self.get_volume()

    def set_volume_limits(self, profile: object, default_volume: object, max_volume: object) -> dict[str, Any]:
        """Change start value and maximum of ``profile`` (``speaker``/``headphones``) - kept over restarts. A
        current volume above the new maximum is lowered at once."""
        if profile not in tuple(VolumeProfile):
            raise NotFound(f"unknown volume '{profile}' - valid: {', '.join(VolumeProfile)}.")
        try:
            self._volumes.set_limits(VolumeProfile(str(profile)), *check_limits(default_volume, max_volume))
        except ValueError as e:
            raise BadRequest(str(e)) from None
        self._logger.info("volume limits of %s: start %s, maximum %s.", profile, default_volume, max_volume)
        return self.get_volume()

    # --- bluetooth headset -------------------------------------------------------------------------------------------

    def bluetooth_info(self) -> dict[str, Any]:
        """The paired headset (``available``: there is a bluetooth driver; ``virtual``: box without hardware) with
        start value and maximum of its volume."""
        headset = self._headset
        info: dict[str, Any] = (
            headset.info()
            if headset is not None
            else {"available": False, "virtual": False, "device": None, "connected": False, "battery": None,
                  "volume": None, "pairing": False, "pairing_result": None}
        )  # fmt: skip
        manager = self._volumes.manager(VolumeProfile.HEADPHONES)
        info["default_volume"] = manager.default_volume
        info["max_volume"] = manager.max_volume
        return info

    def _available_headset(self) -> Headset:
        if self._headset is None or self._headset.state is None:
            raise Conflict("bluetooth is not available.")
        return self._headset

    def bluetooth_pair(self) -> dict[str, Any]:
        """Search a headset in pairing mode (up to a minute) and pair it - it replaces the paired one."""
        headset = self._available_headset()
        if headset.info()["pairing"]:
            raise Conflict("a search is running already.", code="bluetooth.searching")
        headset.pair()
        return self.bluetooth_info()

    def bluetooth_unpair(self) -> None:
        """Remove the paired headset - a playing album pauses."""
        headset = self._available_headset()
        if headset.info()["device"] is None:
            raise Conflict("no headset is paired.", code="bluetooth.not_paired")
        headset.unpair()

    def virtual_headset(self, connected: object = None, button: object = None) -> dict[str, Any]:
        """The virtual headset of a box without hardware: switch it on/off (``connected``) or press one of its
        buttons."""
        virtual = self._virtual_headset
        if virtual is None:
            raise NotFound("no virtual headset - the box runs with hardware.")
        if connected is not None:
            if not isinstance(connected, bool):
                raise BadRequest("connected must be true or false.")
            if virtual.state.device is None:
                raise Conflict("the virtual headset is not paired.")
            virtual.switch(connected)
        if button is not None:
            if button not in BUTTONS:
                raise BadRequest(f"unknown button '{button}' - valid: {', '.join(BUTTONS)}.")
            if not virtual.state.connected:
                raise Conflict("the virtual headset is not connected.")
            virtual.press(BUTTONS[str(button)])
        return self.bluetooth_info()

    def press_button(self, button: str, action: object = "press") -> None:
        """Publish the message of the hardware button - the box reacts exactly as if it had been pressed."""
        if button not in BUTTONS:
            raise NotFound(f"unknown button '{button}' - valid: {', '.join(BUTTONS)}.")
        if not isinstance(action, str) or action not in BUTTON_ACTIONS:
            raise BadRequest(f"unknown action '{action}' - valid: {', '.join(BUTTON_ACTIONS)}.")
        topic = BUTTON_ACTIONS[action]
        self._logger.info("button %s (%s) -> %s", button, action, topic)
        self._pubsub_client.publish(topic, BUTTONS[button])
