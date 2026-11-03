"""Schema of the config file of the library: the sections ``logger`` and ``library``."""

from typing import Any

from tinyjukebox.core.schema.logger import get_schema as get_logger_schema
from tinyjukebox.schema.tinyjukebox import get_schema as get_box_schema

__all__ = ["get_schema"]


def _string(description: str) -> dict[str, str]:
    return {"description": description, "type": "string"}


def _seconds(description: str) -> dict[str, Any]:
    return {"description": description, "type": "number", "exclusiveMinimum": 0}


def get_schema() -> dict[str, Any]:
    logger_key, logger_schema = get_logger_schema()
    rest_api = get_box_schema()["tinyjukebox"]["properties"]["rest-api"]
    library = {
        "description": "the library",
        "type": "object",
        "properties": {
            "data-folder": _string(
                "catalog, boxes, id table, journal and the archive of box versions - a folder that an update of the "
                "library keeps"
            ),
            "albums-folder": _string("all albums - album folders in the catalog are relative to it"),
            "images-folder": _string("the covers of the radio stations"),
            "backups-folder": _string(
                "the backups the library makes by itself, until another folder is set on its page (default: "
                "'backups' beside the albums folder)"
            ),
            "json-folder": _string(
                "the json files the library works with, generated from the catalog (default: 'json' in the data folder)"
            ),
            "playlist-filename": _string("name of the m3u playlist in each album folder (default: playlist.m3u)"),
            "bootstrap-password": _string(
                "the password a new box proves at its first contact - taken over at the first start and whenever it "
                "changes here; in between it can be changed on the page of the library"
            ),
            "platform": _string("platform of the boxes whose versions the library fetches, e.g. aarch64-debian-trixie"),
            "slots-folder": _string("folder with the slot that the package of the library brings along (optional)"),
            "rest-api": {**rest_api, "description": "web page and interface for the boxes (as the box: rest-api)"},
            "releases": {
                "description": "where the library asks for new versions of the box software (optional)",
                "type": "object",
                "properties": {
                    "kind": {
                        "description": "git server (default: github)",
                        "type": "string",
                        "enum": ["github", "gitea"],
                    },
                    "api": _string("address of its interface (default: https://api.github.com)"),
                    "repository": _string("owner/name - without it the library does not ask"),
                    "token-file": _string("file with a token that may read the releases of the repository"),
                    "quiet": _seconds("seconds between two checks when nothing happens (default: 6 hours)"),
                    "busy": _seconds("... after a release was sighted or a check by hand (default: 10 minutes)"),
                    "busy-for": _seconds("how long the short interval lasts (default: 6 hours)"),
                    "back-after": _seconds("after this time the interval is the quiet one again (default: 2 weeks)"),
                    "keep-versions": {
                        "description": "versions the archive keeps at most, if the page sets no number (default: 5)",
                        "type": "integer",
                        "minimum": 1,
                    },
                    "keep-bytes": {
                        "description": "disk space the archive uses at most (default: no limit)",
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "additionalProperties": False,
            },
            "diagnostics": {
                "description": "what the library keeps of the logs of the boxes (optional)",
                "type": "object",
                "properties": {
                    "days": {"description": "days a record is kept (default: 30)", "type": "integer", "minimum": 1},
                    "records-per-box": {
                        "description": "records kept per box at most - the oldest go first (default: 20000)",
                        "type": "integer",
                        "minimum": 100,
                    },
                },
                "additionalProperties": False,
            },
        },
        "required": ["data-folder", "albums-folder", "images-folder"],
        "additionalProperties": False,
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {logger_key: logger_schema, "library": library},
        "required": [logger_key, "library"],
    }
