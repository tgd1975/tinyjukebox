"""TargetAlbum (an album folder with mp3 files and a playlist) and read_playlist."""

import os
import shutil

import pytest

from tests.support import DATA, ONE_SECOND_MP3, album_dict, make_album, noop
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.target import MAX_ALBUM_TITLE_LENGTH, TargetAlbum, check_album_title, read_playlist
from tinyjukebox.metadata import find_cover, store_cover

SOURCE_ALBUMS = DATA / "targets" / "albums"


@pytest.fixture
def root(tmp_path):
    """Copy of tests/data/targets/albums - the albums root folder."""
    shutil.copytree(SOURCE_ALBUMS, tmp_path / "albums")
    return str(tmp_path / "albums")


@pytest.fixture
def album(root, logger):
    return TargetAlbum(album_dict(), root, logger, noop)


def test_init_empty(root, logger):
    with pytest.raises(ValueError, match="'target-id' not in dict"):
        TargetAlbum({}, root, logger, noop)
    with pytest.raises(ValueError, match="'target-type' not in dict"):
        TargetAlbum({"target-id": "id"}, root, logger, noop)
    with pytest.raises(ValueError, match="'target-id' not in dict"):
        TargetAlbum({"target-type": TargetType.album.name}, root, logger, noop)


def test_init_dict(root, logger):
    json = album_dict()
    album = TargetAlbum(json, root, logger, noop)
    assert album.target_id == json["target-id"]
    assert album.full_album_folder == os.path.join(root, json["album-folder"])
    assert album.album_folder == json["album-folder"]
    assert album.root_folder == root
    assert album.created == json["created"]
    assert album.playlist == json["playlist"]
    assert album.target_type == TargetType.album


def test_init_dict_no_created(root, logger):
    json = album_dict(created=None)
    album = TargetAlbum(json, root, logger, noop)
    assert album.target_id == json["target-id"]
    assert album.full_album_folder == os.path.join(root, json["album-folder"])
    assert album.album_folder == json["album-folder"]
    assert album.root_folder == root
    assert album.created is not None
    assert album.playlist == json["playlist"]
    assert album.target_type == TargetType.album


def test_get_tracks(album, root):
    tracks = [os.path.join(root, "simple_album_a", f"{i}.mp3") for i in range(1, 6)]
    assert album._tracks is None
    assert album.get_tracks() == tracks
    album._tracks = tracks[:1]
    assert album.get_tracks() == tracks[:1]  # cached
    assert album.get_tracks(force_update=True) == tracks


def test_get_tracks_follows_the_playlist(root, logger):
    """An album may be listed before its files are there, e.g. while they come from the library."""
    album = TargetAlbum(album_dict("later"), root, logger, noop)
    assert album.get_tracks() == []
    make_album(root, "later", [("1.mp3", ONE_SECOND_MP3)])
    assert album.get_tracks() == [os.path.join(root, "later", "1.mp3")]
    with open(os.path.join(root, "later", "playlist.m3u"), "w") as f:
        f.write("1.mp3\n2.mp3\n")
    assert [os.path.basename(track) for track in album.get_tracks()] == ["1.mp3", "2.mp3"]


def test_to_dict(root, logger):
    json = album_dict()
    album = TargetAlbum(json, root, logger, noop)
    assert album.to_dict() == json


def test_title(root, logger):
    """An entry made before albums had a title has none, and nothing is added to it."""
    written = []
    target = TargetAlbum(album_dict(), root, logger, lambda: written.append(target.to_dict()))
    assert target.title is None
    assert "title" not in target.to_dict()
    target.set_title("  Album A (Hörspiel) ")
    assert target.title == "Album A (Hörspiel)"
    assert written == [{**album_dict(), "created": target.created, "title": "Album A (Hörspiel)"}]
    assert target.to_text().endswith("playlist: playlist.m3u, title: Album A (Hörspiel)")
    target.set_title("Album A (Hörspiel)")  # unchanged: nothing is written
    assert len(written) == 1
    for title in (None, "", "   ", 7, "x" * (MAX_ALBUM_TITLE_LENGTH + 1), "zwei\nZeilen"):
        with pytest.raises(ValueError, match="title must not"):
            target.set_title(title)
    assert target.title == "Album A (Hörspiel)"
    assert check_album_title("x" * MAX_ALBUM_TITLE_LENGTH) == "x" * MAX_ALBUM_TITLE_LENGTH
    # loaded with the entry, and a new album with a title has a valid one
    assert TargetAlbum(album_dict(title="Aus der Datei"), root, logger, noop).title == "Aus der Datei"
    assert TargetAlbum.create_target(album_dict(title=" Neu "), root, logger, noop).title == "Neu"
    with pytest.raises(ValueError, match="title must not be empty"):
        TargetAlbum.create_target(album_dict(title=" "), root, logger, noop)


def test_store_cover(root):
    """A stored cover wins over every other cover of the album and replaces the one stored before."""
    folder = os.path.join(root, "simple_album_a")
    jpeg, png = b"\xff\xd8\xff" + b"1" * 10, b"\x89PNG\r\n\x1a\n" + b"2" * 10
    with open(os.path.join(folder, "folder.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff" + b"0" * 10)
    assert store_cover(folder, png) == "cover.png"
    assert find_cover(folder, []) == (png, "image/png")
    assert store_cover(folder, jpeg) == "cover.jpg"
    assert find_cover(folder, []) == (jpeg, "image/jpeg")
    assert sorted(n for n in os.listdir(folder) if not n.endswith((".mp3", ".m3u"))) == ["cover.jpg", "folder.jpg"]
    with pytest.raises(ValueError, match="only jpeg and png"):
        store_cover(folder, b"RIFF0000WEBP")
    assert find_cover(folder, []) == (jpeg, "image/jpeg")


def test_to_text(album):
    created = album.created
    assert album.to_text() == (
        f"id: id, type: album, created: {created}, album-folder: simple_album_a, playlist: playlist.m3u"
    )
    assert album.to_text(compact=False) == (
        f"id: id, type: album, created: {created}, album-folder: simple_album_a, playlist: playlist.m3u, "
        "track-count: 5, tracks: ['simple_album_a/1.mp3', 'simple_album_a/2.mp3', "
        "'simple_album_a/3.mp3', 'simple_album_a/4.mp3', 'simple_album_a/5.mp3']"
    )


def test_sanity_check_fields_ok(album):
    assert album.sanity_check_fields() == (True, "")


def test_sanity_check_fields_created(album):
    created = album.created
    assert album.sanity_check_fields()[0]
    album.created = "test"
    assert album.sanity_check_fields() == (False, "sanity_check - created timestamp error 'test'")
    album.created = None
    assert album.sanity_check_fields() == (False, "sanity_check - created timestamp error 'None'")
    album.created = created
    assert album.sanity_check_fields() == (True, "")


def test_sanity_check_fields_target_type(album):
    assert album.sanity_check_fields()[0]
    album.target_type = None
    assert album.sanity_check_fields() == (False, "sanity_check - target type not set or not expected value 'None'")
    album.target_type = "Hallo"
    assert album.sanity_check_fields() == (False, "sanity_check - target type not set or not expected value 'Hallo'")
    album.target_type = "album"  # the name instead of the enum
    assert album.sanity_check_fields() == (False, "sanity_check - target type not set or not expected value 'album'")
    album.target_type = TargetType.album
    assert album.sanity_check_fields() == (True, "")


def test_sanity_check_fields_target_id(album):
    assert album.sanity_check_fields()[0]
    album.target_id = None
    assert album.sanity_check_fields() == (False, "sanity_check - target-id not set.")
    album.target_id = ""
    assert album.sanity_check_fields() == (False, "sanity_check - target-id not set.")
    album.target_id = "id"
    assert album.sanity_check_fields() == (True, "")


def test_sanity_check_fields_multiple(album):
    assert album.sanity_check_fields()[0]
    album.target_id = None
    album.target_type = None
    album.created = None
    assert album.sanity_check_fields() == (
        False,
        "sanity_check - created timestamp error 'None'\n"
        "sanity_check - target type not set or not expected value 'None'\n"
        "sanity_check - target-id not set.",
    )


def test_sanity_check_files(album, root):
    assert album.sanity_check_files() == (True, "")
    album.full_album_folder = os.path.join(root, "_nofolder_")
    assert album.sanity_check_files() == (False, f"sanity_check - full_album_folder '{root}/_nofolder_' not found.")
    album.full_album_folder = None
    assert album.sanity_check_files() == (False, "sanity_check - full_album_folder 'None' not found.")
    album.full_album_folder = os.path.join(root, "noplaylist")
    assert album.sanity_check_files() == (
        False,
        f"sanity_check - playlist not found in full_album_folder '{root}/noplaylist'.",
    )
    album.full_album_folder = os.path.join(root, "nomp3s")
    assert album.sanity_check_files() == (
        False,
        f"sanity_check - no mp3-files found in full_album_folder '{root}/nomp3s'.",
    )


def test_sanity_check_tracks_ok(album):
    assert album.sanity_check_tracks() == (True, "")


def test_sanity_check_tracks_failmixed(root, logger):
    album = TargetAlbum(album_dict("failmixed"), root, logger, noop)
    assert album.sanity_check_tracks() == (
        False,
        f"sanity_check - file {root}/failmixed/1.mp3 not valid MP3.\n"
        f"sanity_check - file {root}/failmixed/2.mp3 not valid MP3.\n"
        f"sanity_check - file {root}/failmixed/3.mp3 not found.",
    )


def test_sanity_check_ok(album):
    assert album.sanity_check() == (True, "")


def test_sanity_check_noplaylist(root, logger):
    album = TargetAlbum(album_dict("noplaylist"), root, logger, noop)
    album.target_id = None
    assert album.sanity_check() == (
        False,
        "sanity_check - target-id not set.\n"
        f"sanity_check - playlist not found in full_album_folder '{root}/noplaylist'.",
    )


def test_sanity_check_failmixed(root, logger):
    album = TargetAlbum(album_dict("failmixed"), root, logger, noop)
    album.target_id = None
    assert album.sanity_check() == (
        False,
        "sanity_check - target-id not set.\n"
        f"sanity_check - file {root}/failmixed/1.mp3 not valid MP3.\n"
        f"sanity_check - file {root}/failmixed/2.mp3 not valid MP3.\n"
        f"sanity_check - file {root}/failmixed/3.mp3 not found.",
    )


def test_create_target_album_ok(root, logger):
    album = TargetAlbum.create_target(album_dict(created=None), root, logger, noop)
    assert album is not None


def test_create_target_album_wrong_type(root, logger):
    with pytest.raises(ValueError, match="wrong target type"):
        TargetAlbum.create_target(album_dict(created=None, **{"target-type": "wrong"}), root, logger, noop)


def test_create_target_album_no_update_json(tmp_path, logger):
    with pytest.raises(ValueError, match="update_json method is None"):
        TargetAlbum.create_target(album_dict(created=None), str(tmp_path), logger, None)


def test_create_target_album_sanity_failed(root, logger):
    with pytest.raises(ValueError, match="sanity check failed"):
        TargetAlbum.create_target(album_dict("failmixed", created=None), root, logger, noop)


def test_playlist_comments_and_self_reference_are_skipped(library, logger):
    """m3u directives (#EXTM3U, #EXTINF) and the playlist itself (created with 'ls > playlist.m3u') are no tracks."""
    folder = os.path.join(library.albums_folder, "short")
    with open(os.path.join(folder, "playlist.m3u"), "w") as f:
        f.write("#EXTM3U\n#EXTINF:1,Eins\n1.mp3\n2.mp3\n\n3.mp3\nplaylist.m3u\n")
    album = TargetAlbum.create_target(
        {"target-id": "7", "target-type": "album", "album-folder": "short", "playlist": "playlist.m3u"},
        library.albums_folder,
        logger,
        lambda: None,
    )
    assert [os.path.basename(t) for t in album.get_tracks()] == ["1.mp3", "2.mp3", "3.mp3"]


def test_brackets_in_album_and_track_names(tmp_path, logger):
    """'[...]' is a glob pattern - the sanity checks must find such albums and tracks (no glob)."""
    folder = make_album(
        tmp_path, "Serie [Folge 1]", [("01 Intro [Live].mp3", ONE_SECOND_MP3), ("02 [Teil 2].mp3", ONE_SECOND_MP3)]
    )
    album = TargetAlbum(album_dict("Serie [Folge 1]"), str(tmp_path), logger, noop)
    assert album.sanity_check_files() == (True, "")
    assert album.sanity_check_tracks() == (True, "")
    assert album.sanity_check() == (True, "")
    assert album.get_tracks() == [os.path.join(folder, "01 Intro [Live].mp3"), os.path.join(folder, "02 [Teil 2].mp3")]
    assert TargetAlbum.create_target(album_dict("Serie [Folge 1]"), str(tmp_path), logger, noop)


def test_upper_case_mp3_extension(tmp_path, logger):
    make_album(tmp_path, "upper", [("01.MP3", ONE_SECOND_MP3), ("02.MP3", ONE_SECOND_MP3)])
    album = TargetAlbum(album_dict("upper"), str(tmp_path), logger, noop)
    assert album.sanity_check_files() == (True, "")
    assert album.sanity_check() == (True, "")


def test_read_playlist_skips_comments_and_blank_lines(tmp_path):
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n#EXTINF:12,Artist - Eins\n  1.mp3  \n\n# comment\n2.mp3\r\n")
    assert read_playlist(str(playlist)) == ["1.mp3", "2.mp3"]


def test_read_playlist_skips_itself(tmp_path):
    playlist = tmp_path / "list.m3u"
    playlist.write_text("1.mp3\nlist.m3u\n2.mp3\n")
    assert read_playlist(str(playlist)) == ["1.mp3", "2.mp3"]
    assert read_playlist(str(playlist), "other.m3u") == ["1.mp3", "list.m3u", "2.mp3"]


def test_read_playlist_utf8_with_bom(tmp_path):
    playlist = tmp_path / "playlist.m3u"
    playlist.write_bytes("Löwenzahn.mp3\nÄrger.mp3\n".encode("utf-8-sig"))
    assert read_playlist(str(playlist)) == ["Löwenzahn.mp3", "Ärger.mp3"]


def test_read_playlist_cp1252_fallback(tmp_path):
    """Playlists saved on windows are not utf-8."""
    playlist = tmp_path / "playlist.m3u"
    playlist.write_bytes("Löwenzahn – Folge 1.mp3\nÄrger.mp3\n".encode("cp1252"))
    assert read_playlist(str(playlist)) == ["Löwenzahn – Folge 1.mp3", "Ärger.mp3"]


def test_read_playlist_missing_file(tmp_path, logger):
    with pytest.raises(FileNotFoundError):
        read_playlist(str(tmp_path / "missing.m3u"))
    # the album handles a missing playlist gracefully
    album = TargetAlbum(album_dict("nothing"), str(tmp_path), logger, noop)
    assert album.get_tracks() == []
