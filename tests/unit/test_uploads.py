"""Uploads of new albums (limits) and the playlist order of uploaded files - the http side: test_restapi.py."""

import io
import os

import pytest

from tests.support import ONE_SECOND_MP3
from tinyjukebox.data.targetalbumtools import playlist_order
from tinyjukebox.errors import BadRequest, PayloadTooLarge
from tinyjukebox.web import uploads
from tinyjukebox.web.uploads import STAGING_FOLDER, Uploads

with open(ONE_SECOND_MP3, "rb") as _f:
    MP3 = _f.read()


def add(upload_list, upload_id, filename, data=MP3):
    return upload_list.add_file(upload_id, filename, io.BytesIO(data), len(data))


def staging(library, upload_id):
    return os.listdir(os.path.join(library.albums_folder, STAGING_FOLDER, upload_id))


def test_playlist_order():
    def track(name, number=None, disc=None):
        return {"filename": name, "tracknumber": number, "discnumber": disc}

    assert playlist_order([track("b", 2), track("a", 1)]) == ["a", "b"]
    assert playlist_order([track("x", 1, 2), track("y", 2, 1), track("z", 1, 1)]) == ["z", "y", "x"]
    assert playlist_order([track("b", 1), track("a", 1)]) == ["a", "b"]  # duplicate numbers -> names
    assert playlist_order([track("b", 1), track("a")]) == ["a", "b"]  # missing number -> names
    assert playlist_order([track("Track 10"), track("track 9"), track("Track 1")]) == [
        "Track 1",
        "track 9",
        "Track 10",
    ]


def test_upload_is_refused_if_the_disk_would_be_full(library, logger):
    """The albums file system must keep min_free_space free - otherwise the upload is refused (413)."""
    upload_list = Uploads(library.targets, "playlist.m3u", 2**20, logger, min_free_space=2**62)
    upload_id = upload_list.create("Voll")["id"]
    with pytest.raises(PayloadTooLarge, match="not enough free space") as error:
        add(upload_list, upload_id, "a.mp3")
    assert error.value.status == 413
    assert staging(library, upload_id) == []
    assert upload_list.get(upload_id)["files"] == []


def test_free_space_check_accepts_files_that_fit(library, logger):
    upload_list = Uploads(library.targets, "playlist.m3u", 2**20, logger, min_free_space=0)
    upload_id = upload_list.create("Passt")["id"]
    assert add(upload_list, upload_id, "a.mp3")["filename"] == "a.mp3"
    assert staging(library, upload_id) == ["a.mp3"]


def test_max_files_per_upload(library, logger, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_FILES", 2)
    upload_list = Uploads(library.targets, "playlist.m3u", 2**20, logger, min_free_space=0)
    upload_id = upload_list.create("Viele")["id"]
    add(upload_list, upload_id, "1.mp3")
    add(upload_list, upload_id, "2.mp3")
    with pytest.raises(BadRequest, match="at most 2 files") as error:
        add(upload_list, upload_id, "3.mp3")
    assert error.value.status == 400
    add(upload_list, upload_id, "2.mp3")  # replacing an uploaded file is fine
    assert sorted(staging(library, upload_id)) == ["1.mp3", "2.mp3"]
