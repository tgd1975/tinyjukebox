"""Schema of the activities json file."""

from typing import Any

__all__ = ["schema"]

schema: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "required": ["tag-uid", "target-id", "track-id", "track-time", "target-type", "album-finished"],
        "additionalProperties": False,
        "properties": {
            "tag-uid": {"description": "uid of the rfid tag - same as the key of this entry", "type": "string"},
            "target-id": {"description": "album id - key in the targets json file", "type": ["string", "integer"]},
            "track-id": {"description": "index of the current track in the playlist", "type": "integer"},
            "track-time": {"description": "position in seconds in the current track", "type": "number"},
            "target-type": {"description": "type of the target", "type": "string", "enum": ["album"]},
            "album-finished": {"description": "album has been played to its end the last time", "type": "boolean"},
        },
    },
}
