"""Fill the library from what is at hand on this computer (docs/developer/library.md, section 3.5)::

    tinyjukebox-fill-library https://nas:8866 --user admin --json ~/tinyjukebox/json --albums ~/tinyjukebox/albums \\
        --images ~/tinyjukebox/json/images

Tags, albums with their files, stations and their covers are taken over under their ids, so that a box that has the
same data and the library agree from the start. The command can be repeated: what is there already is skipped. The
positions come from the boxes themselves.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from typing import Any

from tinyjukebox.images import is_cover
from tinyjukebox.sync import handshake
from tinyjukebox.sync import manifest as manifests
from tinyjukebox.sync.client import Client, HttpError, Unreachable, WrongCertificate
from tinyjukebox.sync.files import _quoted
from tinyjukebox.sync.util import read_json

__all__ = ["fill", "main"]


def _say(text: str) -> None:
    print(text)


def _client(url: str, fingerprint: str | None, confirm: bool, server: str = "the library") -> Client:
    """A client that accepts the certificate of the library only. The library has a self-signed certificate, so
    its fingerprint is compared with the one that is expected - or, like ssh, shown and asked for. ``server`` names
    the other side in the texts, for a command that also talks to a box."""
    probe = Client(url)
    probe.request("GET", "/favicon.svg")
    certificate = probe.peer_certificate or ""
    found = handshake.fingerprint(certificate)
    if fingerprint is not None:
        if found.lower() != fingerprint.replace(":", "").lower():
            raise WrongCertificate(f"{server} presents a certificate with the fingerprint {found}")
    elif confirm:
        _say(f"{server[:1].upper()}{server[1:]} presents a certificate with the sha256 fingerprint\n  {found}")
        hint = " (its log shows the fingerprint at the start)" if server == "the library" else ""
        if input(f"Is this {server}{hint}? [y/N] ").strip().lower() != "y":
            raise WrongCertificate("not confirmed")
    return Client(url, certificate=certificate, timeout=60.0)


def fill(
    client: Client, json_folder: str, albums_folder: str | None, images_folder: str | None, say: Any = _say
) -> dict[str, int]:
    """Send the catalog in ``json_folder``, the albums and the covers of the stations. ``client`` is logged in as an
    admin."""
    targets = read_json(os.path.join(json_folder, "targets.json"), {})
    tags = read_json(os.path.join(json_folder, "tags.json"), {})
    # a tag that is linked to nothing that exists is not linked
    for tag in tags.values():
        if str(tag.get("target", {}).get("id")) not in targets:
            tag["target"] = {"type": tag.get("target", {}).get("type") or "album", "id": -1}
    taken = client.post("/api/library/fill", {"targets": targets, "tags": tags})
    counts = {name: len(keys) for name, keys in taken.items()}
    say(f"taken over: {counts['targets']} targets, {counts['tags']} tags")
    counts["files"] = counts["images"] = 0
    for key, target in targets.items() if albums_folder else []:
        if target.get("target-type") != "album":
            continue
        folder = os.path.join(albums_folder or "", target["album-folder"])
        try:
            manifest = manifests.create(folder, target["playlist"])
        except OSError as error:
            say(f"album {key} ({target['album-folder']}): cannot be read here, skipped ({error})")
            continue
        try:
            lacks = client.post(f"/api/library/fill/albums/{key}", {"manifest": manifest})["lacks"]
        except HttpError as error:
            say(f"album {key} ({target['album-folder']}): {error}")
            continue
        for name in lacks:
            client.request(
                "PUT", f"/api/library/fill/albums/{key}/files/{_quoted(name)}", upload=manifests.path(folder, name)
            )
            counts["files"] += 1
        say(f"album {key} ({target['album-folder']}): {len(lacks)} of {len(manifest['files'])} files sent")
    if images_folder:
        for name in sorted(os.listdir(images_folder)):
            if is_cover(name):
                filename = os.path.join(images_folder, name)
                client.request("PUT", f"/api/library/fill/images/{_quoted(name)}", upload=filename)
                counts["images"] += 1
        say(f"{counts['images']} covers of stations sent; the library keeps the ones it lacked")
    return counts


def main(argv: Sequence[str] | None = None) -> None:
    """Console script ``tinyjukebox-fill-library``."""
    parser = argparse.ArgumentParser(
        description="Fill the library from the json files, albums and station covers here."
    )
    parser.add_argument("library", help="address of the library, e.g. https://nas:8866")
    parser.add_argument("--user", required=True, help="an admin of the page of the library")
    parser.add_argument("--password-file", help="file with the password (default: asked for)")
    parser.add_argument("--json", required=True, help="folder with tags.json and targets.json")
    parser.add_argument("--albums", help="albums folder - album-folder entries of targets.json are relative to it")
    parser.add_argument("--images", help="folder with the covers of the radio stations")
    parser.add_argument("--fingerprint", help="sha256 fingerprint of the certificate of the library (else: asked)")
    arguments = parser.parse_args(argv)
    if arguments.password_file:
        with open(arguments.password_file, encoding="utf-8") as infile:
            password = infile.read().strip()
    else:
        password = getpass.getpass(f"Password of {arguments.user}: ")
    try:
        client = _client(arguments.library.rstrip("/"), arguments.fingerprint, confirm=True)
        token = client.post("/api/login", {"username": arguments.user, "password": password})["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        fill(client, arguments.json, arguments.albums, arguments.images)
    except (Unreachable, WrongCertificate, HttpError) as error:
        sys.exit(f"the library was not filled: {error}")
