"""tinyjukebox-migrate-01 - convert the json files of the old box (version 0.1) into the current format.

    tinyjukebox-migrate-01 /path/to/old-card/json /srv/tinyjukebox/json

Reads albums.json and tags.json from the old folder and writes targets.json, tags.json and an empty activity.json into
the new folder. The albums themselves are not touched - copy the albums folder separately.

    old albums.json: "45": {"folder": "/srv/tinyjukebox/albums/Album A/", "id": "45", "playlist": "playlist.m3u"}
    new targets.json: "45": {"target-id": "45", "target-type": "album", "album-folder": "Album A",
                             "playlist": "playlist.m3u", "created": "..."}

The old format does not know when an album was added: 'created' is the date of the oldest tag linked to the album (or
now, for albums without tag). The positions of the old activity.json are not taken over - every album starts at its
beginning.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

import jsonschema

from tinyjukebox.data.common import TargetType, is_valid_timestamp, now
from tinyjukebox.schema.activities import schema as activities_schema
from tinyjukebox.schema.tags import schema as tags_schema
from tinyjukebox.schema.targets import schema as targets_schema

__all__ = ["convert_albums", "main"]

OLD_ALBUMS_FOLDER = "/srv/tinyjukebox/albums"
ALBUMS_FILE, TAGS_FILE, TARGETS_FILE, ACTIVITIES_FILE = "albums.json", "tags.json", "targets.json", "activity.json"


def _load(filename: str) -> dict[str, Any]:
    try:
        with open(filename, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read '{filename}': {e}") from e
    if not isinstance(data, dict):
        raise SystemExit(f"'{filename}' must contain a json object.")
    return data


def _created_by_album(tags: dict[str, Any]) -> dict[str, str]:
    """Album id -> 'created' of the oldest tag linked to it (the timestamps sort like their dates)."""
    created: dict[str, str] = {}
    for tag in tags.values():
        target = tag.get("target", {})
        stamp = tag.get("created")
        if target.get("type") != TargetType.album.name or not is_valid_timestamp(stamp):
            continue
        album_id = str(target.get("id"))
        if album_id not in created or stamp < created[album_id]:
            created[album_id] = stamp
    return created


def convert_albums(albums: dict[str, Any], tags: dict[str, Any], old_albums_folder: str) -> dict[str, Any]:
    """The targets of the old ``albums`` - their folders become relative to ``old_albums_folder``.

    :raises ValueError: if an entry cannot be converted (all problems in one message).
    """
    root = os.path.normpath(old_albums_folder)
    created = _created_by_album(tags)
    default_created = now()
    targets: dict[str, Any] = {}
    problems = []
    for key, album in albums.items():
        if not isinstance(album, dict) or not all(isinstance(album.get(k), str) for k in ("folder", "playlist")):
            problems.append(f"album '{key}': 'folder' and 'playlist' are required.")
            continue
        if str(album.get("id", key)) != key:
            problems.append(f"album '{key}': its 'id' is '{album['id']}'.")
            continue
        folder = os.path.normpath(album["folder"])
        if os.path.commonpath([root, folder]) != root or folder == root:
            problems.append(f"album '{key}': folder '{album['folder']}' is not inside '{old_albums_folder}'.")
            continue
        targets[key] = {
            "target-id": key,
            "target-type": TargetType.album.name,
            "album-folder": os.path.relpath(folder, root),
            "playlist": album["playlist"],
            "created": created.get(key, default_created),
        }
    if problems:
        raise ValueError("\n".join(problems))
    return targets


def _write(filename: str, data: dict[str, Any]) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.flush()
        os.fsync(f.fileno())


def _validate(name: str, data: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:
        raise SystemExit(f"{name}: {e.message} (at {'/'.join(str(p) for p in e.absolute_path)})") from e


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="tinyjukebox-migrate-01", description="Convert the json files of the old box (version 0.1)."
    )
    parser.add_argument("old", help=f"folder with {ALBUMS_FILE} and {TAGS_FILE} of the old box")
    parser.add_argument("new", help=f"folder for {TARGETS_FILE}, {TAGS_FILE} and {ACTIVITIES_FILE} (json folder)")
    parser.add_argument(
        "--old-albums-folder",
        default=OLD_ALBUMS_FOLDER,
        help=f"albums folder of the old box - the album folders become relative to it (default: {OLD_ALBUMS_FOLDER})",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing files in the new folder")
    args = parser.parse_args(argv)

    albums = _load(os.path.join(args.old, ALBUMS_FILE))
    tags = _load(os.path.join(args.old, TAGS_FILE))
    _validate(TAGS_FILE, tags, tags_schema)  # the format of the tags did not change
    try:
        targets = convert_albums(albums, tags, args.old_albums_folder)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    activities: dict[str, Any] = {}
    _validate(TARGETS_FILE, targets, targets_schema)
    _validate(ACTIVITIES_FILE, activities, activities_schema)
    output = {TARGETS_FILE: targets, TAGS_FILE: tags, ACTIVITIES_FILE: activities}

    os.makedirs(args.new, exist_ok=True)
    existing = [name for name in output if os.path.exists(os.path.join(args.new, name))]
    if existing and not args.force:
        raise SystemExit(f"'{args.new}' already contains {', '.join(existing)} - use --force to overwrite.")
    for name, data in output.items():
        _write(os.path.join(args.new, name), data)

    linked = {str(tag["target"].get("id")) for tag in tags.values() if tag["target"].get("type")}
    for album_id in sorted(linked - set(targets), key=str):
        print(f"warning: a tag is linked to album '{album_id}', which does not exist.", file=sys.stderr)
    for album_id in sorted(set(targets) - linked, key=lambda i: targets[i]["album-folder"]):
        print(f"album without tag: {album_id}  {targets[album_id]['album-folder']}")
    print(f"{len(targets)} albums, {len(tags)} tags written to '{args.new}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
