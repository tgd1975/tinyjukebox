"""The first filling of the library from the PC (docs/developer/library.md, section 3.5)."""

import os
import shutil

import pytest

from tests.support import DATA, TAG_ALBUM, TAG_UNKNOWN_ALBUM, create_library
from tests.sync.bed import ADMIN
from tinyjukebox.sync.client import Client
from tinyjukebox.tools.fill_library import fill


def admin_client(bed) -> Client:
    with open(bed.library.api.certificate_file, encoding="utf-8") as infile:
        client = Client(bed.url, certificate=infile.read())
    token = client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def test_the_library_is_filled_under_the_ids_at_hand_and_a_box_with_the_same_data_agrees(bed, tmp_path, logger):
    (tmp_path / "pc").mkdir()
    source = create_library(tmp_path / "pc", logger)
    images = tmp_path / "pc" / "images"
    images.mkdir()
    shutil.copy(DATA / "albums" / "simple_album_a" / "1.mp3", images / "station-not-an-image.txt")
    (images / "station-fm4.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    (images / "other.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)  # no cover of a station: stays here
    said: list[str] = []
    counts = fill(admin_client(bed), str(tmp_path / "pc" / "json"), source.albums_folder, str(images), said.append)
    assert counts == {"targets": 2, "tags": 3, "files": 8, "images": 1}
    bed.library.settle()
    targets = bed.catalog("target")
    assert sorted(targets) == ["1", "2"]  # the ids are kept
    assert bed.catalog("tag")[TAG_ALBUM]["target"]["id"] == "1"
    assert bed.catalog("tag")[TAG_UNKNOWN_ALBUM]["target"]["id"] == -1
    assert bed.library.store.manifest("1")["lacks"] == []
    assert os.path.isfile(tmp_path / "library" / "albums" / "short" / "playlist.m3u")
    assert sorted(os.listdir(tmp_path / "library" / "images")) == ["station-fm4.jpg"]
    # repeated: what is there already is skipped
    counts = fill(admin_client(bed), str(tmp_path / "pc" / "json"), source.albums_folder, str(images), said.append)
    assert (counts["targets"], counts["tags"], counts["files"]) == (0, 0, 0)
    # a box with the same data: its albums are recognized, nothing is uploaded, and it gets the ids of the library
    box = bed.box("box1")
    assert box.synced()
    assert sorted(bed.catalog("target")) == ["1", "2"]
    assert box.tags()[TAG_ALBUM]["album_id"] == "1"
    assert box.sync.data.provisional_ids() == []
    assert not bed.library.store.journal.records("conflict")


def test_an_album_that_was_listed_before_its_files_arrived_shows_its_tracks(bed, tmp_path, logger):
    """The page of the library is open while the library is filled: the entries are there before the files."""
    (tmp_path / "pc").mkdir()
    source = create_library(tmp_path / "pc", logger)
    listed = []

    def say(text):
        bed.library.settle()
        listed.append({album["id"]: album["track_count"] for album in bed.library.control.list_albums()})

    fill(admin_client(bed), str(tmp_path / "pc" / "json"), source.albums_folder, None, say)
    bed.library.settle()
    assert listed[0] == {"1": 0, "2": 0}
    albums = {album["id"]: album for album in bed.library.control.list_albums()}
    assert (albums["1"]["track_count"], albums["2"]["track_count"]) == (3, 3)
    assert albums["2"]["length"] == pytest.approx(12, abs=0.5)  # tracks of 4, 3 and 5 s
    assert len(bed.library.control.get_album("1")["tracks"]) == 3
