"""All known rfid tags (``tags.json``)."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import Listener, Mirror, TargetType, is_unmapped
from tinyjukebox.data.jsondata import JsonData
from tinyjukebox.data.tag import Tag
from tinyjukebox.schema.tags import schema

__all__ = ["Tags"]


class Tags:
    """Tags by uid. Changes are written to the json file immediately.

    Tags are changed by the state machine (new tags), the rest api and the command line - concurrently. ``_lock``
    serializes changes of the collection and the writing of the file; it is never held while waiting for anything
    else. Readers in other threads use :meth:`all` (a snapshot) instead of iterating ``tags``.
    """

    def __init__(self, filename: str, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.RLock()
        self._json = JsonData(filename, filename + "_", schema, self._logger, entries=True)
        self._mirror = Mirror()
        self.tags: dict[str, Tag] = {}
        for key, tag in list(self._json.data.items()):
            try:
                self.tags[key] = Tag(self._logger, tag, self._update_json)
            except ValueError as e:  # a tag this version cannot use does not keep the box from starting
                self._json.set_aside(key, tag, str(e))

    @property
    def unavailable(self) -> set[str]:
        """The uids of tags this version cannot use (docs/developer/library.md, section 4.21): known, but not
        playable here - never taken for new tags."""
        with self._lock:
            return set(self._json.aside) - set(self.tags)

    def _to_dict(self) -> dict[str, Any]:
        return {key: tag.to_dict() for key, tag in self.tags.items()}

    def _update_json(self) -> None:
        with self._lock:
            data = self._to_dict()
            self._json.store(data)
            self._mirror.changed(data)

    def listen(self, listener: Listener) -> None:
        """``listener(uid, entry)`` is called for every tag that changes from now on (``None``: deleted)."""
        with self._lock:
            self._mirror.listen(listener, self._to_dict())

    def sync(self, get: Callable[[], dict[str, Any]]) -> bool:
        """Make the tags what ``get()`` returns (the content of ``tags.json``), keeping the tags that stay. The
        listener is not called. Returns whether something changed."""
        with self._lock:
            data, aside = self._json.split(get())
            if data == self._to_dict() and aside == self._json.aside:
                return False
            for key in [key for key in self.tags if key not in data]:
                del self.tags[key]
            for key, entry in data.items():
                try:
                    if key in self.tags:
                        self.tags[key].load(entry)
                    else:
                        self.tags[key] = Tag(self._logger, entry, self._update_json)
                except ValueError as e:
                    self._logger.warning("sync - tag '%s' set aside: %s", key, e)
                    self.tags.pop(key, None)
                    aside[key] = entry
            data = self._to_dict()
            self._json.store(data, aside)
            self._mirror.replaced(data)
            return True

    def all(self) -> list[Tag]:
        """Snapshot of all tags."""
        with self._lock:
            return list(self.tags.values())

    def add_tag(self, uid: str | None) -> Tag:
        """Add an unmapped tag. :raises ValueError: if the uid is empty or already known."""
        if not uid:
            message = "add_tag - uid must not be None or empty."
            self._logger.error(message)
            raise ValueError(message)
        with self._lock:
            if uid in self.tags or uid in self._json.aside:
                message = f"add_tag - tag already exists with uid '{uid}'"
                self._logger.error(message)
                raise ValueError(message)
            tag = Tag.create_tag(self._logger, uid, self._update_json)
            self.tags[uid] = tag
            self._update_json()
        return tag

    def delete_tag(self, uid: str | None) -> None:
        with self._lock:
            if uid not in self.tags:
                self._logger.error("delete_tag - unknown tag %s.", uid)
                raise ValueError(f"delete_tag - unknown tag {uid}.")
            del self.tags[uid]
            self._update_json()

    def get_tag(self, uid: str | None) -> Tag:
        """:raises ValueError: for unknown uids."""
        tag = self.tags.get(uid) if uid is not None else None
        if tag is None:
            self._logger.warning("get_tag - unknown tag %s.", uid)
            raise ValueError(f"get_tag - unknown tag {uid}.")
        return tag

    def get_used_target_ids(self, target_type: object) -> list[int | str]:
        """Distinct ids of all targets of ``target_type`` linked to a tag - numeric ids sorted numerically first."""
        if not isinstance(target_type, TargetType):
            self._logger.error("get_used_target_ids - target_type must be a TargetType, got %r.", target_type)
            raise ValueError("get_used_target_ids - target_type must be a TargetType.")
        target_ids = {
            tag.target_id
            for tag in self.all()
            if tag.target_type == target_type and tag.target_id is not None and not is_unmapped(tag.target_id)
        }
        return sorted(target_ids, key=lambda i: (0, int(i), "") if str(i).isdigit() else (1, 0, str(i)))

    def assign_target(self, tag_uid: str, target_type: object, target_id: int | str | None) -> None:
        """Link the tag with a target. :raises ValueError: for unknown tags or invalid target type/id."""
        try:
            tag = self.get_tag(tag_uid)
        except ValueError:
            self._logger.error("assign_target - unknown tag_uid %s.", tag_uid)
            raise
        if target_id is None or not str(target_id):
            self._logger.error("assign_target - target_id is None or empty")
            raise ValueError("assign_target - target_id is None or empty")
        if not isinstance(target_type, TargetType):
            self._logger.error("assign_target - target_type must be a TargetType, got %r.", target_type)
            raise ValueError("assign_target - target_type must be a TargetType.")
        with self._lock:
            if tag.target_type != target_type:
                tag.set_target_type(target_type)
            tag.set_target_id(target_id)

    def sanity_check(self) -> bool:
        result = True
        for key, tag in list(self.tags.items()):
            if key != tag.tag_uid:
                self._logger.warning("sanity_check - key '%s' != tag.tag_uid '%s'", key, tag.tag_uid)
                result = False
            if not tag.sanity_check():
                result = False
        return result

    def to_text(self) -> str:
        return "\n".join(f"key: {key}, tag: {tag.to_text()}" for key, tag in list(self.tags.items()))
