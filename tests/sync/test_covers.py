"""Titles and covers of albums: imported from a folder (``tinyjukebox-import-covers``) and synchronized between the
library and the boxes. Covers of radio stations, and what a box knows of an album that it has not fetched."""

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.support import TAG_ALBUM, file_id, wait_for
from tests.sync.test_fill import admin_client
from tests.sync.test_library_and_box import album_id
from tinyjukebox.library.store import knows_album_covers
from tinyjukebox.tools.import_covers import import_covers, plan

JPEG = b"\xff\xd8\xff" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def titles(control):
    return {album["id"]: album["title"] for album in control.list_albums()}


def covers(folder):
    """The stored covers in an album folder, as versions until 0.31 kept them."""
    return sorted(name for name in os.listdir(folder) if name.startswith("cover"))


def test_titles_and_covers_are_imported_into_the_library_and_reach_a_box(bed, tmp_path):
    box = bed.box("box1")
    assert box.synced()
    short, other = album_id(bed, "short"), album_id(bed, "simple_album_b")
    (tmp_path / "album-short.jpg").write_bytes(JPEG)
    captions = {
        short: {"image": "album-short.jpg", "title": " Kurz und gut "},
        other: {"title": "Nur ein Titel"},
        "99": {"image": "album-short.jpg", "title": "Gibt es nicht"},
    }
    (tmp_path / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
    client = admin_client(bed)
    said: list[str] = []
    asked = []

    def refuse(steps):
        asked.append(steps)
        return False

    # refused: nothing is changed
    zero = {"titles": 0, "covers": 0, "failed": 0}
    assert import_covers(client, str(tmp_path), refuse, said.append) == zero
    steps = [
        ("album", short, "Kurz und gut", str(tmp_path / "album-short.jpg")),
        ("album", other, "Nur ein Titel", None),
    ]
    assert asked == [steps]
    assert said == ["album 99: not there - skipped"]
    assert "title" not in bed.catalog("target")[short]

    assert import_covers(client, str(tmp_path), say=said.append) == {"titles": 2, "covers": 1, "failed": 0}
    bed.library.settle()
    assert bed.catalog("target")[short]["title"] == "Kurz und gut"
    assert bed.admin().get(f"/api/albums/{short}/cover").content == JPEG
    # the cover is an image of the images folder, named by the album - no file of the album
    cover = bed.catalog("target")[short]["cover"]
    assert os.listdir(bed.folder / "library" / "images") == [cover]
    assert covers(bed.folder / "library" / "albums" / "short") == []
    # the box has titles and cover with the catalog and fetches the image like the cover of a station
    assert wait_for(lambda: titles(box.control) == {short: "Kurz und gut", other: "Nur ein Titel"}, timeout=10)
    assert wait_for(lambda: images(box) == [cover], timeout=10)
    assert box.control.get_cover(short) == (JPEG, "image/jpeg")
    assert covers(box.folder / "albums" / "short") == []
    # again: the titles are there already
    assert import_covers(client, str(tmp_path), say=said.append) == {"titles": 0, "covers": 1, "failed": 0}
    assert wait_for(lambda: images(box) == [bed.catalog("target")[short]["cover"]] != [cover], timeout=10)


@pytest.mark.parametrize("title_first", [True, False])
def test_title_and_cover_changed_on_a_box_reach_the_library_and_the_other_box(bed, title_first):
    """Two changes of one album in a row: the pass that tells the library the first one runs while the second one is
    made. Both are changes of the entry of the album, and neither is lost. The other box has the cover of an album
    whose files it never fetched."""
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    short = album_id(bed, "short")
    if title_first:
        box1.control.update_album(short, "Vom Kind umbenannt")
        cover = box1.control.set_cover(short, PNG)["cover"]
    else:
        cover = box1.control.set_cover(short, PNG)["cover"]
        box1.control.update_album(short, "Vom Kind umbenannt")
    assert box1.synced()
    assert wait_for(lambda: bed.catalog("target")[short].get("title") == "Vom Kind umbenannt", timeout=10)
    assert bed.catalog("target")[short]["cover"] == cover
    assert wait_for(lambda: bed.library.control.get_cover(short) == (PNG, "image/png"), timeout=10)
    assert box1.control.get_cover(short) == (PNG, "image/png")
    assert wait_for(lambda: titles(box2.control).get(short) == "Vom Kind umbenannt", timeout=10)
    assert wait_for(lambda: images(box2) == [cover], timeout=10)
    assert box2.files("short") == []
    assert box2.control.get_cover(short) == (PNG, "image/png")
    assert not bed.library.store.journal.records("conflict")
    # replaced in the library: the old image disappears everywhere
    with bed.as_admin() as library:
        new = library.set_cover(short, JPEG)["cover"]
    assert wait_for(lambda: images(box1) == [new] and images(box2) == [new], timeout=10)
    assert box1.control.get_cover(short) == (JPEG, "image/jpeg")


def test_files_of_an_album_changed_on_a_box_reach_the_library_though_its_entry_is_what_it_was(bed):
    """A playlist written anew or an image put into the folder changes the files of an album and nothing in its
    entry. The library must take the list of files all the same - otherwise the box would take the older files of
    the library for a change made there and undo its own."""
    box = bed.box("box1")
    assert box.synced()
    short = album_id(bed, "short")
    in_library = bed.folder / "library" / "albums" / "short"
    # only a file that is not played
    (box.folder / "albums" / "short" / "folder.png").write_bytes(PNG)
    box.sync.album_changed(short)
    assert wait_for(lambda: os.path.isfile(in_library / "folder.png"), timeout=10)
    assert box.synced()
    # the title, and when that is settled another file: the entry of the second change is the one of the library
    box.control.update_album(short, "Kurz und gut")
    assert box.synced()
    assert bed.catalog("target")[short]["title"] == "Kurz und gut"
    os.remove(box.folder / "albums" / "short" / "folder.png")
    (box.folder / "albums" / "short" / "folder.jpg").write_bytes(JPEG)
    box.sync.album_changed(short)
    assert wait_for(lambda: os.path.isfile(in_library / "folder.jpg"), timeout=10)
    assert wait_for(lambda: not os.path.exists(in_library / "folder.png"), timeout=10)
    assert box.synced()
    assert sorted(name for name in box.files("short") if name.startswith("folder")) == ["folder.jpg"]
    # a track more and the playlist written anew
    shutil.copy(box.folder / "albums" / "short" / "1.mp3", box.folder / "albums" / "short" / "4.mp3")
    box.control.rebuild_playlist(short)
    assert wait_for(lambda: os.path.isfile(in_library / "4.mp3"), timeout=10)
    assert box.synced()
    assert bed.library.store.manifest(short)["order"] == ["1.mp3", "2.mp3", "3.mp3", "4.mp3"]
    assert "4.mp3" in box.files("short")
    assert bed.catalog("target")[short]["title"] == "Kurz und gut"
    assert not bed.library.store.journal.records("conflict")


def test_plan_says_what_cannot_be_done(tmp_path):
    albums = {"1": {"id": "1", "title": "Eins"}, "2": {"id": "2", "title": "zwei"}}
    with pytest.raises(ValueError, match=r"captions\.json"):
        plan(str(tmp_path), albums)
    (tmp_path / "eins.png").write_bytes(PNG)
    captions = {
        "1": {"title": "Eins", "image": "eins.png"},  # the title is set already
        "2": {"title": "Zwei", "image": "fehlt.jpg"},
        "3": {"title": "Drei"},
    }
    (tmp_path / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
    said: list[str] = []
    steps = [("album", "1", None, str(tmp_path / "eins.png")), ("album", "2", "Zwei", None)]
    assert plan(str(tmp_path), albums, said.append) == steps
    assert said == [
        "album 2: image 'fehlt.jpg' not found in the folder - the image is skipped",
        "album 3: not there - skipped",
    ]
    # an image outside the folder is not sent
    captions = {"1": {"image": "../eins.png"}}
    (tmp_path / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
    assert plan(str(tmp_path), albums, said.append) == []
    (tmp_path / "captions.json").write_text('["1"]', encoding="utf-8")
    with pytest.raises(ValueError, match="must map album ids"):
        plan(str(tmp_path), albums)


def test_a_box_knows_tracks_and_playing_time_of_an_album_that_it_has_not_fetched(bed):
    assert bed.box("box1").synced()
    other = album_id(bed, "simple_album_b")
    # the library read the tracks as soon as it had them
    described = bed.library.store.entry("target", other)["album"]
    assert described["tracks"] == 3
    assert described["length"] == pytest.approx(12, abs=0.5)
    assert {album["id"]: album["track_count"] for album in bed.library.control.list_albums()}[other] == 3
    box = bed.box("box2", empty=True)
    assert box.synced()
    assert box.files("simple_album_b") == []
    album = next(album for album in box.control.list_albums() if album["id"] == other)
    assert (album["track_count"], album["length"]) == (3, described["length"])


def test_the_cover_of_a_station_is_the_image_named_after_its_id(bed, tmp_path):
    with bed.as_admin() as library:
        station = library.add_radio("FM4", "https://example.org/fm4.m3u")["id"]
    (tmp_path / "captions.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"radio-{station}.jpg").write_bytes(JPEG)
    (tmp_path / "radio-999.png").write_bytes(PNG)
    said: list[str] = []
    counts = import_covers(admin_client(bed), str(tmp_path), say=said.append)
    assert counts == {"titles": 0, "covers": 1, "failed": 0}
    assert said == ["radio station 999: not there - skipped"]
    assert bed.library.control.get_radio_cover(station) == (JPEG, "image/jpeg")


def images(box):
    return list(box.control.images.names())


def test_the_cover_of_a_station_reaches_the_library_and_the_other_box(bed):
    box1, box2 = bed.box("box1"), bed.box("box2", empty=True)
    assert box1.synced()
    assert box2.synced()
    box1.control.add_radio("FM4", "https://example.org/fm4.m3u")
    assert box1.synced()
    final = next(key for key, target in bed.catalog("target").items() if target.get("name") == "FM4")
    cover = box1.control.set_radio_cover(final, JPEG)["cover"]
    assert cover.startswith("station-")
    assert wait_for(lambda: bed.library.control.get_radio(final)["cover"] == cover, timeout=10)
    assert bed.library.control.get_radio_cover(final) == (JPEG, "image/jpeg")
    assert wait_for(lambda: box2.control.get_radio(final)["cover"] == cover, timeout=10)
    assert box2.control.get_radio_cover(final) == (JPEG, "image/jpeg")
    # replaced in the library: the old image disappears everywhere
    with bed.as_admin() as library:
        new = library.set_radio_cover(final, PNG)["cover"]
    assert wait_for(lambda: box1.control.get_radio(final)["cover"] == new and images(box1) == [new], timeout=10)
    assert wait_for(lambda: box2.control.get_radio(final)["cover"] == new and images(box2) == [new], timeout=10)


def test_the_library_adds_tracks_and_playing_time_to_the_albums_it_has_already(bed):
    """The first start with a version that notes them: the catalog of the version before has neither."""
    box = bed.box("box1")
    assert box.synced()
    other = album_id(bed, "simple_album_b")
    port = bed.library.api.port
    bed.library.stop()
    catalog = bed.folder / "library" / "data" / "catalog" / "targets.json"
    data = json.loads(catalog.read_text(encoding="utf-8"))
    for entry in data["entries"].values():
        del entry["album"]["tracks"], entry["album"]["length"]
    catalog.write_text(json.dumps(data), encoding="utf-8")
    bed.library = bed._library(port)
    bed.library.start()
    described = bed.library.store.entry("target", other)["album"]
    assert (described["tracks"], described["length"]) == (3, pytest.approx(12, abs=0.5))
    # ... and the box gets them with the catalog
    assert wait_for(lambda: box.sync.data.album(other).get("tracks") == 3, timeout=10)


def test_a_library_and_a_box_of_0_28_go_on_with_the_covers_of_the_stations_only(bed):
    """Until 0.28 the list of the images was part of the catalog file of the card images of tags, and a box kept the
    covers in the folder ``cards``. The list becomes a file of its own, the covers stay, the rest is not used."""
    box = bed.box("box1")
    assert box.synced()
    with bed.as_admin() as library:
        station = library.add_radio("FM4", "https://example.org/fm4.m3u")["id"]
        cover = library.set_radio_cover(station, JPEG)["cover"]
    assert wait_for(lambda: images(box) == [cover], timeout=10)
    root = bed.folder / "library"
    listed = json.loads((root / "data" / "catalog" / "images.json").read_text())["images"]
    # the library as 0.28 left it: the list in cards.json, with a card image and the assignment to a tag
    (root / "images" / "album-14.jpg").write_bytes(PNG)
    old = {"format": 1, "entries": {TAG_ALBUM: {"value": None, "rev": 2}}, "images": {**listed, "album-14.jpg": {}}}
    port = bed.library.api.port
    bed.library.stop()
    (root / "data" / "catalog" / "images.json").unlink()
    (root / "data" / "catalog" / "cards.json").write_text(json.dumps(old))
    # ... and the box: the covers in the folder cards, a copy of cards.json
    folder = Path(box.control.images.folder)
    copy = folder.parent.parent / "sync" / "copy"
    box.stop()
    folder.rename(folder.parent / "cards")
    (folder.parent / "cards" / "album-14.jpg").write_bytes(PNG)
    (copy / "images.json").rename(copy / "cards.json")

    bed.library = bed._library(port)
    bed.library.start()
    assert not (root / "data" / "catalog" / "cards.json").exists()
    assert json.loads((root / "data" / "catalog" / "images.json").read_text())["images"] == listed
    assert bed.library.control.get_radio_cover(station) == (JPEG, "image/jpeg")
    assert (root / "images" / "album-14.jpg").exists()  # nothing is deleted

    box.start()
    assert images(box) == [cover]
    assert sorted(os.listdir(folder.parent / "cards")) == ["album-14.jpg"]
    assert not (copy / "cards.json").exists()
    assert box.synced()
    assert box.control.get_radio_cover(station) == (JPEG, "image/jpeg")
    assert images(box) == [cover]


def test_the_library_moves_the_stored_covers_and_a_box_takes_that_over_without_fetching_a_track(bed):
    """Until 0.31 the cover of an album was ``cover.jpg`` in its folder, a file of the album. The library moves it
    into the images folder at its first start; the album has a new list of files. A box that has the album removes
    its own stored cover and keeps every track it has."""
    box1, box2 = bed.box("box1"), bed.box("box2", empty=True)
    assert box1.synced()
    assert box2.synced()
    short = album_id(bed, "short")
    other = album_id(bed, "simple_album_b")
    root = bed.folder / "library"
    in_library, on_box = root / "albums" / "short", box1.folder / "albums" / "short"
    # as 0.31 had it: the cover is a file of the album, on both sides, and its manifest lists it
    (on_box / "cover.jpg").write_bytes(JPEG)
    (on_box / "folder.png").write_bytes(PNG)
    box1.sync.album_changed(short)
    assert wait_for(lambda: os.path.isfile(in_library / "cover.jpg"), timeout=10)
    assert box1.synced()
    before = bed.library.store.entry("target", short)
    assert "cover.jpg" in [item["name"] for item in bed.library.store.manifest(short)["files"]]
    tracks = {name: file_id(on_box / name) for name in box1.files("short") if name.endswith(".mp3")}
    in_library_tracks = {name: file_id(in_library / name) for name in tracks}

    bed.restart_library()
    after = bed.library.store.entry("target", short)
    cover = after["value"]["cover"]
    assert os.listdir(root / "images") == [cover]
    assert (root / "images" / cover).read_bytes() == JPEG
    assert covers(in_library) == []
    assert os.path.isfile(in_library / "folder.png")  # only the stored cover is moved
    names = [item["name"] for item in bed.library.store.manifest(short)["files"]]
    assert "cover.jpg" not in names
    assert "folder.png" in names
    assert after["album"]["checksum"] != before["album"]["checksum"]
    assert after["album"]["identity"] == before["album"]["identity"]
    assert (after["album"]["lacks"], after["album"]["tracks"]) == ([], 3)
    assert after["album"]["length"] == before["album"]["length"]
    assert after["rev"] > before["rev"]
    assert "cover" not in bed.catalog("target")[other]  # an album without a stored cover is what it was
    assert bed.library.control.get_cover(short) == (JPEG, "image/jpeg")
    assert {name: file_id(in_library / name) for name in tracks} == in_library_tracks

    # the box that has the album: its stored cover goes, the image comes, no track is fetched again
    assert wait_for(lambda: images(box1) == [cover] and covers(on_box) == [], timeout=10)
    assert box1.synced()
    assert {name: file_id(on_box / name) for name in tracks} == tracks
    assert os.path.isfile(on_box / "folder.png")
    assert box1.control.get_cover(short) == (JPEG, "image/jpeg")
    assert box1.sync.files.lacking(short) == []
    # ... and the library does not get the file back from it
    assert covers(in_library) == []
    assert bed.library.store.entry("target", short)["album"]["lacks"] == []
    # the box that never fetched the album has its cover all the same
    assert wait_for(lambda: images(box2) == [cover], timeout=10)
    assert box2.control.get_cover(short) == (JPEG, "image/jpeg")
    assert not bed.library.store.journal.records("conflict")
    # the next start finds nothing to move
    revision = bed.library.store.entry("target", short)["rev"]
    bed.restart_library()
    assert bed.library.store.entry("target", short)["rev"] == revision
    assert os.listdir(root / "images") == [cover]


def test_a_box_before_0_32_gets_the_catalog_without_the_covers_of_the_albums(bed):
    """Such a box does not know that an album names its cover: it would not keep the file of the targets at its
    start, fetch the image of every album with every pass - and drop the cover with every change of the album."""
    assert knows_album_covers("0.32.0")
    assert knows_album_covers("1.0.0")
    assert knows_album_covers("")  # not said: as this library
    assert not knows_album_covers("0.31.0")
    box, new = bed.box("box1"), bed.box("box2", empty=True)
    assert box.synced()
    assert new.synced()
    box.sync.version = box.sync.data.version = "0.31.0"
    short = album_id(bed, "short")
    with bed.as_admin() as library:
        station = library.add_radio("FM4", "https://example.org/fm4.m3u")["id"]
        of_station = library.set_radio_cover(station, PNG)["cover"]
        cover = library.set_cover(short, JPEG)["cover"]
    assert wait_for(lambda: images(new) == sorted([cover, of_station]), timeout=10)
    assert box.synced()
    assert images(box) == [of_station]
    assert sorted(os.listdir(box.control.images.folder)) == [of_station]
    copied = box.sync.data.working("target")
    assert "cover" not in copied[short]
    assert copied[station]["cover"] == of_station
    store = bed.library.store
    assert store.catalog_index(album_covers=False) != store.catalog_index()
    assert store.catalog_index(album_covers=False)["tags.json"] == store.catalog_index()["tags.json"]
    # a change of the album by this box keeps the cover
    box.control.update_album(short, "Von der alten Box")
    assert box.synced()
    assert bed.catalog("target")[short] == {**bed.catalog("target")[short], "title": "Von der alten Box"}
    assert bed.catalog("target")[short]["cover"] == cover
    assert wait_for(lambda: titles(new.control).get(short) == "Von der alten Box", timeout=10)
    assert new.control.get_cover(short) == (JPEG, "image/jpeg")
    assert not store.journal.records("conflict")


def test_a_library_before_0_32_refuses_the_cover_of_an_album_and_the_pass_goes_on(bed, monkeypatch):
    """The library needs the version first. A box that has it earlier keeps the cover it was given and settles the
    rest: the journal is delivered."""
    box = bed.box("box1")
    assert box.synced()
    monkeypatch.setattr("tinyjukebox.library.store.is_cover", lambda name: name.startswith("station-"))
    short = album_id(bed, "short")
    cover = box.control.images.add(JPEG, "album-")
    box.sync.journal("alive")
    box.sync.link.wait_pass(5)
    assert wait_for(lambda: box.sync.data.journal_lines() == [], timeout=10)
    assert images(box) == [cover]
    assert short in bed.catalog("target")
