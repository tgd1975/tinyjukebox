"""Schema of the tags json file."""

from typing import Any

__all__ = ["schema"]

schema: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "required": ["created", "tag", "target"],
        "additionalProperties": False,
        "properties": {
            "created": {"description": "timestamp when this entry has been added", "type": "string"},
            "tag": {
                "type": "object",
                "required": ["uid"],
                "additionalProperties": False,
                "properties": {
                    "uid": {"description": "uid of the rfid tag - same as the key of this entry", "type": "string"},
                    "name": {"description": "name of the tag", "type": "string"},
                },
            },
            "target": {
                "oneOf": [
                    {
                        "description": "album, radio station, entry of Spotify or calendar (id -1: not linked yet)",
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "type": {
                                "description": "target type",
                                "type": "string",
                                "enum": ["album", "radio", "spotify", "calendar"],
                            },
                            "id": {
                                "description": "id of the target - key in the targets json file",
                                "type": ["string", "integer"],
                            },
                            "day": {
                                "description": "day of a figure of a calendar: 1 to 24 December",
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 24,
                            },
                        },
                        "required": ["type", "id"],
                    },
                    {
                        "description": "not linked yet",
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"type": {"description": "target type", "type": "string", "enum": [""]}},
                    },
                ]
            },
        },
    },
}
