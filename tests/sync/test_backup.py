"""Backups (docs/developer/operations.md, section 10): what one of the library holds, that the data put back from it
is the library again, the backups that the library and a box without a library make by themselves - two schedules -
and the command that fetches one."""

import datetime
import email.message
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
from typing import Any
from urllib.parse import quote

import pytest

from tests.support import TAG_ALBUM, ApiClient, wait_for
from tests.sync.bed import BOOTSTRAP
from tests.sync.test_fill import admin_client
from tests.sync.test_library_and_box import album_id
from tests.sync.test_slots import version_in_the_archive
from tinyjukebox import backups as backups_module
from tinyjukebox.backup import JOURNAL, Backup, Folder, Incomplete, verify
from tinyjukebox.backups import Backups, next_due
from tinyjukebox.errors import BadRequest, Conflict, NotFound
from tinyjukebox.sync.client import HttpError
from tinyjukebox.tools import backup as tool


def members(content: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        return {member.name: (archive.extractfile(member) or io.BytesIO()).read() for member in archive}


# --- what a backup holds ---------------------------------------------------------------------------------------------


def test_a_backup_holds_the_data_of_the_library_and_nothing_secret(bed, tmp_path):
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    token = admin.post("/api/library/tokens", {"name": "script"}).json["token"]  # kept in the settings, hashed
    version_in_the_archive(bed, "9.9.9", tmp_path)
    image = bed.library.images.add(b"\xff\xd8\xff" + b"0" * 100)
    box_id = bed.library.store.boxes()[0]["id"]

    plain = admin.get("/api/backup")
    assert (plain.status, plain.headers["Content-Type"]) == (200, "application/gzip")
    assert re.fullmatch(r"tinyjukebox-library-\d{8}-\d{6}\.tar\.gz", plain.headers.get_filename())
    about, found = verify(io.BytesIO(plain.content)), members(plain.content)
    assert (about["kind"], about["albums"], about["skipped"]) == ("library", False, [])
    expected = {
        "data/catalog/tags.json", "data/catalog/targets.json", "data/catalog/images.json", f"data/boxes/{box_id}.json",
        "data/ids.json", "data/versions.json", "data/journal.sqlite", f"images/{image}",
        f"data/manifests/{album_id(bed, 'short')}.json",
    }  # fmt: skip
    assert expected <= set(found)
    assert TAG_ALBUM in json.loads(found["data/catalog/tags.json"])["entries"]
    assert json.loads(found[f"data/boxes/{box_id}.json"])["positions"] is not None
    # the box versions are to be fetched again: their files are not part of a backup
    assert json.loads(found["data/versions.json"])["9.9.9"]["files"] == {}
    assert bed.library.store.versions()["9.9.9"]["files"]  # ... and the library itself still has them
    (tmp_path / "journal.sqlite").write_bytes(found["data/journal.sqlite"])
    journal = sqlite3.connect(tmp_path / "journal.sqlite")
    assert journal.execute("SELECT count(*) FROM records").fetchone()[0] > 0
    journal.close()
    # left out: the settings with password and tokens, the archive, the generated files, the albums
    left_out = ("data/settings.json", "data/archive/", "data/json/", "albums/", "ssl/")
    assert not [name for name in found if name.startswith(left_out)]
    for secret in (BOOTSTRAP, token, "password"):
        assert not [name for name, content in found.items() if secret.encode() in content], secret

    full = admin.get("/api/backup?albums=1")
    assert full.headers["Content-Type"] == "application/x-tar"
    found = members(full.content)
    assert {"albums/short/1.mp3", "albums/short/playlist.m3u", "data/catalog/tags.json"} <= set(found)
    assert not [name for name in found if ".tinyjukebox-staging" in name]


def test_the_backup_of_a_box_without_a_library_holds_the_lines_of_its_journal(bed):
    """The statistics of such a box are counted from these lines, and nobody else has them. Only the lines: not the
    identity of the box and not what it exchanges with a library."""
    box = bed.box("box1", connect=False)
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    box.place(None)
    # the line "end" of the tag that was taken off comes last: nothing is written between backup and comparison
    assert wait_for(lambda: "end" in [line["event"] for line in box.sync.data.journal_lines(None)])
    backup = box.control.backup()
    lines = box.sync.data.journal_lines(None)
    out = io.BytesIO()
    backup.write(out)
    found = members(out.getvalue())
    assert [json.loads(line) for line in found[JOURNAL].decode().splitlines()] == lines
    assert {line["event"] for line in lines} >= {"start", "tag"}
    assert {"json/activity.json", "json/journal.jsonl", "json/tags.json", "json/targets.json"} <= set(found)
    assert not [name for name in found if name.startswith(("sync/", "identity/"))]
    assert box.sync.data.box_id.encode() not in out.getvalue()
    # what the box counts from the lines is what the lines of the backup say
    assert box.control.statistics()["origin"] == "box"


def test_the_backup_of_a_box_with_a_library_holds_no_journal(bed):
    """The library has the lines of such a box, and its backup holds them."""
    box = bed.box("box1")
    assert box.synced()
    out = io.BytesIO()
    box.control.backup().write(out)
    assert JOURNAL not in members(out.getvalue())


def test_backups_are_for_admins(bed, certificate):
    assert bed.admin().post("/api/users", {"username": "anna", "password": "anna-password"}).status == 201
    user = ApiClient(bed.library.api.port, certificate[0]).login("anna", "anna-password")
    for method, path in (
        ("GET", "/api/backup"),
        ("GET", "/api/library/backups"),
        ("PUT", "/api/library/backups"),
        ("POST", "/api/library/backups"),
        ("GET", "/api/library/backups/tinyjukebox-library-20261010-030000.tar.gz"),
        ("DELETE", "/api/library/backups/tinyjukebox-library-20261010-030000.tar.gz"),
    ):
        assert user.request(method, path, {} if method != "GET" else None).status == 403, (method, path)
        assert user.request(method, path, {} if method != "GET" else None, token="").status == 401, (method, path)


# --- putting it back -------------------------------------------------------------------------------------------------


def test_the_data_put_back_from_a_backup_is_the_library_again(bed, tmp_path):
    """Restoring by hand: stop, unpack into the folders, start. Certificate and users stayed on the computer, so the
    box goes on without being told anything."""
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    assert admin.patch("/api/tags/" + quote(TAG_ALBUM, safe=""), {"name": "rote Katze"}).status == 200
    assert admin.put("/api/library/bootstrap", {"password": "another-bootstrap-password"}).status == 204
    version_in_the_archive(bed, "9.9.9", tmp_path)
    image = b"\xff\xd8\xff" + b"0" * 100
    cover = bed.library.images.add(image)
    assert wait_for(lambda: box.tags()[TAG_ALBUM]["name"] == "rote Katze")  # the box has the changes
    before = {kind: bed.catalog(kind) for kind in ("tag", "target")}
    content = admin.get("/api/backup?albums=1").content

    root, port = bed.folder / "library", bed.library.api.port
    bed.library.stop()
    for name in ("data", "images", "albums"):
        shutil.rmtree(root / name)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        archive.extractall(root, filter="data")
    assert sorted(name for name in os.listdir(root) if "." not in name) == ["albums", "data", "images"]
    bed.library = bed._library(port)
    bed.library.start()

    assert {kind: bed.catalog(kind) for kind in before} == before
    assert bed.catalog("tag")[TAG_ALBUM]["tag"]["name"] == "rote Katze"
    assert os.path.exists(root / "albums" / "short" / "1.mp3")
    assert (root / "images" / cover).read_bytes() == image
    # the settings were not part of the backup: the bootstrap password is the one of the config again
    assert bed.library.settings["bootstrap"] == BOOTSTRAP
    assert bed.library.store.versions()["9.9.9"]["files"] == {}
    # the box is known with its key, and what it changes arrives
    box.control.set_tag_name(TAG_ALBUM, "blaue Maus")
    assert box.synced()
    assert bed.catalog("tag")[TAG_ALBUM]["tag"]["name"] == "blaue Maus"
    assert [entry["id"] for entry in bed.library.store.boxes()] == [box.sync.info()["box"]]  # no new box


def started_again(bed):
    """What the program does after a backup was confirmed: it stops, swaps the folders and starts again."""
    port, library = bed.library.api.port, bed.library
    library.stop()
    library.restore.apply()
    bed.library = bed._library(port)
    bed.library.start()


def test_the_library_puts_a_backup_back_that_is_sent_to_it(bed):
    """From the page: the backup is sent, unpacked beside the data and put back when it is confirmed. What a backup
    does not hold stays: the settings with the bootstrap password, the schedules and folders of the backups."""
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    tag = "/api/tags/" + quote(TAG_ALBUM, safe="")
    assert admin.patch(tag, {"name": "rote Katze"}).status == 200
    content = admin.get("/api/backup").content
    about = verify(io.BytesIO(content))
    # after the backup
    assert admin.patch(tag, {"name": "blaue Maus"}).status == 200
    assert admin.put("/api/library/bootstrap", {"password": "another-bootstrap-password"}).status == 204
    assert admin.put("/api/library/backups", {"plain": {"keep": 3}}).status == 200
    assert wait_for(lambda: box.tags()[TAG_ALBUM]["name"] == "blaue Maus")
    assert admin.get("/api/restore").json == {
        "state": "idle", "about": None, "error": None, "last": None, "kind": "library", "library": False,
    }  # fmt: skip

    # the backup of a box is refused, and so is one that was cut off
    out = io.BytesIO()
    box.control.backup().write(out)
    refused = admin.request("PUT", "/api/restore", raw=out.getvalue())
    assert (refused.status, refused.json["code"]) == (400, "restore.kind")
    refused = admin.request("PUT", "/api/restore", raw=content[: len(content) // 2])
    assert (refused.status, refused.json["code"]) == (400, "restore.incomplete")
    assert not [name for name in os.listdir(bed.folder / "library") if name.endswith(".new")]

    staged = admin.request("PUT", "/api/restore", raw=content)
    assert (staged.status, staged.json["state"], staged.json["about"]["created"]) == (200, "staged", about["created"])
    assert (bed.folder / "library" / "data.new" / "catalog" / "tags.json").exists()
    assert bed.catalog("tag")[TAG_ALBUM]["tag"]["name"] == "blaue Maus"  # nothing touched yet
    assert admin.post("/api/restore/confirm").status == 202
    assert admin.get("/api/restore").json["state"] == "confirmed"
    # the boxes are asked to wait until the library runs again
    box.sync.link.wait_pass(5)
    assert "503" in box.sync.info()["alone"]

    started_again(bed)
    admin = bed.admin()
    assert bed.catalog("tag")[TAG_ALBUM]["tag"]["name"] == "rote Katze"
    assert bed.library.settings["bootstrap"] == "another-bootstrap-password"  # stays
    assert bed.library.backups.status()["plain"]["keep"] == 3  # stays
    last = admin.get("/api/restore").json["last"]
    assert (last["backup"]["created"], last["backup"]["albums"], last["missing"]) == (about["created"], False, [])
    assert wait_for(lambda: not (bed.folder / "library" / "data.before").exists())
    # the boxes take over the restored catalog
    assert wait_for(lambda: box.tags()[TAG_ALBUM]["name"] == "rote Katze", timeout=10)


def test_the_library_puts_back_a_backup_of_its_folder_and_names_the_albums_that_are_missing(bed, caplog):
    assert bed.box("box1").synced()  # the library has the albums of the box
    admin = bed.admin()
    assert admin.post("/api/library/backups", {}).status == 202

    def made():
        status = admin.get("/api/library/backups").json
        return status["backups"] if not status["running"] else []

    (entry,) = wait_for(made)
    shutil.rmtree(bed.folder / "library" / "albums" / "short")  # an album that is gone since the backup
    assert admin.post("/api/restore", {"name": "unknown.tar.gz"}).status == 404
    assert admin.post("/api/restore", {"name": entry["name"]}).status == 202
    assert wait_for(lambda: admin.get("/api/restore").json["state"] == "staged")
    assert admin.post("/api/restore/confirm").status == 202
    started_again(bed)
    short = album_id(bed, "short")
    missing = bed.admin().get("/api/restore").json["last"]["missing"]
    assert missing == [{"id": short, "title": "short", "folder": "short"}]
    assert f"restore: album 'short' ({short}) is in the data, but its folder 'short' is missing." in caplog.text


def test_a_box_without_a_library_puts_a_backup_back(bed, tmp_path):
    """The json files, the covers, the albums and the lines of the journal. The schedules of the backups and the
    identity of the box stay."""
    box = bed.box("alone", connect=False)
    box.control.set_tag_name(TAG_ALBUM, "rote Katze")
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    box.place(None)
    lines = box.sync.data.journal_lines(None)
    assert {line["event"] for line in lines} >= {"start", "tag"}
    backup = box.control.backup(albums=True)
    filename, _ = backup.save(str(tmp_path))
    box_id = box.sync.data.box_id
    # after the backup
    box.control.set_tag_name(TAG_ALBUM, "blaue Maus")
    box.control.backups.set({"plain": {"keep": 3}})
    (box.folder / "albums" / "short" / "new.mp3").write_bytes(b"later")
    restore = box.control.restore
    assert restore.stage(filename)["albums"] is True
    restore.confirm()
    box.stop()
    restore.apply()
    box.start()
    assert box.tags()[TAG_ALBUM]["name"] == "rote Katze"
    assert not (box.folder / "albums" / "short" / "new.mp3").exists()
    assert box.control.backups.status()["plain"]["keep"] == 3  # stays
    assert box.sync.data.box_id == box_id  # stays
    now = box.sync.data.journal_lines(None)
    assert now[: len(lines)] == lines  # the lines of the backup, and the start that followed
    assert now[-1]["event"] == "start"
    assert now[-1]["start"] > max(line["start"] for line in lines)
    assert box.control.statistics()["tags"][0]["plays"] == 1
    assert box.control.restore.last()["missing"] == []
    assert wait_for(lambda: not (box.folder / "json.before").exists() and not (box.folder / "albums.before").exists())


def test_the_slot_of_the_package_returns_for_a_version_whose_files_are_gone(bed, tmp_path):
    version_in_the_archive(bed, "9.9.9", tmp_path)
    bed.library.store.set_version("9.9.9", "manual", "web:admin", "tried")
    bed.library.store.drop_version_files("9.9.9")  # as after the data was put back from a backup
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "slot.json").write_text(json.dumps(bed.library.store.versions()["9.9.9"]["description"]))
    (slots / "slot.tar.gz").write_text("archive")
    (slots / "slot.tar.gz.sig").write_text("signature")
    bed.library._config = {**bed.library._config, "slots-folder": str(slots)}
    bed.library._own_slot()
    entry = bed.library.store.versions()["9.9.9"]
    assert entry["files"] == {"description": "slot.json", "archive": "slot.tar.gz", "signature": "slot.tar.gz.sig"}
    assert entry["state"] == "manual"  # what was decided about the version stays
    with open(bed.library.store.version_file("9.9.9", "archive"), encoding="utf-8") as infile:
        assert infile.read() == "archive"


# --- the backups that are made by a schedule --------------------------------------------------------------------------


def at(text: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(text)


@pytest.mark.parametrize(
    ("now", "every", "unit", "last", "due"),
    [
        ("2026-10-10 02:00", 1, "days", None, "2026-10-10 03:00"),  # never made: today
        ("2026-10-10 03:00", 1, "days", None, "2026-10-11 03:00"),  # the time has passed: tomorrow, not at once
        ("2026-10-10 12:00", 1, "days", "2026-10-10", "2026-10-11 03:00"),
        ("2026-10-10 12:00", 7, "days", "2026-10-10", "2026-10-17 03:00"),
        ("2026-10-16 23:59", 7, "days", "2026-10-10", "2026-10-17 03:00"),
        ("2026-10-20 01:00", 7, "days", "2026-10-10", "2026-10-20 03:00"),  # the program did not run when it was due
        ("2026-10-20 04:00", 7, "days", "2026-10-10", "2026-10-21 03:00"),
        ("2026-10-10 12:00", 1, "weeks", "2026-10-10", "2026-10-17 03:00"),
        ("2026-10-10 12:00", 3, "weeks", "2026-10-10", "2026-10-31 03:00"),
        ("2026-10-10 12:00", 1, "months", "2026-10-10", "2026-11-10 03:00"),  # the same day of the month
        ("2026-10-10 12:00", 3, "months", "2026-11-10", "2027-02-10 03:00"),  # over the end of the year
        ("2026-12-31 12:00", 1, "months", "2026-12-31", "2027-01-31 03:00"),
        ("2027-01-31 12:00", 1, "months", "2027-01-31", "2027-02-28 03:00"),  # a day the month does not have: its last
        ("2028-01-31 12:00", 1, "months", "2028-01-31", "2028-02-29 03:00"),
        ("2026-10-31 12:00", 1, "months", "2026-10-31", "2026-11-30 03:00"),
        ("2027-03-15 01:00", 1, "months", "2027-01-31", "2027-03-15 03:00"),  # long overdue: today
    ],
)
def test_when_the_next_backup_is_due(now, every, unit, last, due):
    settings = {"time": "03:00", "every": every, "unit": unit}
    assert next_due(at(now), settings, datetime.date.fromisoformat(last) if last else None) == at(due)
    if unit == "days":  # a schedule that names no unit counts days
        del settings["unit"]
        assert next_due(at(now), settings, datetime.date.fromisoformat(last) if last else None) == at(due)


@pytest.fixture
def kept(tmp_path, logger):
    """Backups of a folder with one file, in ``tmp_path/backups``; every backup is a second younger than the last."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"image")
    clock = iter(range(1_800_000_000, 1_900_000_000))

    def create(albums: bool) -> Backup:
        return Backup("library", albums, [], [Folder("images", str(source))], created=next(clock))

    backups = Backups(str(tmp_path / "backups"), str(tmp_path / "backups.json"), create, logger)
    yield backups
    backups.stop()


def names(kept) -> list[str]:
    return [entry["name"] for entry in kept.status()["backups"]]


def test_there_are_two_schedules_and_each_keeps_the_backups_of_its_kind(kept, tmp_path):
    """Without the albums and with them: set apart from each other. How many are kept counts the backups of one
    kind, which the file name says - also when both kinds are written into one folder."""
    status = kept.status()
    assert {
        kind: {key: status[kind][key] for key in backups_module.DEFAULTS[kind]} for kind in ("plain", "albums")
    } == (backups_module.DEFAULTS)
    assert (status["plain"]["keep"], status["albums"]["keep"], status["albums"]["unit"]) == (7, 2, "months")
    assert kept.set({"plain": {"keep": 3}, "albums": {"keep": 1}})["albums"]["keep"] == 1
    made = [kept.make(albums) for albums in (False, True, False, True, False, False, True)]
    plain, full = [made[index] for index in (0, 2, 4, 5)], [made[index] for index in (1, 3, 6)]
    assert all(name.endswith(".tar.gz") for name in plain)
    assert all(name.endswith("-albums.tar") for name in full)
    # newest first, the three newest without the albums and the newest one with them
    assert names(kept) == [made[6], made[5], made[4], made[2]]
    status = kept.status()
    assert [entry["albums"] for entry in status["backups"]] == [True, False, False, False]
    assert status["backups"][0]["size"] == os.path.getsize(tmp_path / "backups" / made[6])
    # the time in the name is the local time of the program, whatever the time zone of the computer
    assert status["backups"][0]["when"] == time.strftime("%Y-%m-%d %H:%M", time.localtime(1_800_000_006))
    assert verify(kept.path(made[6]))["files"] == 1
    # other files in the folder are neither listed nor touched
    (tmp_path / "backups" / "notes.txt").write_text("mine")
    (tmp_path / "backups" / "tinyjukebox-box-20200101-000000.tar.gz").write_text("of a box")
    kept.make(False)
    assert len(names(kept)) == 4
    assert {"notes.txt", "tinyjukebox-box-20200101-000000.tar.gz"} <= set(os.listdir(tmp_path / "backups"))
    with pytest.raises(NotFound):
        kept.path("notes.txt")
    with pytest.raises(NotFound):
        kept.path("../backups.json")
    kept.remove(made[6])
    assert made[6] not in os.listdir(tmp_path / "backups")


def test_the_schedules_are_checked_and_kept(kept, tmp_path, logger):
    assert (kept.status()["plain"]["next"], kept.status()["albums"]["next"]) == (None, None)  # no schedule yet
    status = kept.set({
        "plain": {"enabled": True, "time": "04:30", "every": 1, "unit": "weeks", "keep": 52},
        "albums": {"enabled": True, "time": "05:15", "every": 1, "unit": "months", "keep": 2},
    })  # fmt: skip
    plain, full = status["plain"], status["albums"]
    assert (plain["enabled"], plain["time"], plain["every"], plain["unit"], plain["keep"]) == (
        True, "04:30", 1, "weeks", 52,
    )  # fmt: skip
    assert (full["enabled"], full["time"], full["unit"], full["keep"]) == (True, "05:15", "months", 2)
    assert plain["next"].endswith(" 04:30")
    assert full["next"].endswith(" 05:15")
    # one schedule alone, and a field alone: the rest stays
    assert kept.set({"albums": {"enabled": False}})["plain"]["enabled"] is True
    assert kept.status()["albums"]["next"] is None
    assert kept.status()["albums"]["time"] == "05:15"
    for wrong in (
        {"time": "4:30"}, {"time": "24:00"}, {"every": 0}, {"every": 366}, {"keep": 1000}, {"keep": "3"},
        {"enabled": 1}, {"unit": "years"}, {"unit": None},
    ):  # fmt: skip
        for kind in ("plain", "albums"):
            with pytest.raises(BadRequest):
                kept.set({kind: wrong})
    refused: tuple[Any, ...] = ({"time": "04:00"}, {"both": {}}, {"plain": "on"}, ["plain"])
    for flat in refused:
        with pytest.raises(BadRequest):
            kept.set(flat)
    assert kept.status()["plain"]["time"] == "04:30"  # nothing of what was refused was taken over
    again = Backups(kept.folder(), str(tmp_path / "backups.json"), kept._create, logger)
    assert (again.status()["plain"]["time"], again.status()["plain"]["unit"]) == (
        "04:30",
        "weeks",
    )  # survives a restart
    assert again.status()["albums"]["keep"] == 2


def no_backup(albums: bool) -> Backup:
    raise AssertionError("no backup is made in this test")


@pytest.mark.parametrize("albums", [False, True])
def test_the_one_schedule_of_before_becomes_the_schedule_of_its_kind(tmp_path, logger, albums):
    """Until 0.33 there was one schedule with a tick "with MP3s"."""
    other = tmp_path / "elsewhere"
    old = {
        "enabled": True, "time": "02:15", "every": 3, "albums": albums, "keep": 5, "folder": str(other),
        "last": {"when": "2026-10-10T01:15:00Z", "ok": True, "message": "tinyjukebox-library-20261010-021500.tar.gz"},
        "scheduled": "2026-10-10",
    }  # fmt: skip
    (tmp_path / "backups.json").write_text(json.dumps(old))
    backups = Backups(str(tmp_path / "backups"), str(tmp_path / "backups.json"), no_backup, logger)
    status = backups.status()
    mine, the_other = ("albums", "plain") if albums else ("plain", "albums")
    taken = {key: status[mine][key] for key in ("enabled", "time", "every", "unit", "keep", "folder", "last")}
    assert taken == {
        "enabled": True, "time": "02:15", "every": 3, "unit": "days", "keep": 5, "folder": str(other),
        "last": old["last"],
    }  # fmt: skip
    assert backups._state[mine]["scheduled"] == "2026-10-10"
    defaults = backups_module.DEFAULTS[the_other]
    assert {key: status[the_other][key] for key in defaults} == defaults  # off, with the defaults
    assert (status[the_other]["folder"], status[the_other]["last"]) == (str(tmp_path / "backups"), None)
    # written in the new form with the next change, and read again
    backups.set({the_other: {"keep": 9}})
    assert set(json.loads((tmp_path / "backups.json").read_text())) == {"plain", "albums"}
    again = Backups(str(tmp_path / "backups"), str(tmp_path / "backups.json"), no_backup, logger)
    assert again.status()[mine]["time"] == "02:15"
    assert again.status()[the_other]["keep"] == 9


def test_each_schedule_has_a_folder_of_its_own(kept, tmp_path, logger):
    default = str(tmp_path / "backups")
    status = kept.status()
    assert [(status[kind]["folder"], status[kind]["default_folder"]) for kind in ("plain", "albums")] == [
        (default, default),
        (default, default),
    ]
    first = kept.make(False)
    other = tmp_path / "nas" / "share" / "backups"
    status = kept.set({"albums": {"folder": str(other) + "/"}})
    assert (status["albums"]["folder"], status["albums"]["default_folder"]) == (str(other), default)
    assert status["plain"]["folder"] == default
    assert other.is_dir()  # created
    second = kept.make(True)
    third = kept.make(False)
    assert (sorted(os.listdir(other)), sorted(os.listdir(default))) == ([second], sorted([first, third]))
    # the page lists the backups of both folders in one table, newest first
    assert names(kept) == [third, second, first]
    assert kept.path(second) == str(other / second)
    assert kept.path(first) == os.path.join(default, first)
    # a folder that is not set any more: what was made there stays where it is and is not listed
    elsewhere = tmp_path / "elsewhere"
    kept.set({"albums": {"folder": str(elsewhere)}})
    assert names(kept) == [third, first]
    assert os.listdir(other) == [second]
    with pytest.raises(NotFound):
        kept.path(second)
    assert kept.set({"plain": {"time": "05:00"}})["albums"]["folder"] == str(elsewhere)  # left out: stays
    again = Backups(default, str(tmp_path / "backups.json"), kept._create, logger)
    assert (again.folder("plain"), again.folder("albums")) == (default, str(elsewhere))  # survives a restart
    # what the program cannot use is not taken over
    (tmp_path / "file").write_text("no folder")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    for wrong in ("relative/folder", 7, str(tmp_path / "file" / "below"), str(locked), str(locked / "below")):
        with pytest.raises(BadRequest):
            kept.set({"plain": {"folder": wrong}})
    locked.chmod(0o700)
    assert kept.folder("plain") == default
    # empty, or the folder of the config itself: the one of the config again
    assert kept.set({"albums": {"folder": ""}})["albums"]["folder"] == default
    assert kept.set({"albums": {"folder": str(other)}})["albums"]["folder"] == str(other)
    assert kept.set({"albums": {"folder": default}})["albums"]["folder"] == default
    assert kept._state["albums"]["folder"] is None


def test_the_folder_stays_while_a_backup_is_made(kept, tmp_path):
    kept.start()
    kept._running = True  # as while a backup is written
    with pytest.raises(Conflict, match="cannot be changed"):
        kept.set({"albums": {"folder": str(tmp_path / "other")}})
    assert kept.set({"plain": {"keep": 3, "folder": kept.folder()}})["plain"]["keep"] == 3  # the same folder: no change
    kept._running = False


def test_a_backup_is_made_when_it_is_due_and_when_it_is_asked_for(kept, monkeypatch):
    with pytest.raises(Conflict):
        kept.ask(False)  # not started
    soon = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
    monkeypatch.setattr(
        backups_module, "next_due", lambda now, settings, last: soon if last is None else soon.replace(year=2999)
    )
    # both are due at the same time: the one without the albums is made first, and the other one is not put off
    kept.set({"plain": {"enabled": True}, "albums": {"enabled": True}})
    kept.start()
    assert wait_for(lambda: len(names(kept)) == 2 and not kept.status()["running"])
    status = kept.status()
    assert [entry["albums"] for entry in status["backups"]] == [True, False]  # newest first
    for kind, entry in (("plain", status["backups"][1]), ("albums", status["backups"][0])):
        assert (status[kind]["last"]["ok"], status[kind]["last"]["message"]) == (True, entry["name"])
        assert kept._state[kind]["scheduled"] == datetime.date.today().isoformat()
    # asked for at once: it is of the kind that was asked for and no backup of the schedule
    kept._state["plain"]["scheduled"] = kept._state["albums"]["scheduled"] = "2026-01-01"
    kept.ask(True)
    assert wait_for(lambda: len(names(kept)) == 3 and not kept.status()["running"])
    assert kept.status()["backups"][0]["albums"] is True
    assert kept._state["albums"]["scheduled"] == "2026-01-01"
    kept.ask(False)
    assert wait_for(lambda: len(names(kept)) == 4 and not kept.status()["running"])
    assert kept.status()["backups"][0]["albums"] is False


def test_only_the_schedule_that_is_on_makes_backups(kept, monkeypatch):
    soon = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
    monkeypatch.setattr(
        backups_module, "next_due", lambda now, settings, last: soon if last is None else soon.replace(year=2999)
    )
    kept.set({"albums": {"enabled": True}})
    kept.start()
    assert wait_for(lambda: len(names(kept)) == 1 and not kept.status()["running"])
    assert kept.status()["backups"][0]["albums"] is True
    assert kept.status()["plain"]["last"] is None
    assert not wait_for(lambda: len(names(kept)) > 1, timeout=0.5)


def test_a_backup_that_fails_leaves_nothing_and_is_told(tmp_path, logger):
    def create(albums: bool) -> Backup:
        raise OSError("the disk is gone")

    backups = Backups(str(tmp_path / "backups"), str(tmp_path / "backups.json"), create, logger)
    backups.start()
    backups.ask(True)
    assert wait_for(lambda: backups.status()["albums"]["last"] is not None)
    backups.stop()
    status = backups.status()
    last = status["albums"]["last"]
    assert (last["ok"], last["message"], status["backups"], status["plain"]["last"]) == (
        False, "the disk is gone", [], None,
    )  # fmt: skip
    assert os.listdir(tmp_path / "backups") == []


def test_the_page_sets_the_schedules_asks_for_a_backup_fetches_and_removes_it(bed):
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    status = admin.get("/api/library/backups").json
    assert (status["plain"]["enabled"], status["albums"]["enabled"]) == (False, False)
    assert (status["backups"], status["running"], status["active"], status["plain"]["last"]) == ([], False, True, None)
    assert status["plain"]["folder"] == str(bed.folder / "library" / "backups")  # beside the albums
    assert admin.put("/api/library/backups", {"plain": {"time": "25:00"}}).status == 400
    assert admin.put("/api/library/backups", {"time": "02:15"}).status == 400  # the flat fields of before
    refused = admin.put("/api/library/backups", {"albums": {"folder": "backups"}})
    assert (refused.status, "absolute path" in refused.json["error"]) == (400, True)
    elsewhere = str(bed.folder / "elsewhere")
    changed = admin.put("/api/library/backups", {"albums": {"folder": elsewhere}}).json
    assert (changed["albums"]["folder"], changed["plain"]["folder"]) == (elsewhere, status["plain"]["folder"])
    weekly = {"enabled": True, "time": "02:15", "every": 1, "unit": "weeks", "keep": 52}
    assert admin.put("/api/library/backups", {"plain": weekly}).json["plain"]["keep"] == 52

    assert admin.post("/api/library/backups", {"albums": True}).status == 202

    def made(number):
        status = admin.get("/api/library/backups").json
        return status["backups"] if not status["running"] and len(status["backups"]) == number else []

    (entry,) = wait_for(lambda: made(1))
    assert entry["albums"] is True
    assert os.listdir(elsewhere) == [entry["name"]]  # in the folder of its kind
    assert admin.post("/api/library/backups", {}).status == 202
    plain, entry = wait_for(lambda: made(2))
    assert (plain["albums"], os.listdir(bed.folder / "library" / "backups")) == (False, [plain["name"]])
    stored = admin.get("/api/library/backups/" + entry["name"])
    assert (stored.status, stored.headers.get_filename(), len(stored.content)) == (200, entry["name"], entry["size"])
    assert "albums/short/1.mp3" in members(stored.content)
    assert admin.get("/api/library/backups/unknown.tar").status == 404
    assert admin.request("DELETE", "/api/library/backups/" + entry["name"]).status == 204
    assert [item["name"] for item in admin.get("/api/library/backups").json["backups"]] == [plain["name"]]
    # the schedules are data of the library and part of its backups
    assert json.loads(members(admin.get("/api/backup").content)["data/backups.json"])["plain"]["time"] == "02:15"
    # the library has no schedules of a box
    assert admin.get("/api/backups").status == 404


def test_a_box_without_a_library_makes_backups_by_itself_and_one_with_a_library_does_not(bed, monkeypatch):
    """The same two schedules as the library has, on the page of the box. While a box works with a library, the
    library has the data and makes the backups: no schedule runs on the box, and its settings wait."""
    alone = bed.box("alone", connect=False)
    backups = alone.control.backups
    assert backups.folder() == str(alone.folder / "backups")  # beside the albums folder
    assert (backups.active, backups.status()["active"]) == (True, True)
    assert backups.set({"plain": {"keep": 1}})["plain"]["keep"] == 1
    assert json.loads((alone.folder / "json" / "backups.json").read_text())["plain"]["keep"] == 1
    for _ in range(2):
        backups.ask(False)
        assert wait_for(lambda: not backups.status()["running"])
        time.sleep(1.1)  # the names of backups differ by the second
    backups.ask(True)
    assert wait_for(lambda: not backups.status()["running"] and len(backups.status()["backups"]) == 2)
    full, plain = backups.status()["backups"]
    assert re.fullmatch(r"tinyjukebox-box-\d{8}-\d{6}-albums\.tar", full["name"])
    assert re.fullmatch(r"tinyjukebox-box-\d{8}-\d{6}\.tar\.gz", plain["name"])
    about = verify(backups.path(plain["name"]))
    assert (about["kind"], about["albums"]) == ("box", False)
    with tarfile.open(backups.path(plain["name"])) as archive:
        found = set(archive.getnames())
    assert {"json/tags.json", "json/targets.json", JOURNAL} <= found
    assert "json/backups.json" not in found  # where the backups go is no data of a backup
    # a schedule that is due
    soon = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
    monkeypatch.setattr(
        backups_module, "next_due", lambda now, settings, last: soon if last is None else soon.replace(year=2999)
    )
    backups.set({"plain": {"enabled": True, "keep": 5}})
    assert wait_for(lambda: len(backups.status()["backups"]) == 3)

    # ... and a box with a library
    box = bed.box("box1")
    assert box.synced()
    waiting = box.control.backups
    assert (waiting.active, waiting.status()["active"]) == (False, False)
    status = waiting.set({"plain": {"enabled": True}, "albums": {"enabled": True}})  # kept for the day it is unlinked
    assert (status["plain"]["enabled"], status["plain"]["next"], status["albums"]["next"]) == (True, None, None)
    with pytest.raises(Conflict, match="works with a library"):
        waiting.ask(False)
    assert not wait_for(lambda: waiting.status()["backups"], timeout=0.6)
    assert not (box.folder / "backups").exists()
    # unlinked: its schedules count again
    box.control.unlink_library()
    assert waiting.status()["active"] is True
    assert wait_for(lambda: len(waiting.status()["backups"]) == 2, timeout=10)


# --- the command -----------------------------------------------------------------------------------------------------


def test_the_command_fetches_a_backup_and_reads_it_to_its_end(bed, tmp_path, capsys):
    box = bed.box("box1")
    assert box.synced()
    (tmp_path / "full").mkdir()
    target, about = tool.fetch(admin_client(bed), str(tmp_path / "full"), albums=True)
    assert (about["kind"], about["albums"]) == ("library", True)
    assert os.listdir(tmp_path / "full") == [os.path.basename(target)]
    assert "albums/short/1.mp3" in members((tmp_path / "full" / os.path.basename(target)).read_bytes())

    (tmp_path / "password").write_text("admin-password\n")
    (tmp_path / "plain").mkdir()
    arguments = [bed.url, str(tmp_path / "plain"), "--user", "admin", "--password-file", str(tmp_path / "password")]
    tool.main([*arguments, "--fingerprint", bed.library.api.fingerprint])
    (name,) = os.listdir(tmp_path / "plain")
    assert capsys.readouterr().out.startswith(f"{tmp_path / 'plain' / name}: backup of a library (version ")
    with pytest.raises(SystemExit, match=r"no backup: .*fingerprint"):
        tool.main([*arguments, "--fingerprint", "00"])

    # the folders in which the library keeps its own backups: one for each kind
    there = ["--fingerprint", bed.library.api.fingerprint, "--library-folder"]
    tool.main([*arguments, *there, str(tmp_path / "kept")])
    assert capsys.readouterr().out == f"the library keeps its backups in {tmp_path / 'kept'}\n"
    default = str(bed.folder / "library" / "backups")
    assert (bed.library.backups.folder("plain"), bed.library.backups.folder("albums")) == (
        str(tmp_path / "kept"),
        default,
    )
    with pytest.raises(SystemExit, match="absolute path"):
        tool.main([*arguments, *there, "kept"])
    tool.main([*arguments, *there, "", "--library-folder-albums", str(tmp_path / "full")])
    assert capsys.readouterr().out == (
        f"the library keeps its backups in {default}\n"
        f"the library keeps its backups with the albums in {tmp_path / 'full'}\n"
    )
    assert (bed.library.backups.folder("plain"), bed.library.backups.folder("albums")) == (
        default,
        str(tmp_path / "full"),
    )
    with pytest.raises(SystemExit, match="is no folder"):
        tool.main([bed.url, str(tmp_path / "missing"), "--user", "admin"])


def test_the_command_puts_a_backup_back(bed, tmp_path, capsys, monkeypatch):
    box = bed.box("box1")
    assert box.synced()
    client = admin_client(bed)
    target, _ = tool.fetch(client, str(tmp_path), albums=False)
    asked = []

    def refuse(status):
        asked.append(status)
        return False

    assert tool.restore(client, target, refuse)["state"] == "idle"
    assert (asked[0]["state"], asked[0]["kind"]) == ("staged", "library")
    assert not (bed.folder / "library" / "data.new").exists()  # discarded there
    (tmp_path / "password").write_text("admin-password\n")
    arguments = [bed.url, "--user", "admin", "--password-file", str(tmp_path / "password")]
    arguments += ["--fingerprint", bed.library.api.fingerprint]
    with pytest.raises(SystemExit, match="is no file"):
        tool.main([*arguments, "--restore", str(tmp_path / "missing.tar.gz")])
    monkeypatch.setattr("builtins.input", lambda question: "n")
    tool.main([*arguments, "--restore", target])
    said = capsys.readouterr().out
    assert "This replaces all data of the library by those of the backup of" in said
    assert said.endswith("not put back\n")
    tool.main([*arguments, "--restore", target, "--yes"])
    assert capsys.readouterr().out.endswith("put back: it starts again with the data of the backup\n")
    assert bed.library.restore.confirmed
    started_again(bed)


def test_the_command_keeps_no_backup_that_was_cut_off(tmp_path):
    out = io.BytesIO()
    backup = Backup("library", True, [("data/ids.json", b"{}" * 5000)])
    backup.write(out)

    class CutOff:
        def __init__(self, name: str, size: int) -> None:
            self.name, self.size = name, size

        def request(self, method, path, sink):
            with open(sink, "wb") as outfile:
                outfile.write(out.getvalue()[: self.size])
            headers = email.message.Message()
            headers["Content-Disposition"] = f'attachment; filename="{self.name}"'
            return type("Answer", (), {"headers": headers})()

    for client in (CutOff(backup.filename, 4000), CutOff("../../evil.tar", len(out.getvalue()))):
        with pytest.raises(Incomplete):
            tool.fetch(client, str(tmp_path), albums=True)  # type: ignore[arg-type]
        assert os.listdir(tmp_path) == []
    whole = CutOff(backup.filename, len(out.getvalue()))
    target, _ = tool.fetch(whole, str(tmp_path), albums=True)  # type: ignore[arg-type]
    assert os.path.basename(target) == backup.filename


def test_the_command_sets_the_folder_of_a_box_and_says_where_no_backups_are_kept():
    asked = []

    class Box:
        """A box: it has no /api/library/backups."""

        def put(self, path, body):
            asked.append((path, body))
            if path != "/api/backups":
                raise HttpError(404, "not found.")
            return {"albums": {"folder": "/somewhere"}}

    assert tool.set_library_folder(Box(), "/somewhere/", albums=True) == "/somewhere"  # type: ignore[arg-type]
    assert asked == [
        ("/api/library/backups", {"albums": {"folder": "/somewhere/"}}),
        ("/api/backups", {"albums": {"folder": "/somewhere/"}}),
    ]

    class Older:
        def put(self, path, body):
            raise HttpError(404, "not found.")

    with pytest.raises(HttpError, match="no backups are kept there"):
        tool.set_library_folder(Older(), "/somewhere")  # type: ignore[arg-type]
