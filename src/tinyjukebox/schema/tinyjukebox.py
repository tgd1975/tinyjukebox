"""Schema of the config section ``tinyjukebox``."""

from typing import Any

__all__ = ["get_schema"]


def _string(description: str) -> dict[str, str]:
    return {"description": description, "type": "string"}


def get_schema() -> dict[str, Any]:
    """``{"tinyjukebox": schema}`` - combined with the sections ``logger`` and ``drivers`` by
    ``TinyJukebox.get_schema()``."""
    return {
        "tinyjukebox": {
            "description": "Tiny Jukebox box",
            "type": "object",
            "properties": {
                "albums-folder": _string(
                    "root folder of all albums - album folders in the targets json are relative to it"
                ),
                "backups-folder": _string(
                    "folder of the backups that a box without a library makes by itself, until another folder is set "
                    "on its page (optional, default: folder 'backups' beside the albums folder)"
                ),
                "playlist-filename": _string("name of the m3u playlist in each album folder (e.g. playlist.m3u)"),
                "volume": {
                    "type": "object",
                    "properties": {
                        "max-volume": {"description": "maximum volume in %", "type": "number"},
                        "default-volume": {"description": "volume on startup in %", "type": "number"},
                        "volume-step": {"description": "change of the volume per button press in %", "type": "number"},
                        "volume-step-hold": {
                            "description": "change of the volume in % per hold event of a volume button that is kept "
                            "pressed (optional, default: 1) - 'hold_timeout' and 'hold_repeat' of its input driver "
                            "say how often",
                            "type": "number",
                            "minimum": 0,
                        },
                        "mixer-control": _string(
                            "alsa mixer control of the default card (default: Master) - list: 'amixer scontrols'"
                        ),
                    },
                    "required": ["max-volume", "default-volume", "volume-step"],
                    "additionalProperties": False,
                },
                "bluetooth": {
                    "description": "volume of the bluetooth headset (optional) - start value and maximum of speaker "
                    "and headset can be changed at runtime (web page, command line); the changed values are stored "
                    "in the volume json file and apply instead of the config",
                    "type": "object",
                    "properties": {
                        "default-volume": {
                            "description": "volume in % whenever the headset connects (default: 40)",
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "max-volume": {
                            "description": "maximum volume of the headset in % (default: 75) - 100: the box does "
                            "not limit it",
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                    "additionalProperties": False,
                },
                "albumplayer": {
                    "type": "object",
                    "properties": {
                        "rewind-max-time": {
                            "description": "'previous' within this many seconds of a track goes to the previous "
                            "track, later it restarts the current track",
                            "type": "number",
                        },
                        "update-schedule": {
                            "description": "store the playback position every n seconds",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "pause-between-tracks": {
                            "description": "pause between two tracks in seconds",
                            "type": "number",
                            "minimum": 0,
                        },
                        "buffer-time": {
                            "description": "alsa buffer of aplay in ms (optional, default: aplay's default, usually "
                            "500 ms) - smaller starts the sound faster; raise it if the sound stutters",
                            "type": "number",
                            "minimum": 20,
                            "maximum": 2000,
                        },
                    },
                    "required": ["rewind-max-time", "update-schedule", "pause-between-tracks"],
                    "additionalProperties": False,
                },
                "json": {
                    "type": "object",
                    "properties": {
                        "tags": _string("tags json file, e.g. /srv/tinyjukebox/json/tags.json"),
                        "targets": _string("targets (albums) json file, e.g. /srv/tinyjukebox/json/targets.json"),
                        "activities": _string("activities json file, e.g. /srv/tinyjukebox/json/activity.json"),
                        "volume": _string(
                            "start value and maximum of the volumes changed at runtime (optional, default: "
                            "volume.json next to the tags json)"
                        ),
                    },
                    "required": ["tags", "targets", "activities"],
                    "additionalProperties": False,
                },
                "sync": {
                    "description": "synchronization with the library (optional) - the box works with a library as "
                    "soon as its address was entered (web page, or the file 'library' in the identity folder)",
                    "type": "object",
                    "properties": {
                        "folder": _string(
                            "what the library delivered, the changes of this box that it has not confirmed, the "
                            "journal (default: folder 'sync' next to the folder of the tags json)"
                        ),
                        "identity": _string(
                            "what identifies this box: its id, its key, address and certificate of the library "
                            "(default: folder 'identity' next to the folder of the tags json)"
                        ),
                        "retry-seconds": {
                            "description": "how often the library is tried again while it cannot be reached "
                            "(default: 30)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "keep-free": {
                            "description": "MB that stay free on the card of a box that works with a library "
                            "(default: 500, 0: never delete): when less is free, the audio files of albums are "
                            "deleted here, the album not played for the longest time first - the library has them, "
                            "and they are fetched again when their tag is placed",
                            "type": "number",
                            "minimum": 0,
                        },
                        "stage1-seconds": {
                            "description": "health check of a new version: seconds after the start in which no "
                            "unexpected exception may occur and everything must run (default: 10)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "stage2-seconds": {
                            "description": "health check of a new version: seconds an album must have played "
                            "without an unexpected exception before the version is confirmed (default: 60)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "look-hours": {
                            "description": "a box with slots and without a library: hours between two looks for a "
                            "new version at the releases of the git server (default: 24)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                    },
                    "additionalProperties": False,
                },
                "rest-api": {
                    "description": "https rest api and web page (optional)",
                    "type": "object",
                    "properties": {
                        "enabled": {"description": "default: true", "type": "boolean"},
                        "host": _string("address to listen on (default: 0.0.0.0)"),
                        "port": {"description": "default: 8443", "type": "integer", "minimum": 0, "maximum": 65535},
                        "certificate": _string(
                            "certificate file (pem) - a self signed certificate is created if it "
                            "does not exist (default: ~/.config/tinyjukebox/tinyjukebox.crt)"
                        ),
                        "private-key": _string(
                            "private key file (pem, default: ~/.config/tinyjukebox/tinyjukebox.key)"
                        ),
                        "hostnames": {
                            "description": "additional host names / ip addresses for the self signed certificate",
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "users-file": _string(
                            "users and password hashes - manage with 'tinyjukebox-users' "
                            "(default: ~/.config/tinyjukebox/users.yaml)"
                        ),
                        "images-folder": _string(
                            "covers of albums, radio stations and entries of Spotify (default: folder 'images' next "
                            "to the tags json)"
                        ),
                        "session-timeout": {
                            "description": "logout after this many seconds without request (default: 604800 = 7 days)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "idle-timeout": {
                            "description": "logout after this many seconds in which the user of the page did nothing "
                            "(default: 1800 = 30 minutes; 0: never)",
                            "type": "number",
                            "minimum": 0,
                        },
                        "max-upload-size": {
                            "description": "maximum size of an uploaded mp3 file in MB (default: 500)",
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": [
                "volume",
                "albums-folder",
                "playlist-filename",
                "albumplayer",
                "json",
            ],
            "additionalProperties": False,
        }
    }
