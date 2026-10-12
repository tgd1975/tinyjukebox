"""A box leaves its library (docs/developer/library.md, section 4.20): it keeps what is on its card, forgets the
library, and works like a box that never had one. The library is not asked."""

import json

import pytest

from tests.support import TAG_ALBUM, TAG_UNMAPPED, wait_for
from tests.sync.bed import BOOTSTRAP
from tinyjukebox.errors import ApiError


def test_an_unlinked_box_keeps_what_it_has_and_works_for_itself(bed):
    box = bed.box("box1")
    assert box.synced()
    box_id = box.sync.data.box_id
    tags, albums = box.tags(), box.control.list_albums()
    box.disconnect()  # the library is switched off, or does not exist any more
    station = box.control.add_radio("FM4", "https://example.org/fm4.m3u")
    box.control.link_tag_radio(TAG_UNMAPPED, station["id"])
    info = box.control.library_info()
    assert (info["attached"], info["connected"], info["absent"]) == (True, False, 0)
    assert info["outbox"] == 2  # the library has not taken the station and the link

    info = box.control.unlink_library()
    assert (info["library"], info["attached"], info["connected"], info["outbox"]) == ("", False, False, 0)
    assert info["box"] == box_id  # it stays the same box
    assert sorted(path.name for path in box.identity.iterdir()) == ["box-id"]
    # nothing on the card changed: the tags, the albums, and what the library never saw
    assert box.tags()[TAG_ALBUM] == tags[TAG_ALBUM]
    assert box.control.list_albums() == albums
    assert box.tags()[TAG_UNMAPPED]["radio_id"] == station["id"]
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    box.place(None)
    # a box for itself: a new station gets a number, and nothing is recorded for a library
    assert box.control.add_radio("Ö1", "https://example.org/oe1.m3u")["id"].isdigit()
    assert box.sync.data.outbox() == []

    box.stop()
    box.start()  # its json files are the truth again
    assert not box.sync.attached
    assert box.tags()[TAG_ALBUM]["album_id"] == tags[TAG_ALBUM]["album_id"]
    assert box.tags()[TAG_UNMAPPED]["radio_id"] == station["id"]
    assert sorted(radio["name"] for radio in box.control.list_radios()) == ["FM4", "Ö1"]
    assert box.control.list_albums() == albums
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")


def test_an_album_that_is_not_on_the_card_is_lost_with_the_library(bed):
    assert bed.box("box1").synced()
    box = bed.box("box2", empty=True)
    assert box.synced()
    assert box.control.library_info()["absent"] == 2  # the catalog arrived, the files of both albums did not
    box.control.unlink_library()
    assert box.control.library_info()["absent"] == 0  # nothing to say about a library any more
    assert TAG_ALBUM in box.tags()  # the figure stays, its album cannot be fetched
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "broken_album", timeout=10)


def test_an_unlinked_box_is_linked_again_like_the_first_time(bed):
    box = bed.box("box1")
    assert box.synced()
    box_id = box.sync.data.box_id
    box.control.unlink_library()
    box.control.add_radio("FM4", "https://example.org/fm4.m3u")  # while it stood alone
    box.control.set_library(bed.url, BOOTSTRAP)
    assert not box.control.library_info()["attached"]  # from the next start, as the first time
    box.stop()
    box.start()
    assert box.synced()
    assert box.sync.data.box_id == box_id
    assert box.sync.data.provisional_ids() == []
    # the library recognizes what it has already, and takes what is new
    targets = bed.catalog("target").values()
    assert sorted(target["album-folder"] for target in targets if "album-folder" in target) == [
        "short", "simple_album_b",
    ]  # fmt: skip
    assert [target["name"] for target in targets if target.get("target-type") == "radio"] == ["FM4"]
    assert len(bed.library.store.boxes()) == 1
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")


def test_unlinking_ends_the_stop_of_a_library_that_is_too_old(bed):
    box = bed.box("box1")
    assert box.synced()
    # the state stays without a connection; with one, the next pass would find that this library fits
    box.disconnect()
    box.sync.set_fit("library-too-old: this box speaks version 9, the library serves [1]")
    box.place(TAG_ALBUM)
    assert box.control.library_info()["blocked"]
    assert box.state != "play_album"  # the box plays nothing
    box.control.unlink_library()
    assert not box.control.library_info()["blocked"]
    assert wait_for(lambda: box.state == "play_album")  # the figure on the box plays at once


def test_the_page_of_a_box_unlinks_it(bed, tmp_path, logger, certificate):
    from tests.support import ApiClient
    from tests.sync.bed import ADMIN
    from tests.sync.conftest import users
    from tinyjukebox.web.auth import UsersFile
    from tinyjukebox.web.server import RestApi

    box = bed.box("box1")
    assert box.synced()
    UsersFile.write(str(tmp_path / "users.yaml"), users())
    config = {"host": "127.0.0.1", "port": 0, "certificate": certificate[0], "private-key": certificate[1],
              "users-file": str(tmp_path / "users.yaml")}  # fmt: skip
    api = RestApi(config, box.control, box.service._targets, logger)
    api.start()
    try:
        page = ApiClient(api.port, certificate[0]).login(*ADMIN)
        assert page.get("/api/box/system").json["library"] == bed.url  # the section "Version" names its source
        answer = page.delete("/api/box/library")
        assert answer.status == 200
        assert (answer.json["library"], answer.json["attached"]) == ("", False)
        assert page.get("/api/box/system").json["library"] == ""
        answer = page.delete("/api/box/library")  # a box that has no library
        assert (answer.status, answer.json["code"]) == (409, "box.not_linked")
    finally:
        api.stop()
    with pytest.raises(ApiError):
        box.control.unlink_library()
    # the journal says what happened, like the log
    journal = [json.loads(line)["event"] for line in (box.folder / "sync" / "journal").read_text().splitlines()]
    assert "unlinked" in journal
