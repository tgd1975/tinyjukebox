"""Activities of all tags (activities json file)."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.common import Listener, Mirror, TargetType, UpdateJson, is_unmapped
from tinyjukebox.data.jsondata import JsonData
from tinyjukebox.data.tag import Tag
from tinyjukebox.data.tags import Tags
from tinyjukebox.schema.activities import schema

__all__ = ["Activities", "create_activity_factory", "load_activity_factory"]


def create_activity_factory(tag: Any, update_json: UpdateJson | None, logger: logging.Logger) -> ActivityAlbum:
    """New activity for a linked tag. :raises ValueError: for missing, unlinked or unsupported tags."""
    factory_logger = get_child(logger, "create_activity_factory")
    if tag is None or not hasattr(tag, "target_id"):
        factory_logger.error("tag not provided or not a tag: %s", tag)
        raise ValueError("tag not provided or not a tag.")
    if is_unmapped(tag.target_id):
        factory_logger.error("target id of tag must be set.")
        raise ValueError("target id of tag must be set.")
    if tag.target_type not in (TargetType.album, TargetType.spotify):
        factory_logger.error("don't know what to do with target type: %s.", tag.target_type)
        raise ValueError(f"don't know what to do with target type: {tag.target_type}.")
    return ActivityAlbum.create_activity(tag, update_json, logger)


def load_activity_factory(
    activity: dict[str, Any] | None, update_json: UpdateJson | None, logger: logging.Logger
) -> ActivityAlbum:
    """Activity from the json file. :raises ValueError: for invalid entries."""
    factory_logger = get_child(logger, "load_activity_factory")
    if activity is None or "target-type" not in activity:
        factory_logger.error("expected dict with key 'target-type', got %s", activity)
        raise ValueError("expected dict with key 'target-type'")
    if activity["target-type"] not in (TargetType.album.name, TargetType.spotify.name):
        factory_logger.error("don't know what to do with target type: %s.", activity["target-type"])
        raise ValueError(f"don't know what to do with target type: {activity['target-type']}.")
    return ActivityAlbum(activity, update_json, logger)


class Activities:
    """Activities by tag uid. The file is written on every change of an activity (see
    :class:`~tinyjukebox.data.tags.Tags` for the locking rules).

    A missing or broken file is not fatal: the activities are rebuilt from the tags (all albums start from the
    beginning).
    """

    def __init__(self, filename: str, tags: Tags, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.RLock()
        self._tags = tags
        self._filename = filename
        self._mirror = Mirror()
        self.activities: dict[str, ActivityAlbum] = {}
        try:
            self._json = JsonData(filename, filename + "_", schema, self._logger)
        except FileNotFoundError:
            self._logger.warning("__init__ - no usable activities file - creating an empty one and rebuilding.")
            with open(filename, "w", encoding="utf-8") as outfile:
                outfile.write("{}")
            self._json = JsonData(filename, filename + "_", schema, self._logger)
            self.rebuild_activities()
        else:
            self.activities = {
                key: load_activity_factory(activity, self._update_json, self._logger)
                for key, activity in self._json.data.items()
            }

    def rebuild_activities(self) -> None:
        """New activities (start of the album) for all tags linked to an album or an entry of Spotify - radio
        stations have none."""
        self._logger.info("rebuild_activities")
        with self._lock:
            self.activities = {
                tag.tag_uid: create_activity_factory(tag, self._update_json, self._logger)
                for tag in self._tags.all()
                if tag.tag_uid is not None
                and tag.target_type in (TargetType.album, TargetType.spotify)
                and not is_unmapped(tag.target_id)
            }
            self._update_json()

    def _to_dict(self) -> dict[str, Any]:
        return {key: activity.to_dict() for key, activity in self.activities.items()}

    def _update_json(self) -> None:
        with self._lock:
            data = self._to_dict()
            self._json.store(data)
            self._mirror.changed(data)

    def listen(self, listener: Listener) -> None:
        """``listener(tag_uid, entry)`` is called for every activity that changes from now on (``None``: deleted)."""
        with self._lock:
            self._mirror.listen(listener, self._to_dict())

    def sync(self, get: Callable[[], dict[str, Any]]) -> bool:
        """Make the activities what ``get()`` returns (the content of the activities json file), keeping the
        activities that stay. The listener is not called. Returns whether something changed."""
        with self._lock:
            data = get()
            if data == self._to_dict():
                return False
            for key in [key for key in self.activities if key not in data]:
                del self.activities[key]
            for key, entry in data.items():
                if key in self.activities:
                    self.activities[key].load(entry)
                else:
                    self.activities[key] = load_activity_factory(entry, self._update_json, self._logger)
            data = self._to_dict()
            self._json.store(data)
            self._mirror.replaced(data)
            return True

    def all(self) -> dict[str, ActivityAlbum]:
        """Snapshot of all activities by tag uid."""
        with self._lock:
            return dict(self.activities)

    def get_activity(self, tag_uid: str | None) -> ActivityAlbum:
        """:raises ValueError: for unknown tag uids."""
        activity = self.activities.get(tag_uid) if tag_uid is not None else None
        if activity is None:
            self._logger.warning("get_activity - unknown tag uid %s.", tag_uid)
            raise ValueError(f"get_activity - unknown tag uid {tag_uid}.")
        return activity

    def add_activity(self, tag: Tag | None) -> ActivityAlbum:
        """New activity for the tag. :raises ValueError: if the tag is missing, unlinked or has an activity."""
        if tag is None or tag.tag_uid is None:
            self._logger.warning("add_activity - tag is None.")
            raise ValueError("add_activity - tag is None.")
        with self._lock:
            if tag.tag_uid in self.activities:
                self._logger.warning("add_activity - tag uid %s already exists.", tag.tag_uid)
                raise ValueError(f"add_activity - tag uid {tag.tag_uid} already exists.")
            activity = create_activity_factory(tag, self._update_json, self._logger)
            self.activities[tag.tag_uid] = activity
            self._update_json()
        return activity

    def delete_activity(self, tag_uid: str | None) -> None:
        if tag_uid is None:
            self._logger.warning("delete_activity - tag is None.")
            raise ValueError("delete_activity - tag is None.")
        with self._lock:
            if tag_uid not in self.activities:
                self._logger.warning("delete_activity - unknown tag uid %s.", tag_uid)
                raise ValueError(f"delete_activity - unknown tag uid {tag_uid}.")
            del self.activities[tag_uid]
            self._update_json()

    def sanity_check(self) -> bool:
        result = True
        for key, activity in self.all().items():
            if key != activity.tag_uid:
                self._logger.warning("sanity_check - key '%s' != activity.tag_uid '%s'", key, activity.tag_uid)
                result = False
            if not activity.sanity_check():
                result = False
        return result

    def to_text(self) -> str:
        return "\n".join(f"key: {key}, activity: {activity.to_text()}" for key, activity in self.all().items())
