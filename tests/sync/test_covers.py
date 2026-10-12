"""Titles and covers of albums: imported from a folder (``tinyjukebox-import-covers``) and synchronized between the
library and the boxes. Covers of radio stations, and what a box knows of an album that it has not fetched."""

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.support import TAG_ALBUM, wait_for
from tests.sync.test_fill import admin_client
from tests.sync.test_library_and_box import album_id
from tinyjukebox.tools.import_covers import import_covers, plan

JPEG = b"\xff\xd8\xff" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def titles(control):
    return {album["id"]: album["title"] for album in control.list_albums()}


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
    assert os.path.isfile(bed.folder / "library" / "albums" / "short" / "cover.jpg")
    # the box has the titles with the catalog and fetches the cover as a file of the album
    assert wait_for(lambda: titles(box.control) == {short: "Kurz und gut", other: "Nur ein Titel"}, timeout=10)
    assert wait_for(lambda: "cover.jpg" in box.files("short"), timeout=10)
    assert box.control.get_cover(short) == (JPEG, "image/jpeg")
    # again: the titles are there already
    assert import_covers(client, str(tmp_path), say=said.append) == {"titles": 0, "covers": 1, "failed": 0}


@pytest.mark.parametrize("title_first", [True, False])
def test_title_and_cover_changed_on_a_box_reach_the_library_and_the_other_box(bed, title_first):
    """Two changes of one album in a row: the pass that tells the library the first one runs while the second one is
    made. The box must not take the library's older list of files for a change made there - it would delete the
    cover it just got - and the list of files must travel with the change of the entry, whichever came first."""
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    short = album_id(bed, "short")
    if title_first:
        box1.control.update_album(short, "Vom Kind umbenannt")
        box1.control.set_cover(short, PNG)
    else:
        box1.control.set_cover(short, PNG)
        box1.control.update_album(short, "Vom Kind umbenannt")
    assert box1.synced()
    assert wait_for(lambda: bed.catalog("target")[short].get("title") == "Vom Kind umbenannt", timeout=10)
    assert wait_for(lambda: os.path.isfile(bed.folder / "library" / "albums" / "short" / "cover.png"), timeout=10)
    assert "cover.png" in box1.files("short")
    assert box1.control.get_cover(short) == (PNG, "image/png")
    assert wait_for(lambda: titles(box2.control).get(short) == "Vom Kind umbenannt", timeout=10)
    assert not bed.library.store.journal.records("conflict")


def test_files_of_an_album_changed_on_a_box_reach_the_library_though_its_entry_is_what_it_was(bed):
    """A new cover or a playlist written anew changes the files of an album and nothing in its entry. The library
    must take the list of files all the same - otherwise the box would take the older files of the library for a
    change made there and undo its own."""
    box = bed.box("box1")
    assert box.synced()
    short = album_id(bed, "short")
    in_library = bed.folder / "library" / "albums" / "short"
    # only the cover
    box.control.set_cover(short, PNG)
    assert wait_for(lambda: os.path.isfile(in_library / "cover.png"), timeout=10)
    assert box.synced()
    assert "cover.png" in box.files("short")
    # the title, and when that is settled another cover: the entry of the second change is the one of the library
    box.control.update_album(short, "Kurz und gut")
    assert box.synced()
    assert bed.catalog("target")[short]["title"] == "Kurz und gut"
    box.control.set_cover(short, JPEG)
    assert wait_for(lambda: os.path.isfile(in_library / "cover.jpg"), timeout=10)
    assert wait_for(lambda: not os.path.exists(in_library / "cover.png"), timeout=10)
    assert box.synced()
    assert sorted(name for name in box.files("short") if name.startswith("cover")) == ["cover.jpg"]
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
