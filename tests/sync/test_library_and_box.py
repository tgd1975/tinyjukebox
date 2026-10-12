"""A library and boxes that synchronize: the scenarios of docs/developer/library.md, section 9.5, with the
real application - tags, albums, stations and positions in place of the notes of the test bed."""

import os
import shutil
from urllib.parse import quote

import pytest

from tests.support import TAG_ALBUM, TAG_UNKNOWN_ALBUM, TAG_UNMAPPED, ApiClient, wait_for


def test_a_box_brings_what_it_has_to_an_empty_library(bed):
    """The first start with a library: everything on the box is settled like something created without a
    connection - nothing is lost, and no provisional id is left."""
    box = bed.box("box1")
    assert box.synced()
    assert box.sync.data.provisional_ids() == []
    targets = bed.catalog("target")
    assert sorted(target["album-folder"] for target in targets.values()) == ["short", "simple_album_b"]
    # the albums themselves arrived: the library has every file
    for target in targets.values():
        assert bed.library.store.manifest(target["target-id"])["lacks"] == []
        assert os.path.isfile(os.path.join(bed.folder, "library", "albums", target["album-folder"], "playlist.m3u"))
    tags = bed.catalog("tag")
    short = next(key for key, target in targets.items() if target["album-folder"] == "short")
    assert tags[TAG_ALBUM]["target"] == {"type": "album", "id": short}
    assert tags[TAG_UNMAPPED]["target"]["id"] == -1
    assert tags[TAG_UNKNOWN_ALBUM]["target"]["id"] == -1  # a link to an album that does not exist is no link
    # the box works with the final ids
    assert box.tags()[TAG_ALBUM]["album_id"] == short
    assert {album["id"] for album in box.control.list_albums()} == set(targets)
    # the box proved the bootstrap password, has a key of its own and does not need the password any more
    assert not (box.identity / "bootstrap").exists()
    assert (box.identity / "key").exists()
    assert (box.identity / "library.crt").exists()


def test_the_box_still_plays_after_it_met_the_library(bed):
    box = bed.box("box1")
    assert box.synced()
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    assert wait_for(lambda: box.control.status()["album"] is not None)


def album_id(bed, folder):
    return next(key for key, target in bed.catalog("target").items() if target.get("album-folder") == folder)


# --- synchronization (S) ---------------------------------------------------------------------------------------------


def test_s1_a_tag_linked_in_the_library_plays_at_once_on_the_box(bed):
    box = bed.box("box1")
    assert box.synced()
    box.place(TAG_UNMAPPED)
    assert wait_for(lambda: box.state == "unknown_tag")
    with bed.as_admin() as library:
        library.link_tag_album(TAG_UNMAPPED, album_id(bed, "simple_album_b"))
    # the event of the library makes the box compare; the tag on the box is evaluated again
    assert wait_for(lambda: box.state == "play_album")
    assert box.tags()[TAG_UNMAPPED]["album_id"] == album_id(bed, "simple_album_b")


def test_s2_s5_a_station_created_without_connection_survives_and_gets_its_final_id(bed):
    box = bed.box("box1")
    assert box.synced()
    box.disconnect()
    station = box.control.add_radio("FM4", "https://example.org/fm4.m3u")
    assert station["id"].startswith(box.sync.data.box_id + "-")  # provisional: not settled with the library
    box.control.link_tag_radio(TAG_UNMAPPED, station["id"])
    assert box.tags()[TAG_UNMAPPED]["radio_id"] == station["id"]  # effective at once
    box.stop()
    box.start()  # ... and after a restart, still without the library
    assert box.tags()[TAG_UNMAPPED]["radio_id"] == station["id"]
    assert [radio["name"] for radio in box.control.list_radios()] == ["FM4"]
    box.connect(bed.url, bootstrap=None)
    assert box.synced()
    final = next(key for key, target in bed.catalog("target").items() if target.get("name") == "FM4")
    assert final.isdigit()
    assert bed.catalog("tag")[TAG_UNMAPPED]["target"] == {"type": "radio", "id": final}
    assert box.tags()[TAG_UNMAPPED]["radio_id"] == final
    assert box.sync.data.provisional_ids() == []
    assert box.sync.data.renamed() == {}  # nothing is left to remember


def test_s3_both_sides_change_the_same_tag_the_library_wins(bed, caplog):
    box = bed.box("box1")
    assert box.synced()
    box.disconnect()
    box.control.set_tag_name(TAG_ALBUM, "name of the box")
    with bed.as_admin() as library:
        library.set_tag_name(TAG_ALBUM, "name of the library")
    box.connect(bed.url, bootstrap=None)
    assert box.synced()
    assert box.tags()[TAG_ALBUM]["name"] == "name of the library"
    assert bed.catalog("tag")[TAG_ALBUM]["tag"]["name"] == "name of the library"
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("conflict: box" in message and "name of the box" in message for message in warnings)  # library
    assert any("conflict: this box" in message and "name of the box" in message for message in warnings)
    assert bed.library.store.journal.records("conflict")


def test_s3_two_changes_of_one_box_in_a_row_are_no_conflict(bed):
    box = bed.box("box1")
    assert box.synced()
    box.disconnect()
    box.control.set_tag_name(TAG_ALBUM, "first")
    box.control.link_tag_album(TAG_ALBUM, album_id(bed, "simple_album_b"))
    box.control.set_tag_name(TAG_ALBUM, "second")
    box.connect(bed.url, bootstrap=None)
    assert box.synced()
    tag = bed.catalog("tag")[TAG_ALBUM]
    assert tag["tag"]["name"] == "second"
    assert tag["target"]["id"] == album_id(bed, "simple_album_b")
    assert not bed.library.store.journal.records("conflict")


def test_s4_the_box_changes_a_tag_that_the_library_deleted(bed):
    box = bed.box("box1")
    assert box.synced()
    box.disconnect()
    box.control.set_tag_name(TAG_UNMAPPED, "still here")
    with bed.as_admin() as library:
        library.remove_tag(TAG_UNMAPPED)
    box.connect(bed.url, bootstrap=None)
    assert box.synced()
    assert TAG_UNMAPPED not in box.tags()
    assert TAG_UNMAPPED not in bed.catalog("tag")
    assert bed.library.store.journal.records("conflict")


def test_s11_a_position_belongs_to_its_box(bed):
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    box1.place(TAG_ALBUM)
    assert wait_for(lambda: box1.state == "play_album")
    box1.place(None)
    assert box1.synced()
    state = bed.library.store.box_state(box1.sync.data.box_id)
    assert state["positions"][TAG_ALBUM]["value"]["target-id"] == album_id(bed, "short")
    assert box2.synced()
    assert bed.library.store.box_state(box2.sync.data.box_id)["positions"] == {}
    assert box2.control.list_activities() == []


def test_an_unknown_tag_is_reported_to_the_library_and_a_known_one_is_looked_up(bed):
    box1 = bed.box("box1")
    assert box1.synced()
    box1.place("AA:BB:CC:DD")
    assert wait_for(lambda: box1.state == "unknown_tag")
    assert box1.synced()
    assert bed.catalog("tag")["AA:BB:CC:DD"]["target"]["id"] == -1  # appears in the library for linking
    with bed.as_admin() as library:
        library.link_tag_album("AA:BB:CC:DD", album_id(bed, "short"))
    assert wait_for(lambda: box1.state == "play_album")


# --- files (F) -------------------------------------------------------------------------------------------------------


def test_f1_an_album_that_is_not_on_the_box_is_fetched_and_played(bed):
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    # the whole catalog arrived, the audio files did not
    assert set(box2.tags()) == set(box1.tags())
    assert box2.files("short") == []
    box2.place(TAG_ALBUM)
    assert wait_for(lambda: box2.state == "play_album", timeout=10)
    everything = ["1.mp3", "2.mp3", "3.mp3", "playlist.m3u"]
    assert wait_for(lambda: box2.files("short") == everything, timeout=10)
    assert box2.files("simple_album_b") == []  # only what was needed


def test_f1_without_the_library_a_missing_album_is_a_broken_album(bed):
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    box2.disconnect()
    box2.place(TAG_ALBUM)
    assert wait_for(lambda: box2.state == "broken_album", timeout=10)


def test_f6_an_album_with_identical_files_is_not_uploaded_twice(bed):
    box1 = bed.box("box1")
    assert box1.synced()
    before = dict(bed.catalog("target"))
    box2 = bed.box("box2")  # brings the same albums along
    assert box2.synced()
    assert bed.catalog("target") == before
    assert bed.library.store.uploads() == []
    assert box2.tags()[TAG_ALBUM]["album_id"] == album_id(bed, "short")
    assert box2.sync.data.provisional_ids() == []


def test_an_album_removed_in_the_library_is_removed_from_the_box(bed):
    box = bed.box("box1")
    assert box.synced()
    with bed.as_admin() as library:
        library.delete_album(album_id(bed, "simple_album_b"), delete_files=True)
    assert wait_for(lambda: box.files("simple_album_b") == [])
    assert [album["folder"] for album in box.control.list_albums()] == ["short"]


# --- access (A) ------------------------------------------------------------------------------------------------------


def test_a4_a_wrong_bootstrap_password_and_no_credentials_are_rejected(bed):
    box = bed.box("box1", bootstrap="another password, also long")
    box.sync.link.wait_pass(5)
    assert not box.sync.link.connected
    assert "handshake was rejected" in box.sync.info()["alone"]
    assert bed.library.store.boxes() == []
    admin = bed.admin()
    assert admin.get("/api/sync/interface", token="").status == 401
    assert admin.get("/api/sync/v1/catalog", token="").status == 401
    assert admin.get("/api/sync/does-not-exist", token="").status == 401  # nobody learns which paths exist
    assert admin.get("/api/sync/v1/catalog").status == 401  # a login of the page is no key of a box


def test_a3_a_removed_box_is_rejected_the_other_one_keeps_working(bed):
    box1 = bed.box("box1")
    box2 = bed.box("box2", empty=True)
    assert box1.synced()
    assert box2.synced()
    admin = bed.admin()
    assert admin.delete(f"/api/library/boxes/{box1.sync.data.box_id}").status == 204
    box1.sync.link.wait_pass(5)
    assert wait_for(lambda: not box1.sync.link.connected)
    # (a pass that was under way only hears 401; the next one says what it means)
    assert wait_for(lambda: "rejects the key" in box1.sync.info()["alone"])
    assert box2.synced()


def test_a2_another_certificate_is_refused(bed, tmp_path):
    from tinyjukebox.web.certificates import create_self_signed_certificate

    box = bed.box("box1")
    assert box.synced()
    files = (str(tmp_path / "library" / "library.crt"), str(tmp_path / "library" / "library.key"))
    for name in files:
        os.remove(name)
    create_self_signed_certificate(*files)
    bed.restart_library()
    box.sync.link.wait_pass(5)
    assert wait_for(lambda: "another certificate" in box.sync.info()["alone"])
    assert not box.sync.link.connected


def test_the_page_of_the_library_shows_the_boxes(bed):
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    assert admin.get("/api/session").json["mode"] == "library"
    overview = admin.get("/api/library/overview").json
    assert [entry["id"] for entry in overview["boxes"]] == [box.sync.data.box_id]
    assert overview["boxes"][0]["status"]["version"] == box.sync.version
    assert admin.patch(f"/api/library/boxes/{box.sync.data.box_id}", {"name": "Kinderzimmer"}).status == 204
    assert wait_for(lambda: box.sync.info()["name"] == "Kinderzimmer")
    assert len(admin.get("/api/tags").json) == 3
    assert admin.get("/api/status").json["state"] == "library"


def test_space_on_the_card_the_album_not_played_for_the_longest_time_makes_room(bed, monkeypatch):
    box1 = bed.box("box1")
    assert box1.synced()
    box2 = bed.box("box2", empty=True)
    assert box2.synced()
    for uid, folder in ((TAG_ALBUM, "short"), (TAG_UNMAPPED, "simple_album_b")):
        with bed.as_admin() as library:
            library.link_tag_album(uid, album_id(bed, folder))
        assert box2.synced()
        box2.place(uid)
        assert wait_for(lambda: len(box2.files(folder)) == 4, timeout=10)  # noqa: B023 - tracks and playlist
    assert wait_for(lambda: box2.state == "play_album" and not box2.sync.files.fetching(), timeout=10)
    # the card is full: the album that plays stays, the other one makes room and stays in the catalog
    box2.sync.files._keep_free = 10**6
    usage = shutil.disk_usage(box2.folder)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=0))
    box2.sync.files.make_room()
    assert box2.files("short") == []
    assert box2.files("simple_album_b")
    assert len(box2.control.list_albums()) == 2
    # ... and is fetched again when its figure is placed
    monkeypatch.undo()
    box2.place(TAG_ALBUM)
    assert wait_for(lambda: box2.state == "play_album", timeout=10)
    assert wait_for(lambda: len(box2.files("short")) == 4, timeout=10)
    # an album that exists only on this box is never deleted for space
    box1.disconnect()
    shutil.copytree(box1.folder / "albums" / "short", box1.folder / "albums" / "only here")
    box1.control.add_album("only here", "Only here")
    box1.sync.files._keep_free = 10**6
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=0))
    box1.sync.files.make_room()
    assert len(box1.files("only here")) == 4
    assert box1.files("short") == []  # the library has this one


def test_f3_f4_an_incomplete_album_is_invisible_and_a_tag_linked_to_it_waits(bed):
    """The store of the library, asked directly: the album a box is uploading has no id and is in no catalog until
    its last file arrived; the tag that was linked to it on the box is applied only then."""
    box = bed.box("box1")
    assert box.synced()
    store, box_id = bed.library.store, box.sync.data.box_id
    from tinyjukebox.sync import manifest as manifests

    # (another album than the ones the library has)
    source = box.folder / "new album"
    source.mkdir()
    track = (box.folder / "albums" / "short" / "1.mp3").read_bytes()
    (source / "1.mp3").write_bytes(track + b"one")
    (source / "extra.mp3").write_bytes(track + b"more")
    (source / "playlist.m3u").write_text("1.mp3\nextra.mp3\n")
    manifest = manifests.create(str(source), "playlist.m3u")
    new = f"{box_id}-77"
    value = {"target-id": new, "target-type": "album", "album-folder": "new album", "playlist": "playlist.m3u",
             "created": "2026-10-06T10:00:00.000000Z"}  # fmt: skip
    common = {"by": "console", "version": "0.9.0", "clock": {"time": "", "synchronized": True}}
    tag = {"tag": {"uid": TAG_UNMAPPED, "name": "unmapped"}, "target": {"type": "album", "id": new},
           "created": "2020-12-01T12:34:56.789000Z"}  # fmt: skip
    results = store.apply(box_id, [
        {"n": 900, "op": "create", "kind": "target", "key": new, "value": value, "manifest": manifest, **common},
        {"n": 901, "op": "set", "kind": "tag", "key": TAG_UNMAPPED, "value": tag, "base": 1, **common},
    ])  # fmt: skip
    assert [result["result"] for result in results] == ["waiting", "waiting"]
    assert sorted(results[0]["missing"]) == ["1.mp3", "extra.mp3", "playlist.m3u"]
    assert all(target.get("album-folder") != "new album" for target in bed.catalog("target").values())
    assert bed.catalog("tag")[TAG_UNMAPPED]["target"]["id"] == -1
    assert store.uploads()[0]["transferred"] == 0
    assert store.resolve(box_id, "target", new) == {"missing": results[0]["missing"]}

    def send(name):
        def save(filename):
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            shutil.copy(source / name, filename)
            return next(entry["sha256"] for entry in manifest["files"] if entry["name"] == name)

        return store.receive_file(box_id, new, name, save)

    assert send("1.mp3")["missing"] == ["playlist.m3u", "extra.mp3"]
    assert store.uploads()[0]["transferred"] == 1
    send("playlist.m3u")
    assert send("extra.mp3")["missing"] == []
    final = store.resolve(box_id, "target", new)["id"]
    assert bed.catalog("target")[final]["album-folder"] == "new album"
    assert bed.catalog("tag")[TAG_UNMAPPED]["target"] == {"type": "album", "id": final}  # applied with the album
    assert store.uploads() == []
    # sent again - e.g. the confirmation got lost: the same answers, nothing twice
    again = store.apply(box_id, [
        {"n": 900, "op": "create", "kind": "target", "key": new, "value": value, "manifest": manifest, **common},
    ])  # fmt: skip
    assert again[0]["id"] == final
    assert len(bed.catalog("target")) == 3


def test_s8_a_library_restored_from_a_backup_the_positions_of_the_box_win(bed, tmp_path):
    box = bed.box("box1")
    assert box.synced()
    backup = tmp_path / "backup"
    shutil.copytree(tmp_path / "library" / "data", backup)
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    box.place(None)
    assert box.synced()
    box_id = box.sync.data.box_id
    position = bed.library.store.box_state(box_id)["positions"][TAG_ALBUM]
    with bed.as_admin() as library:
        library.set_tag_name(TAG_UNMAPPED, "after the backup")
    assert wait_for(lambda: box.tags()[TAG_UNMAPPED]["name"] == "after the backup")
    # the library goes back to the older state
    port = bed.library.api.port
    bed.library.stop()
    shutil.rmtree(tmp_path / "library" / "data")
    shutil.copytree(backup, tmp_path / "library" / "data")
    bed.library = bed._library(port)
    bed.library.start()
    assert box.synced()
    # the catalog heals by itself: the box fetches the older file
    assert wait_for(lambda: box.tags()[TAG_UNMAPPED]["name"] == "unmapped")
    # the position exists only on the box any more: it is sent again
    assert wait_for(lambda: TAG_ALBUM in bed.library.store.box_state(box_id)["positions"])
    assert bed.library.store.box_state(box_id)["positions"][TAG_ALBUM]["value"] == position["value"]


def test_s10_a_box_set_up_again_takes_over_an_old_one(bed):
    old = bed.box("box1")
    assert old.synced()
    old.place(TAG_ALBUM)
    assert wait_for(lambda: old.state == "play_album")
    old.place(None)
    assert old.synced()
    new = bed.box("box2", empty=True)
    assert new.synced()
    assert new.sync.data.box_id != old.sync.data.box_id
    assert new.control.list_activities() == []
    admin = bed.admin()
    answer = admin.patch(f"/api/library/boxes/{new.sync.data.box_id}", {"takeover": old.sync.data.box_id})
    assert answer.status == 204
    assert wait_for(lambda: [a["tag_uid"] for a in new.control.list_activities()] == [TAG_ALBUM])
    old.sync.link.wait_pass(5)
    assert wait_for(lambda: not old.sync.link.connected)  # the old box is removed with it


def test_c8_a_script_with_its_token_may_ask_for_a_check_and_nothing_else(bed):
    admin = bed.admin()
    token = admin.post("/api/library/tokens").json["token"]
    script = bed.admin()
    script.token = token
    assert script.post("/api/library/releases/check").status == 200
    assert script.get("/api/library/overview").status == 401
    assert script.get("/api/tags").status == 401
    script.token = "not a token"
    assert script.post("/api/library/releases/check").status == 401
    assert admin.post("/api/library/releases/check").status == 200  # the button of the page


# --- the phone as reader (docs/developer/library.md, section 5.1) ---------------------------------------------------

PHONE_TAG = "04:A1:B2:C3:D4:E5:80"  # a tag that no box of the tests knows


def test_a_tag_read_by_a_phone_is_added_and_reaches_the_boxes(bed):
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    path = "/api/tags/" + quote(PHONE_TAG, safe="")
    answer = admin.put(path)
    assert answer.status == 201
    assert (answer.json["uid"], answer.json["album_id"], answer.json["radio_id"]) == (PHONE_TAG, None, None)
    assert bed.catalog("tag")[PHONE_TAG]["target"]["id"] == -1  # appears in the library for linking
    assert wait_for(lambda: PHONE_TAG in box.tags())  # as every change of the catalog
    # a tag that is known: nothing changes, whatever it is linked to
    assert admin.put(path).status == 200
    short = album_id(bed, "short")
    assert admin.put(path + "/album", {"album_id": short}).status == 200
    again = admin.put(path)
    assert (again.status, again.json["album_id"]) == (200, short)
    assert admin.get("/api/status").json["tag"] is None  # nothing is kept about the scan


def test_every_user_may_scan_and_linking_stays_with_admins(bed, certificate):
    assert bed.admin().post("/api/users", {"username": "anna", "password": "anna-password"}).status == 201
    user = ApiClient(bed.library.api.port, certificate[0]).login("anna", "anna-password")
    path = "/api/tags/" + quote(PHONE_TAG, safe="")
    assert user.put(path).status == 201
    assert user.put(path + "/album", {"album_id": "1"}).status == 403
    assert user.put(path, token="").status == 401


@pytest.mark.parametrize(
    "uid",
    [
        "04:86:01",  # 3 bytes
        "04:1A:2B:3C:4D",  # 5 bytes
        "04:1A:2B:3C:4D:5E:6F:00:00:00:00",  # 11 bytes
        "04:1a:2b:3c:4d:5e:6f",  # the reader reports upper case
        "041A2B3C4D5E6F",
        "136:4:99:1",  # a virtual tag of the box on the PC
        "current",
    ],
)
def test_a_scan_takes_only_the_uid_of_a_reader(bed, uid):
    before = set(bed.catalog("tag"))
    assert bed.admin().put("/api/tags/" + quote(uid, safe="")).status == 400
    assert set(bed.catalog("tag")) == before


def test_the_page_of_the_library_has_a_cookie_of_its_own(bed):
    """On the PC a box and the library share the host name; a login to one must not end the login to the other."""
    answer = bed.admin().post("/api/login", {"username": "admin", "password": "admin-password"}, token="")
    cookie = answer.getheader("Set-Cookie")
    assert cookie.startswith("tinyjukebox_session_library=")
    client = bed.admin()
    client.token = ""
    token = cookie.split(";")[0].split("=", 1)[1]
    assert client.get("/api/session", headers={"Cookie": f"tinyjukebox_session_library={token}"}).status == 200
    assert client.get("/api/session", headers={"Cookie": f"tinyjukebox_session={token}"}).status == 401


def test_the_page_of_a_box_shows_and_sets_the_library(bed, tmp_path, logger, certificate):
    """The requests of the tab "System" of a box, over https as the page sends them."""
    from tests.support import ApiClient
    from tests.sync.bed import ADMIN, BOOTSTRAP
    from tests.sync.conftest import users
    from tinyjukebox.web.auth import UsersFile
    from tinyjukebox.web.server import RestApi

    box = bed.box("box1", connect=False)
    UsersFile.write(str(tmp_path / "users.yaml"), users())
    config = {"host": "127.0.0.1", "port": 0, "certificate": certificate[0], "private-key": certificate[1],
              "users-file": str(tmp_path / "users.yaml")}  # fmt: skip
    api = RestApi(config, box.control, box.service._targets, logger)
    api.start()
    try:
        page = ApiClient(api.port, certificate[0]).login(*ADMIN)
        info = page.get("/api/box").json
        assert (info["library"], info["attached"], info["connected"]) == ("", False, False)
        answer = page.put("/api/box/library", {"url": bed.url + "/", "bootstrap": BOOTSTRAP})
        assert answer.status == 200
        assert (answer.json["library"], answer.json["attached"]) == (bed.url, False)  # from the next start
        assert (box.identity / "bootstrap").read_text().strip() == BOOTSTRAP
        assert page.put("/api/box/library", {"url": "http://not-https"}).status == 400
        assert page.put("/api/box/name", {"name": "Kinderzimmer"}).status == 409  # not before it has a library
        system = page.get("/api/box/system").json
        assert (system["available"], system["version"]) == (False, box.sync.version)
        assert page.post("/api/box/system/revert", {"arguments": []}).status == 409  # a box without slots
    finally:
        api.stop()
    box.stop()
    box.start()
    assert box.synced()
    assert box.control.library_info()["attached"]
    assert box.control.set_box_name("Kinderzimmer")["box"] == box.sync.data.box_id
    assert wait_for(lambda: bed.library.store.box_name(bed.library.store.boxes()[0]) == "Kinderzimmer")
