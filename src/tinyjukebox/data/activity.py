"""Activities: the playback position of a tag's target (where to resume when the tag is placed again)."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import TargetType, UpdateJson, is_unmapped

__all__ = ["AActivity", "ActivityAlbum"]

_REQUIRED_ALBUM_KEYS = ("track-id", "track-time", "album-finished")


class AActivity(ABC):
    """Base class of all activities."""

    def __init__(
        self,
        tag_uid: str,
        target_id: str | int,
        target_type: TargetType,
        update_json: UpdateJson | None,
        logger: logging.Logger,
    ) -> None:
        self._logger = get_child(logger, f"{type(self).__name__}.{tag_uid}-{target_id}")
        self.tag_uid = tag_uid
        self.target_id = target_id
        self.target_type = target_type
        if target_id is None or not str(target_id):
            self._logger.error("__init__ - target id must not be empty.")
            raise ValueError("target id must not be empty.")
        if update_json is None or not callable(update_json):
            self._logger.error("__init__ - update_json method not provided")
            raise ValueError("update_json method not provided.")
        self._update_json = update_json

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...

    @abstractmethod
    def to_text(self) -> str: ...

    @abstractmethod
    def sanity_check(self) -> bool: ...


class ActivityAlbum(AActivity):
    """Entry of the activities json file - the position in an album or an entry of Spotify::

    {"tag-uid": "1:2:3:4", "target-id": "7", "track-id": 2, "track-time": 31.4, "album-finished": false,
     "target-type": "album"}

    An entry of Spotify has ``"target-type": "spotify"`` and also ``track-uri``, the address of its track.
    """

    def __init__(self, activity: dict[str, Any] | None, update_json: UpdateJson | None, logger: logging.Logger) -> None:
        if not isinstance(activity, dict) or "tag-uid" not in activity or "target-id" not in activity:
            logger.error("ActivityAlbum.__init__ - activity must be a dict with tag-uid and target-id: %s", activity)
            raise ValueError("activity must be a dict with tag-uid and target-id.")
        target_type = TargetType.spotify if activity.get("target-type") == TargetType.spotify.name else TargetType.album
        super().__init__(activity["tag-uid"], activity["target-id"], target_type, update_json, logger)
        self.load(activity)

    def load(self, activity: dict[str, Any]) -> None:
        """Take over target and position of ``activity`` (an entry of the activities json file) - nothing is
        written."""
        missing = [key for key in _REQUIRED_ALBUM_KEYS if key not in activity]
        if missing:
            self._logger.error("__init__ - activity misses %s (%s).", missing, activity)
            raise ValueError(f"activity misses {missing}.")
        self.target_id = activity["target-id"]
        self.track_id: int = activity["track-id"]
        self.track_time: float = activity["track-time"]
        self.finished: bool = activity["album-finished"]
        self.track_uri: str | None = activity.get("track-uri")

    def update(self, track_id: int, track_time: float, album_finished: bool, track_uri: str | None = None) -> None:
        """Store the new position - the json file is written only if something changed."""
        new = (track_id, track_time, album_finished, track_uri)
        if new == (self.track_id, self.track_time, self.finished, self.track_uri):
            self._logger.debug("update - unchanged")
            return
        self.track_id, self.track_time, self.finished, self.track_uri = new
        self._update_json()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tag-uid": self.tag_uid,
            "target-id": self.target_id,
            "track-id": self.track_id,
            "track-time": self.track_time,
            "album-finished": self.finished,
            "target-type": self.target_type.name,
        }
        if self.track_uri is not None:
            result["track-uri"] = self.track_uri
        return result

    def to_text(self) -> str:
        return (
            f"target id: {self.target_id}, track id: {self.track_id}, time: {self.track_time}, "
            f"finished: {self.finished}"
        )

    def sanity_check(self) -> bool:
        if self.track_time is not None and self.track_time < 0:
            self._logger.warning("sanity_check - track time < 0 (%s)", self.track_time)
            return False
        if self.track_id is not None and self.track_id < 0:
            self._logger.warning("sanity_check - track id < 0 (%s)", self.track_id)
            return False
        finished: object = self.finished  # may have been set to anything
        if not isinstance(finished, bool):
            self._logger.warning("sanity_check - finished is not a bool (%s).", type(self.finished))
            return False
        return True

    @classmethod
    def create_activity(cls, tag: Any, update_json: UpdateJson | None, logger: logging.Logger) -> "ActivityAlbum":
        """New activity (start of the album) for a linked tag. :raises ValueError: for missing or unlinked tags."""
        target_id = getattr(tag, "target_id", None)
        if tag is None or is_unmapped(target_id) or not hasattr(tag, "tag_uid"):
            logger.error("ActivityAlbum.create_activity - tag must be linked to a target: %s", tag)
            raise ValueError("tag must be linked to a target.")
        activity = {
            "tag-uid": tag.tag_uid,
            "target-id": target_id,
            "track-id": 0,
            "track-time": 0,
            "album-finished": False,
            "target-type": TargetType.spotify.name
            if getattr(tag, "target_type", None) == TargetType.spotify
            else TargetType.album.name,
        }
        return ActivityAlbum(activity, update_json, logger)
