"""Set the titles and the covers of the albums and the covers of the radio stations of a box or of the library from
a folder on this computer::

    tinyjukebox-import-covers https://tinyjukebox.local --user admin /path/to/covers

The folder holds the images and ``captions.json``, which names the image and the title of an album under its id::

    {"14": {"image": "album-14.jpg", "title": "Artist - Album (2010)"}}

An entry may leave out ``image`` or ``title``. The cover of a radio station is the image that is named after its id:
``radio-115.jpg`` (or ``.jpeg``, ``.png``, ``.webp``).

The command shows what it is going to change and asks before it does. It can be repeated: a title that is the same
as in ``captions.json`` is skipped, every other one is replaced.
"""

import argparse
import getpass
import os
import re
import sys
from collections.abc import Sequence
from typing import Any

from tinyjukebox.sync.client import Client, HttpError, Unreachable, WrongCertificate
from tinyjukebox.sync.util import read_json
from tinyjukebox.tools.fill_library import _client

__all__ = ["CAPTIONS_FILE", "import_covers", "main", "plan"]

CAPTIONS_FILE = "captions.json"
STATION_COVER = re.compile(r"radio-(.+)\.(jpe?g|png|webp)", re.IGNORECASE)

Step = tuple[str, str, str | None, str | None]
"""("album" or "radio", id, new title or None, image file or None)"""
NAMES = {"album": "album", "radio": "radio station"}


def _say(text: str) -> None:
    print(text)


def plan(
    folder: str, albums: dict[str, dict[str, Any]], say: Any = _say, radios: dict[str, dict[str, Any]] | None = None
) -> list[Step]:
    """What is to be done: the steps for the albums of ``captions.json`` in ``folder`` that are there (``albums``:
    the albums of the box or library by id), then for the images ``radio-<id>`` of the stations that are there
    (``radios``: the stations by id). What cannot be done is said and left out.

    :raises ValueError: if ``captions.json`` is missing or not of the form above.
    """
    captions = read_json(os.path.join(folder, CAPTIONS_FILE), None)
    if not isinstance(captions, dict) or not all(isinstance(entry, dict) for entry in captions.values()):
        raise ValueError(f"{os.path.join(folder, CAPTIONS_FILE)} must map album ids to their image and title")
    steps: list[Step] = []
    for album_id, entry in captions.items():
        album = albums.get(album_id)
        if album is None:
            say(f"album {album_id}: not there - skipped")
            continue
        title, image = entry.get("title"), entry.get("image")
        filename = os.path.join(folder, str(image)) if image else None
        if filename is not None and (os.path.basename(str(image)) != image or not os.path.isfile(filename)):
            say(f"album {album_id}: image '{image}' not found in the folder - the image is skipped")
            filename = None
        new_title = title.strip() if isinstance(title, str) and title.strip() != album["title"] else None
        if new_title or filename:
            steps.append(("album", album_id, new_title or None, filename))
    for name in sorted(os.listdir(folder)):
        match = STATION_COVER.fullmatch(name)
        if match is None:
            continue
        if match.group(1) not in (radios or {}):
            say(f"radio station {match.group(1)}: not there - skipped")
            continue
        steps.append(("radio", match.group(1), None, os.path.join(folder, name)))
    return steps


def import_covers(client: Client, folder: str, confirm: Any = None, say: Any = _say) -> dict[str, int]:
    """Set titles and covers as :func:`plan` says. ``confirm(steps)`` may refuse (returns False): nothing is changed
    then. Returns how many titles and covers were set and how many albums and stations failed."""
    albums = {album["id"]: album for album in client.get("/api/albums")}
    radios = {radio["id"]: radio for radio in client.get("/api/radios")}
    steps = plan(folder, albums, say, radios)
    counts = {"titles": 0, "covers": 0, "failed": 0}
    if not steps or (confirm is not None and not confirm(steps)):
        return counts
    for kind, target_id, title, filename in steps:
        try:
            if title is not None:
                client.request("PATCH", f"/api/albums/{target_id}", {"title": title})
                counts["titles"] += 1
            if filename is not None:
                client.request("PUT", f"/api/{kind}s/{target_id}/cover", upload=filename)
                counts["covers"] += 1
        except HttpError as error:
            say(f"{NAMES[kind]} {target_id}: failed - {error}")
            counts["failed"] += 1
    return counts


def _ask(steps: list[Step], shown: int = 200) -> bool:
    for kind, target_id, title, filename in steps[:shown]:
        changes = [f"title '{title}'" if title else "", f"image {os.path.basename(filename)}" if filename else ""]
        _say(f"{NAMES[kind]} {target_id}: " + ", ".join(change for change in changes if change))
    if len(steps) > shown:
        _say(f"... and {len(steps) - shown} more")
    return input(f"Change these {len(steps)} albums and radio stations? [y/N] ").strip().lower() == "y"


def main(argv: Sequence[str] | None = None) -> None:
    """Console script ``tinyjukebox-import-covers``."""
    parser = argparse.ArgumentParser(
        description="Set titles and covers of albums and covers of radio stations from a folder with captions.json."
    )
    parser.add_argument("address", help="address of the box or of the library, e.g. https://tinyjukebox.local")
    parser.add_argument("folder", help=f"folder with the images and {CAPTIONS_FILE}")
    parser.add_argument("--user", required=True, help="an admin of the web page")
    parser.add_argument("--password-file", help="file with the password (default: asked for)")
    parser.add_argument("--fingerprint", help="sha256 fingerprint of the certificate (else: shown and asked)")
    parser.add_argument("--yes", action="store_true", help="do not ask before anything is changed")
    arguments = parser.parse_args(argv)
    if arguments.password_file:
        with open(arguments.password_file, encoding="utf-8") as infile:
            password = infile.read().strip()
    else:
        password = getpass.getpass(f"Password of {arguments.user}: ")
    try:
        client = _client(arguments.address.rstrip("/"), arguments.fingerprint, True, "the box or library")
        token = client.post("/api/login", {"username": arguments.user, "password": password})["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        counts = import_covers(client, os.path.expanduser(arguments.folder), None if arguments.yes else _ask)
    except (Unreachable, WrongCertificate, HttpError, ValueError) as error:
        sys.exit(f"nothing more was changed: {error}")
    _say(f"{counts['titles']} titles and {counts['covers']} covers set, {counts['failed']} failed")
    if counts["failed"]:
        sys.exit(1)
