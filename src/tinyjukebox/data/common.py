"""Types and helpers shared by tags, targets and activities."""

import datetime
from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from typing import Any

__all__ = [
    "NO_TARGET",
    "TIMESTAMP_FORMAT",
    "Listener",
    "Mirror",
    "TargetType",
    "UpdateJson",
    "extra_fields",
    "is_unmapped",
    "is_valid_timestamp",
    "now",
]

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
NO_TARGET = -1
"""target id of a tag that is not linked to a target yet (stored as -1 or "-1")."""

UpdateJson = Callable[[], None]
"""Callback of a collection: write the current state of all entries to the json file."""


Listener = Callable[[str, Any], None]
"""Called with key and new value of an entry of a collection that changed (``None``: the entry is gone)."""


class Mirror:
    """Tells a listener which entries of a collection changed - the synchronization with the library
    (:mod:`tinyjukebox.sync`) records every change this way, whoever made it. Called under the lock of the
    collection."""

    def __init__(self) -> None:
        self._listener: Listener | None = None
        self._known: dict[str, Any] = {}

    def listen(self, listener: Listener, known: dict[str, Any]) -> None:
        """``known``: the entries as the listener knows them already."""
        self._listener = listener
        self._known = known

    def changed(self, data: dict[str, Any]) -> None:
        """``data``: all entries as they are now."""
        if self._listener is None:
            return
        known, self._known = self._known, data
        for key in [*known, *(key for key in data if key not in known)]:
            if known.get(key) != data.get(key):
                self._listener(key, data.get(key))

    def replaced(self, data: dict[str, Any]) -> None:
        """The entries were set by the one who listens: nothing to tell."""
        self._known = data


class TargetType(Enum):
    album = "album"
    radio = "radio"
    spotify = "spotify"


def now() -> str:
    """Current UTC time in the timestamp format of the json files."""
    return datetime.datetime.now(datetime.UTC).strftime(TIMESTAMP_FORMAT)


def is_valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


def is_unmapped(target_id: object) -> bool:
    """True for the target id of a tag that is not linked (-1, "-1") and for missing ids."""
    return target_id is None or str(target_id) == str(NO_TARGET)


def extra_fields(entry: Mapping[str, Any], known: Iterable[str]) -> dict[str, Any]:
    """The fields of an entry that this version does not know - a newer version wrote them. They are kept and written
    back as they are, so that this version never deletes them (docs/developer/library.md, section 4.21)."""
    known = set(known)
    return {key: value for key, value in entry.items() if key not in known}
