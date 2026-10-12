"""Broken files of albums: the library checks the tracks it has, a box reports a track that its decoder found
damaged, and the library collects both."""

from tests.support import TAG_ALBUM, ApiClient, wait_for
from tests.sync.test_library_and_box import album_id
from tinyjukebox.library.store import NO_MP3

DECODER_SAID = "2.mp3: Invalid data found when processing input"


def marked(client: ApiClient) -> dict[str, int]:
    """The albums as the list of the page marks them: id -> number of broken files."""
    return {album["id"]: album["broken"] for album in client.get("/api/albums").json}


def test_the_library_notes_a_track_that_is_no_mp3_file(bed):
    box = bed.box("box1")
    assert box.synced()
    short, other = album_id(bed, "short"), album_id(bed, "simple_album_b")
    store, client = bed.library.store, bed.admin()
    assert store.broken() == {}
    assert marked(client) == {short: 0, other: 0}

    track = bed.folder / "library" / "albums" / "short" / "2.mp3"
    good = track.read_bytes()
    track.write_bytes(b"no mp3")
    store.album_changed(short, "test")
    assert store.broken() == {short: {"2.mp3": {"library": NO_MP3, "boxes": {}}}}
    assert marked(client) == {short: 1, other: 0}
    assert client.get(f"/api/albums/{short}").json["broken"] == [{"file": "2.mp3", "library": NO_MP3, "boxes": []}]
    assert client.get(f"/api/albums/{other}").json["broken"] == []

    # what was found is kept, not found again at every start
    bed.restart_library()
    assert bed.library.store.broken() == {short: {"2.mp3": {"library": NO_MP3, "boxes": {}}}}

    # the file is replaced by a good one: nothing is left
    track.write_bytes(good)
    bed.library.store.album_changed(short, "test")
    assert bed.library.store.broken() == {}


def test_a_box_reports_a_track_that_its_decoder_found_damaged(bed):
    box = bed.box("box1")
    assert box.synced()
    short = album_id(bed, "short")
    box.place(TAG_ALBUM)
    # the state is entered before the album it plays is there
    assert wait_for(lambda: box.service.stmoperation.get_album() is not None)
    # the player of the tests decodes nothing: this is what the real one calls when the decoder found a file damaged
    box.service.stmoperation.get_album()._track_failed(1, DECODER_SAID)
    box.place(None)
    assert box.synced()
    assert wait_for(lambda: not box.sync.data.journal_lines(1))

    store, client = bed.library.store, bed.admin()
    box_id = store.boxes()[0]["id"]
    found = store.broken()[short]["2.mp3"]
    assert list(found) == ["boxes"]  # the library itself found nothing
    assert found["boxes"][box_id]["error"] == DECODER_SAID
    assert marked(client)[short] == 1
    (reported,) = client.get(f"/api/albums/{short}").json["broken"]
    assert (reported["file"], reported["library"]) == ("2.mp3", None)
    assert [(report["box"], report["error"]) for report in reported["boxes"]] == [(box_id, DECODER_SAID)]

    # a report that says nothing about the file is dropped by an admin
    album = client.delete(f"/api/library/albums/{short}/broken")
    assert album.status == 200
    assert album.json["broken"] == []
    assert store.broken() == {}
    assert client.delete("/api/library/albums/99/broken").status == 404


def test_a_report_ends_with_the_file_it_is_about(bed):
    box = bed.box("box1")
    assert box.synced()
    short = album_id(bed, "short")
    store = bed.library.store
    box_id = store.boxes()[0]["id"]
    clock = {"time": "2026-10-09T10:00:00Z", "synchronized": True}
    store.reported(box_id, {"n": 1, "event": "broken", "album": short, "file": "2.mp3", "error": "x", "clock": clock})
    store.reported(box_id, {"n": 2, "event": "broken", "album": short, "file": "9.mp3", "error": "x", "clock": clock})
    store.reported(box_id, {"n": 3, "event": "broken", "album": "99", "file": "1.mp3", "error": "x", "clock": clock})
    # only what the library has: no file 9.mp3, no album 99
    assert store.broken() == {short: {"2.mp3": {"boxes": {box_id: {"when": "2026-10-09T10:00:00Z", "error": "x"}}}}}

    # another file under this name: what was said about the old one does not apply to it
    track = bed.folder / "library" / "albums" / "short" / "2.mp3"
    other = sorted((bed.folder / "library" / "albums" / "simple_album_b").glob("*.mp3"))[0]
    track.write_bytes(other.read_bytes())
    store.album_changed(short, "test")
    assert store.broken() == {}


def test_only_admins_drop_reports(bed, certificate):
    assert bed.admin().post("/api/users", {"username": "anna", "password": "anna-password"}).status == 201
    user = ApiClient(bed.library.api.port, certificate[0]).login("anna", "anna-password")
    assert user.delete("/api/library/albums/1/broken").status == 403
