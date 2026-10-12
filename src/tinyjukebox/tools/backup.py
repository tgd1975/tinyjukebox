"""Fetch a backup of a box or of the library to this computer::

    tinyjukebox-backup https://nas:8866 --user admin                         # into the current folder
    tinyjukebox-backup https://tinyjukebox.local --user admin --albums ~/backups

or set the folder in which the library keeps the backups that it makes by itself::

    tinyjukebox-backup https://nas:8866 --user admin --library-folder /volume1/tinyjukebox/backups

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


def set_library_folder(client: Client, folder: str) -> str:
    """Set the folder in which the library keeps the backups that it makes by itself; returns the folder that counts
    now. ``client`` is logged in as an admin of the library.

    :raises HttpError: if the library cannot write there, or if this is a box - only the library keeps backups.
    """
    try:
        return str(client.put("/api/library/backups", {"folder": folder})["folder"])
    except HttpError as error:
        if error.status == 404:
            raise HttpError(404, "only the library keeps backups, and this is a box") from None
        raise


def main(argv: Sequence[str] | None = None) -> None:
    """Console script ``tinyjukebox-backup``."""
    parser = argparse.ArgumentParser(description="Fetch a backup of a box or of the library to this computer.")
    parser.add_argument("address", help="address of the box or of the library, e.g. https://nas:8866")
    parser.add_argument("folder", nargs="?", default=".", help="where the backup is put (default: the current folder)")
    parser.add_argument("--albums", action="store_true", help="with the audio files of the albums")
    parser.add_argument(
        "--library-folder",
        metavar="FOLDER",
        help="fetch nothing: set the folder on the computer of the library in which it keeps the backups that it "
        "makes by itself ('' for the one of its config)",
    )
    parser.add_argument("--user", required=True, help="an admin of the web page")
    parser.add_argument("--password-file", help="file with the password (default: asked for)")
    parser.add_argument("--fingerprint", help="sha256 fingerprint of the certificate (else: shown and asked)")
    arguments = parser.parse_args(argv)
    folder = os.path.expanduser(arguments.folder)
    if arguments.library_folder is None and not os.path.isdir(folder):
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
        if arguments.library_folder is not None:
            print(f"the library keeps its backups in {set_library_folder(client, arguments.library_folder)}")
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
