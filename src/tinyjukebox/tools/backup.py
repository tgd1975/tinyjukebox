"""Fetch a backup of a box or of the library to this computer::

    tinyjukebox-backup https://nas:8866 --user admin                         # into the current folder
    tinyjukebox-backup https://tinyjukebox.local --user admin --albums ~/backups

or set the folders in which the library - or a box without a library - keeps the backups that it makes by itself,
the one for the backups without the albums and the one for those with them::

    tinyjukebox-backup https://nas:8866 --user admin --library-folder /volume1/tinyjukebox/backups
    tinyjukebox-backup https://nas:8866 --user admin --library-folder-albums /volume2/backups-with-mp3s

Without ``--albums`` the backup holds everything but the audio files of the albums and is small; with it, it holds
them as well and is as large as all albums together. Nothing secret is part of a backup. The file is read to its end
before it gets its name: a backup that was cut off on the way is not kept. What a backup contains and how it is put
back: docs/developer/operations.md, section 10.
"""

import argparse
import contextlib
import getpass
import os
import sys
from collections.abc import Sequence
from typing import Any

from tinyjukebox.backup import NAME, PART, Incomplete, verify
from tinyjukebox.sync.client import Client, HttpError, Unreachable, WrongCertificate
from tinyjukebox.tools.fill_library import _client

__all__ = ["fetch", "main", "set_library_folder"]

KEPT = ("/api/library/backups", "/api/backups")  # the schedules of the library, and of a box without a library


def fetch(client: Client, folder: str, albums: bool) -> tuple[str, dict[str, Any]]:
    """Fetch a backup into ``folder``; returns its file and what it says about itself
    (:func:`tinyjukebox.backup.verify`). ``client`` is logged in as an admin.

    :raises Incomplete: if what arrived is no complete backup - nothing is kept then.
    """
    part = os.path.join(folder, f".tinyjukebox-backup{PART}")
    with contextlib.suppress(FileNotFoundError):
        os.remove(part)  # the client would continue a file that is there
    try:
        answer = client.request("GET", "/api/backup" + ("?albums=1" if albums else ""), sink=part)
        name = (answer.headers.get_filename() or "").strip()
        if NAME.fullmatch(name) is None:
            raise Incomplete(f"the answer names no backup: {name!r}")
        about = verify(part)
        target = os.path.join(folder, name)
        os.replace(part, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(part)
    return target, about


def set_library_folder(client: Client, folder: str, albums: bool = False) -> str:
    """Set the folder in which the library keeps the backups that it makes by itself - those without the albums or,
    with ``albums``, those with them; returns the folder that counts now. ``client`` is logged in as an admin of the
    library, or of a box that keeps backups.

    :raises HttpError: if the folder cannot be written to, or if nothing keeps backups there.
    """
    kind = "albums" if albums else "plain"
    for path in KEPT:
        try:
            return str(client.put(path, {kind: {"folder": folder}})[kind]["folder"])
        except HttpError as error:
            if error.status != 404:
                raise
    raise HttpError(404, "no backups are kept there: it is neither a library nor a box of this version")


def main(argv: Sequence[str] | None = None) -> None:
    """Console script ``tinyjukebox-backup``."""
    parser = argparse.ArgumentParser(description="Fetch a backup of a box or of the library to this computer.")
    parser.add_argument("address", help="address of the box or of the library, e.g. https://nas:8866")
    parser.add_argument("folder", nargs="?", default=".", help="where the backup is put (default: the current folder)")
    parser.add_argument("--albums", action="store_true", help="with the audio files of the albums")
    parser.add_argument(
        "--library-folder",
        metavar="FOLDER",
        help="fetch nothing: set the folder on the computer of the library in which it keeps the backups without "
        "the albums that it makes by itself ('' for the one of its config)",
    )
    parser.add_argument(
        "--library-folder-albums",
        metavar="FOLDER",
        help="fetch nothing: the same for the backups with the albums",
    )
    parser.add_argument("--user", required=True, help="an admin of the web page")
    parser.add_argument("--password-file", help="file with the password (default: asked for)")
    parser.add_argument("--fingerprint", help="sha256 fingerprint of the certificate (else: shown and asked)")
    arguments = parser.parse_args(argv)
    folder = os.path.expanduser(arguments.folder)
    folders = [(arguments.library_folder, False), (arguments.library_folder_albums, True)]
    folders = [(kept, albums) for kept, albums in folders if kept is not None]
    if not folders and not os.path.isdir(folder):
        sys.exit(f"'{folder}' is no folder")
    if arguments.password_file:
        with open(arguments.password_file, encoding="utf-8") as infile:
            password = infile.read().strip()
    else:
        password = getpass.getpass(f"Password of {arguments.user}: ")
    try:
        client = _client(arguments.address.rstrip("/"), arguments.fingerprint, True, "the box or library")
        token = client.post("/api/login", {"username": arguments.user, "password": password})["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        for kept, albums in folders:
            what = "its backups with the albums" if albums else "its backups"
            print(f"the library keeps {what} in {set_library_folder(client, kept, albums)}")
        if folders:
            return
        target, about = fetch(client, folder, arguments.albums)
    except (Unreachable, WrongCertificate, HttpError, Incomplete, OSError) as error:
        sys.exit(f"no backup: {error}")
    print(
        f"{target}: backup of a {about['kind']} (version {about['version']}), {about['files']} files, "
        f"{about['bytes']} bytes"
    )
    for name in about["skipped"]:
        print(f"  left out, it disappeared while it was read: {name}")
