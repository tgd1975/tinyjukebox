"""Album folder tools: scan for albums, add them to the targets, playlist order and writing."""

import os

import pytest

from tests.support import ONE_SECOND_MP3, make_album, prepare_targets
from tinyjukebox.data.target import read_playlist
from tinyjukebox.data.targetalbumtools import (
    add_album_to_targets,
    add_new_albums_to_targets,
    natural_key,
    playlist_order,
    scan_for_album_folders,
    scan_for_new_albums,
    write_playlist,
)
from tinyjukebox.data.targets import Targets

PLAYLIST = "playlist.m3u"
SIMPLE = ["simple_album_a", "simple_album_b", "simple_album_c"]
NESTED = ["nested/nested_a", "nested/nested_b"]


def test_scan_for_albums_multiple(tmp_path, logger):
    _, root = prepare_targets(tmp_path, "testdata_multiple", SIMPLE)
    assert scan_for_album_folders(logger, root, PLAYLIST) == SIMPLE


def test_scan_for_albums_multiple_with_nested(tmp_path, logger):
    _, root = prepare_targets(tmp_path, "testdata_multiple", SIMPLE + NESTED)
    assert scan_for_album_folders(logger, root, PLAYLIST) == NESTED + SIMPLE


def test_scan_for_albums_none(tmp_path, logger):
    _, root = prepare_targets(tmp_path, "testdata_empty_dict")
    assert scan_for_album_folders(logger, root, PLAYLIST) == []


def test_scan_for_new_albums_2_found(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_one", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 1
    assert scan_for_new_albums(targets, logger, root, PLAYLIST) == ["simple_album_b", "simple_album_c"]


def test_scan_for_new_albums_3_found(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    assert scan_for_new_albums(targets, logger, root, PLAYLIST) == SIMPLE


def test_scan_for_new_albums_0_found(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_multiple", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 3
    assert scan_for_new_albums(targets, logger, root, PLAYLIST) == []


def test_add_album_to_targets_success(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    albums = scan_for_new_albums(targets, logger, root, PLAYLIST)
    assert len(albums) == 3
    assert albums[0] == "simple_album_a"

    prev_fn = os.stat(filename)
    album = add_album_to_targets(targets, logger, albums[0], PLAYLIST)
    assert os.stat(filename) != prev_fn

    assert len(targets.targets) == 1
    assert targets.get_target("1") == album
    assert album.album_folder == albums[0]


def test_add_album_to_targets_failure(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    albums = scan_for_new_albums(targets, logger, root, PLAYLIST)
    assert len(albums) == 3
    assert "lalalalalla" not in albums

    prev_fn = os.stat(filename)
    with pytest.raises(ValueError, match="sanity check failed"):
        add_album_to_targets(targets, logger, "lalalalalla", PLAYLIST)
    assert os.stat(filename) == prev_fn
    assert len(targets.targets) == 0


def test_add_album_to_targets_one_additional(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_one", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 1
    albums = scan_for_new_albums(targets, logger, root, PLAYLIST)
    assert len(albums) == 2
    assert albums[0] == "simple_album_b"

    prev_fn = os.stat(filename)
    album = add_album_to_targets(targets, logger, albums[0], PLAYLIST)
    assert os.stat(filename) != prev_fn

    assert len(targets.targets) == 2
    assert targets.get_target("2") == album
    assert album.album_folder == albums[0]


def test_add_new_albums_to_targets_empty(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", SIMPLE)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    assert scan_for_new_albums(targets, logger, root, PLAYLIST) == SIMPLE

    prev_fn = os.stat(filename)
    albums = add_new_albums_to_targets(targets, logger, root, PLAYLIST)
    assert os.stat(filename) != prev_fn

    assert len(albums) == 3
    assert len(targets.targets) == 3
    assert albums == list(targets.targets.values())
    assert [album.album_folder for album in targets.albums()] == SIMPLE


def test_add_new_albums_to_targets_multiple_nested(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_multiple", SIMPLE + NESTED)
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 3
    assert scan_for_new_albums(targets, logger, root, PLAYLIST) == NESTED

    prev_fn = os.stat(filename)
    albums = add_new_albums_to_targets(targets, logger, root, PLAYLIST)
    assert os.stat(filename) != prev_fn

    assert len(albums) == 2
    assert len(targets.targets) == 5
    assert set(albums).issubset(targets.targets.values())
    assert [album.album_folder for album in targets.albums()[3:]] == NESTED


def test_add_new_albums_with_brackets_and_upper_case_mp3(tmp_path, logger):
    """Album and track names with '[...]' and '*.MP3' files are valid albums."""
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict")
    make_album(root, "Serie [Folge 1]", [("01 Intro [Live].mp3", ONE_SECOND_MP3)])
    make_album(root, "upper", [("01.MP3", ONE_SECOND_MP3)])
    targets = Targets(filename, root, logger)
    albums = add_new_albums_to_targets(targets, logger, root, PLAYLIST)
    assert [album.album_folder for album in albums] == ["Serie [Folge 1]", "upper"]
    assert targets.sanity_check() == (True, "")


def test_natural_key():
    names = ["10.mp3", "2.mp3", "1.mp3", "Track 10.mp3", "track 9.mp3", "B.mp3", "a.mp3"]
    assert sorted(names, key=natural_key) == [
        "1.mp3",
        "2.mp3",
        "10.mp3",
        "a.mp3",
        "B.mp3",
        "track 9.mp3",
        "Track 10.mp3",
    ]


def track(filename, tracknumber=None, discnumber=None):
    return {"filename": filename, "tracknumber": tracknumber, "discnumber": discnumber}


def test_playlist_order_by_track_number():
    files = [track("a.mp3", 3), track("b.mp3", 1), track("c.mp3", 2)]
    assert playlist_order(files) == ["b.mp3", "c.mp3", "a.mp3"]


def test_playlist_order_by_disc_and_track_number():
    files = [track("x.mp3", 1, 2), track("y.mp3", 2, 1), track("z.mp3", 1, None)]  # no disc number = disc 1
    assert playlist_order(files) == ["z.mp3", "y.mp3", "x.mp3"]


@pytest.mark.parametrize(
    "files",
    [
        [track("10.mp3", 1), track("2.mp3", 2), track("1.mp3")],  # a track number is missing
        [track("10.mp3", 1), track("2.mp3", 1), track("1.mp3", 2)],  # track numbers are not unique
        [track("10.mp3"), track("2.mp3"), track("1.mp3")],  # no tags at all
    ],
)
def test_playlist_order_by_file_name(files):
    assert playlist_order(files) == ["1.mp3", "2.mp3", "10.mp3"]


def test_playlist_order_empty():
    assert playlist_order([]) == []


def test_write_playlist(tmp_path):
    tracks = ["01 Löwenzahn [Live].mp3", "02.MP3", "10 Ende.mp3"]
    write_playlist(str(tmp_path), PLAYLIST, tracks)
    assert (tmp_path / PLAYLIST).read_text(encoding="utf-8") == "".join(f"{name}\n" for name in tracks)
    assert read_playlist(str(tmp_path / PLAYLIST)) == tracks
    write_playlist(str(tmp_path), PLAYLIST, tracks[:1])  # overwrites the old playlist
    assert read_playlist(str(tmp_path / PLAYLIST)) == tracks[:1]
