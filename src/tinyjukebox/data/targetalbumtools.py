"""Album folders: find new albums in the albums folder, add them to the targets, order playlists."""

import logging
import os
import re
from pathlib import Path
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.data.targets import Targets

__all__ = [
    "add_album_to_targets",
    "add_new_albums_to_targets",
    "natural_key",
    "playlist_order",
    "scan_for_album_folders",
    "scan_for_new_albums",
    "write_playlist",
]


def scan_for_album_folders(logger: logging.Logger, rootfolder: str, playlistfilename: str) -> list[str]:
    """All folders below ``rootfolder`` that contain a playlist - relative to ``rootfolder``, sorted."""
    my_logger = get_child(logger, "scan_for_albums")
    root = Path(rootfolder)
    # hidden folders hold what is not an album yet: uploads in progress, albums that boxes are sending
    folders = (path.parent.relative_to(root) for path in root.rglob(playlistfilename))
    albums = sorted(str(folder) for folder in folders if not any(part.startswith((".", "@")) for part in folder.parts))
    my_logger.info("rootfolder: %s, playlist: %s, found album folders: %s", rootfolder, playlistfilename, albums)
    return albums


def scan_for_new_albums(targets: Targets, logger: logging.Logger, rootfolder: str, playlistfilename: str) -> list[str]:
    """Album folders that are not yet known as target."""
    my_logger = get_child(logger, "scan_for_new_albums")
    existing_folders = {album.album_folder for album in targets.albums()}
    new_albums = sorted(set(scan_for_album_folders(logger, rootfolder, playlistfilename)) - existing_folders)
    my_logger.info("known albums: %s, new albums: %s", sorted(f for f in existing_folders if f), new_albums)
    return new_albums


def add_album_to_targets(
    targets: Targets, logger: logging.Logger, folder: str, playlistfilename: str, title: str | None = None
) -> TargetAlbum:
    """:raises ValueError: if the album is not valid (see :meth:`TargetAlbum.create_target`)."""
    get_child(logger, "add_album_to_targets").info("adding album in folder '%s'", folder)
    target = {"target-type": TargetType.album.name, "album-folder": folder, "playlist": playlistfilename}
    if title is not None:
        target["title"] = title
    album = targets.add_target(target)
    assert isinstance(album, TargetAlbum)  # noqa: S101 - created from an album dict
    return album


def add_new_albums_to_targets(
    targets: Targets, logger: logging.Logger, rootfolder: str, playlistfilename: str
) -> list[TargetAlbum]:
    my_logger = get_child(logger, "add_new_albums_to_targets")
    return [
        add_album_to_targets(targets, my_logger, folder, playlistfilename)
        for folder in scan_for_new_albums(targets, my_logger, rootfolder, playlistfilename)
    ]


def natural_key(text: str) -> list[int | str]:
    """Sort key for file names: '2' before '10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def playlist_order(files: list[dict[str, Any]]) -> list[str]:
    """Order of the tracks (``files``: track infos, see :func:`tinyjukebox.metadata.read_track`).

    By disc/track number if every file has a track number and the numbers are unique - otherwise by file name
    (natural order).
    """
    numbers = [(f["discnumber"] or 1, f["tracknumber"]) for f in files]
    if files and all(n[1] is not None for n in numbers) and len(set(numbers)) == len(numbers):
        return [f["filename"] for f in sorted(files, key=lambda f: (f["discnumber"] or 1, f["tracknumber"]))]
    return sorted((f["filename"] for f in files), key=natural_key)


def write_playlist(folder: str, playlist_filename: str, tracks: list[str]) -> None:
    """Write the m3u playlist (one file name per line)."""
    with open(os.path.join(folder, playlist_filename), "w", encoding="utf-8") as f:
        f.write("".join(name + "\n" for name in tracks))
