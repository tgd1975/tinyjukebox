"""Schema of the targets json file."""

from typing import Any

__all__ = ["schema"]

_COMMON: dict[str, Any] = {
    "target-id": {"description": "same as the key of this entry", "type": "string"},
    "created": {"description": "timestamp when this entry has been added", "type": "string"},
}

schema: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": {
        "oneOf": [
            {
                "description": "album",
                "type": "object",
                "required": ["target-id", "target-type", "album-folder", "playlist", "created"],
                "additionalProperties": False,
                "properties": {
                    **_COMMON,
                    "target-type": {"description": "type of the target", "type": "string", "enum": ["album"]},
                    "album-folder": {
                        "description": "folder of the album (relative to albums-folder) with the mp3 files and the "
                        "playlist",
                        "type": "string",
                    },
                    "playlist": {"description": "m3u playlist in the album folder", "type": "string"},
                    "title": {
                        "description": "title of the album - without it the title comes from the tracks or the folder",
                        "type": "string",
                    },
                    "cover": {
                        "description": "cover of the album: name of an image in the images folder - without it the "
                        "album shows an image of its folder or of its tracks",
                        "type": "string",
                    },
                },
            },
            {
                "description": "internet radio station",
                "type": "object",
                "required": ["target-id", "target-type", "name", "url", "created"],
                "additionalProperties": False,
                "properties": {
                    **_COMMON,
                    "target-type": {"description": "type of the target", "type": "string", "enum": ["radio"]},
                    "name": {"description": "name of the station", "type": "string"},
                    "url": {
                        "description": "http(s) url of the stream or of an m3u/pls playlist with the stream",
                        "type": "string",
                    },
                    "cover": {
                        "description": "cover of the station: name of an image in the images folder",
                        "type": "string",
                    },
                },
            },
            {
                "description": "entry of Spotify: an album, playlist, track, episode, podcast, audiobook or artist",
                "type": "object",
                "required": ["target-id", "target-type", "name", "uri", "created"],
                "additionalProperties": False,
                "properties": {
                    **_COMMON,
                    "target-type": {"description": "type of the target", "type": "string", "enum": ["spotify"]},
                    "name": {"description": "name of the entry", "type": "string"},
                    "uri": {
                        "description": "address that Spotify plays: spotify:<kind>:<id>",
                        "type": "string",
                        "pattern": "^spotify:(album|playlist|track|episode|show|audiobook|artist):[A-Za-z0-9]{1,64}$",
                    },
                    "cover": {
                        "description": "cover of the entry: name of an image in the images folder",
                        "type": "string",
                    },
                },
            },
        ]
    },
}
