"""An rfid tag and the target (album) it is linked to."""

import logging
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import NO_TARGET, TargetType, UpdateJson, extra_fields, is_valid_timestamp, now

__all__ = ["Tag"]


class Tag:
    """Entry of ``tags.json``::

        {"tag": {"uid": "1:2:3:4", "name": "Album A"}, "target": {"type": "album", "id": "7"}, "created": "..."}

    ``target_id`` is ``-1`` as long as the tag is not linked to an album. A figure of an advent calendar also has its
    day: ``"target": {"type": "calendar", "id": "31", "day": 17}``. Every change calls ``update_json`` of the owning
    collection.
    """

    def __init__(self, logger: logging.Logger, tag: dict[str, Any], update_json: UpdateJson | None) -> None:
        uid = tag.get("tag", {}).get("uid")
        if not isinstance(uid, str) or not uid:
            message = f"Tag.__init__ - tag.uid must be a non empty string (dict: {tag})."
            logger.error(message)
            raise ValueError(message)
        self.tag_uid: str | None = uid
        self._logger = get_child(logger, f"{type(self).__name__}.{uid}")
        self._logger.debug("__init__ - %s", tag)
        self._update_json = update_json
        self.load(tag)

    def load(self, tag: dict[str, Any]) -> None:
        """Take over name, target and time stamp of ``tag`` (an entry of ``tags.json``) - nothing is written."""
        self.tag_name: str = tag["tag"].get("name", "")
        target = tag.get("target", {})
        # fields of a newer version, on each level: kept and written back
        self._extra = (extra_fields(tag, ("tag", "target", "created")), extra_fields(tag["tag"], ("uid", "name")),
                       extra_fields(target, ("type", "id", "day")))  # fmt: skip
        type_name = str(target.get("type") or "album").lower()
        try:
            self.target_type: TargetType | None = TargetType[type_name]
        except KeyError:
            self._logger.error("__init__ - unknown target type %s.", type_name)
            raise ValueError(f"unknown target type {type_name}.") from None
        self.target_id: int | str | None = target.get("id", NO_TARGET)
        day = target.get("day")
        self.day: int | None = day if self.target_type == TargetType.calendar and isinstance(day, int) else None
        self.created: str | None = tag.get("created") or now()

    def _changed(self) -> None:
        if self._update_json is not None:
            self._update_json()

    def set_target_type(self, target_type: TargetType) -> None:
        if not isinstance(target_type, TargetType):
            raise ValueError("set_target_type - target_type must be a TargetType.")
        self.target_type = target_type
        if target_type != TargetType.calendar:
            self.day = None
        self._changed()

    def set_target_id(self, target_id: int | str | None, day: int | None = None) -> None:
        """``day``: the day of a figure of a calendar."""
        if target_id is None or not str(target_id):
            self._logger.warning("set_target_id - target_id is None or empty.")
            return
        self.target_id = target_id
        self.day = day if self.target_type == TargetType.calendar else None
        self._changed()

    def set_tag_name(self, tag_name: str) -> None:
        self.tag_name = tag_name
        self._changed()

    @classmethod
    def create_tag(cls, logger: logging.Logger, tag_uid: str, update_json: UpdateJson | None) -> "Tag":
        return Tag(logger, {"tag": {"uid": tag_uid}}, update_json)

    def to_dict(self) -> dict[str, Any]:
        extra, extra_tag, extra_target = self._extra
        target_type = self.target_type.name if self.target_type is not None else None
        return {
            "tag": {"uid": self.tag_uid, "name": self.tag_name, **extra_tag},
            "target": {
                "type": target_type,
                "id": self.target_id,
                **({"day": self.day} if self.day is not None else {}),
                **extra_target,
            },
            "created": self.created,
            **extra,
        }

    def sanity_check(self) -> bool:
        result = True
        if not is_valid_timestamp(self.created):
            self._logger.warning("sanity_check - created timestamp error '%s'", self.created)
            result = False
        if not self.tag_uid:
            self._logger.warning("sanity_check - tag_uid not set.")
            result = False
        return result

    def to_text(self) -> str:
        target_type = self.target_type.name if self.target_type is not None else "None"
        day = f", day {self.day}" if self.day is not None else ""
        return (
            f"tag: {self.tag_uid}/'{self.tag_name}' -> target: {target_type}/{self.target_id}{day}. "
            f"created: {self.created}"
        )
