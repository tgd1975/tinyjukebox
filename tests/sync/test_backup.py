"""Backups of the library (docs/developer/library.md, section 4.19): what one holds, that the data put back from it is
the library again, the backups the library makes by itself, and the command that fetches one."""

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
from urllib.parse import quote

import pytest

from tests.support import TAG_ALBUM, ApiClient, wait_for
from tests.sync.bed import BOOTSTRAP
from tests.sync.test_fill import admin_client
from tests.sync.test_library_and_box import album_id
from tests.sync.test_slots import version_in_the_archive
from tinyjukebox.backup import JOURNAL, Backup, Folder, Incomplete, verify
from tinyjukebox.errors import BadRequest, Conflict, NotFound
from tinyjukebox.library import backups as backups_module
from tinyjukebox.library.backups import Backups, next_due
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
    assert wait_for(lambda: len(box.sync.data.journal_lines(None)) >= 2)
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


# --- the backups the library makes by itself -------------------------------------------------------------------------


def at(text: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(text)


@pytest.mark.parametrize(
    ("now", "every", "last", "due"),
    [
        ("2026-10-10 02:00", 1, None, "2026-10-10 03:00"),  # never made: today
        ("2026-10-10 03:00", 1, None, "2026-10-11 03:00"),  # the time has passed: tomorrow, not at once
        ("2026-10-10 12:00", 1, "2026-10-10", "2026-10-11 03:00"),
        ("2026-10-10 12:00", 7, "2026-10-10", "2026-10-17 03:00"),
        ("2026-10-16 23:59", 7, "2026-10-10", "2026-10-17 03:00"),
        ("2026-10-20 01:00", 7, "2026-10-10", "2026-10-20 03:00"),  # the library did not run when it was due
        ("2026-10-20 04:00", 7, "2026-10-10", "2026-10-21 03:00"),
    ],
)
def test_when_the_next_backup_is_due(now, every, last, due):
    settings = {"time": "03:00", "every": every}
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


def test_the_oldest_backups_are_removed_beyond_the_number_to_keep(kept, tmp_path):
    assert kept.set({"keep": 2})["keep"] == 2
    names = [kept.make(albums) for albums in (False, True, False)]
    status = kept.status()
    assert [entry["name"] for entry in status["backups"]] == [names[2], names[1]]  # newest first
    assert [entry["albums"] for entry in status["backups"]] == [False, True]
    assert status["backups"][0]["size"] == os.path.getsize(tmp_path / "backups" / names[2])
    # the time in the name is the local time of the library, whatever the time zone of the computer
    assert status["backups"][0]["when"] == time.strftime("%Y-%m-%d %H:%M", time.localtime(1_800_000_002))
    assert verify(kept.path(names[1]))["files"] == 1
    # other files in the folder are neither listed nor touched
    (tmp_path / "backups" / "notes.txt").write_text("mine")
    (tmp_path / "backups" / "tinyjukebox-box-20200101-000000.tar.gz").write_text("of a box")
    kept.make(False)
    assert len(kept.status()["backups"]) == 2
    assert {"notes.txt", "tinyjukebox-box-20200101-000000.tar.gz"} <= set(os.listdir(tmp_path / "backups"))
    with pytest.raises(NotFound):
        kept.path("notes.txt")
    with pytest.raises(NotFound):
        kept.path("../backups.json")
    kept.remove(names[2])
    assert names[2] not in os.listdir(tmp_path / "backups")


def test_the_schedule_is_checked_and_kept(kept, tmp_path, logger):
    assert {key: kept.status()[key] for key in backups_module.DEFAULTS} == backups_module.DEFAULTS
    assert kept.status()["next"] is None  # no schedule yet
    status = kept.set({"enabled": True, "time": "04:30", "every": 7, "albums": True, "keep": 3})
    assert (status["enabled"], status["time"], status["every"], status["albums"], status["keep"]) == (
        True, "04:30", 7, True, 3,
    )  # fmt: skip
    assert status["next"].endswith(" 04:30")
    for wrong in ({"time": "4:30"}, {"time": "24:00"}, {"every": 0}, {"keep": 1000}, {"keep": "3"}, {"enabled": 1}):
        with pytest.raises(BadRequest):
            kept.set(wrong)
    again = Backups(kept.folder, str(tmp_path / "backups.json"), kept._create, logger)
    assert again.status()["time"] == "04:30"  # survives a restart


def test_the_folder_of_the_backups_is_set_like_the_schedule(kept, tmp_path, logger):
    default = str(tmp_path / "backups")
    assert (kept.status()["folder"], kept.status()["default_folder"]) == (default, default)
    first = kept.make(False)
    other = tmp_path / "nas" / "share" / "backups"
    status = kept.set({"folder": str(other) + "/"})
    assert (status["folder"], status["default_folder"], status["backups"]) == (str(other), default, [])
    assert other.is_dir()  # created
    second = kept.make(True)
    assert [entry["name"] for entry in kept.status()["backups"]] == [second]
    assert (os.listdir(other), os.listdir(default)) == ([second], [first])  # what was made before stays where it is
    with pytest.raises(NotFound):
        kept.path(first)
    assert kept.set({"time": "05:00"})["folder"] == str(other)  # left out: stays
    again = Backups(default, str(tmp_path / "backups.json"), kept._create, logger)
    assert again.folder == str(other)  # survives a restart
    # what the library cannot use is not taken over
    (tmp_path / "file").write_text("no folder")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    for wrong in ("relative/folder", 7, str(tmp_path / "file" / "below"), str(locked), str(locked / "below")):
        with pytest.raises(BadRequest):
            kept.set({"folder": wrong})
    locked.chmod(0o700)
    assert kept.folder == str(other)
    # empty, or the folder of the config itself: the one of the config again
    assert kept.set({"folder": ""})["folder"] == default
    assert kept.set({"folder": str(other)})["folder"] == str(other)
    assert kept.set({"folder": default})["folder"] == default
    assert kept._state["folder"] is None


def test_the_folder_stays_while_a_backup_is_made(kept, tmp_path):
    kept.start()
    kept._running = True  # as while a backup is written
    with pytest.raises(Conflict, match="cannot be changed"):
        kept.set({"folder": str(tmp_path / "other")})
    assert kept.set({"keep": 3, "folder": kept.folder})["keep"] == 3  # the same folder is no change
    kept._running = False


def test_a_backup_is_made_when_it_is_due_and_when_it_is_asked_for(kept, monkeypatch):
    with pytest.raises(Conflict):
        kept.ask(False)  # not started
    soon = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
    monkeypatch.setattr(
        backups_module, "next_due", lambda now, settings, last: soon if last is None else soon.replace(year=2999)
    )
    kept.set({"enabled": True, "albums": True})
    kept.start()
    assert wait_for(lambda: len(kept.status()["backups"]) == 1 and not kept.status()["running"])
    status = kept.status()
    assert status["backups"][0]["albums"] is True
    assert (status["last"]["ok"], status["last"]["message"]) == (True, status["backups"][0]["name"])
    assert kept._state["scheduled"] == datetime.date.today().isoformat()
    kept.ask(False)
    assert wait_for(lambda: len(kept.status()["backups"]) == 2 and not kept.status()["running"])
    assert kept.status()["backups"][0]["albums"] is False


def test_a_backup_that_fails_leaves_nothing_and_is_told(tmp_path, logger):
    def create(albums: bool) -> Backup:
        raise OSError("the disk is gone")

    backups = Backups(str(tmp_path / "backups"), str(tmp_path / "backups.json"), create, logger)
    backups.start()
    backups.ask(False)
    assert wait_for(lambda: backups.status()["last"] is not None)
    backups.stop()
    status = backups.status()
    assert (status["last"]["ok"], status["last"]["message"], status["backups"]) == (False, "the disk is gone", [])
    assert os.listdir(tmp_path / "backups") == []


def test_the_page_sets_the_schedule_asks_for_a_backup_fetches_and_removes_it(bed):
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    status = admin.get("/api/library/backups").json
    assert (status["enabled"], status["backups"], status["running"], status["last"]) == (False, [], False, None)
    assert status["folder"] == str(bed.folder / "library" / "backups")  # beside the albums
    assert admin.put("/api/library/backups", {"time": "25:00"}).status == 400
    refused = admin.put("/api/library/backups", {"folder": "backups"})
    assert (refused.status, "absolute path" in refused.json["error"]) == (400, True)
    elsewhere = str(bed.folder / "elsewhere")
    assert admin.put("/api/library/backups", {"folder": elsewhere}).json["folder"] == elsewhere
    assert admin.put("/api/library/backups", {"enabled": True, "time": "02:15", "keep": 5}).json["keep"] == 5

    assert admin.post("/api/library/backups", {"albums": True}).status == 202

    def made():
        status = admin.get("/api/library/backups").json
        return status["backups"] if not status["running"] else []

    (entry,) = wait_for(made)
    assert entry["albums"] is True
    assert os.listdir(elsewhere) == [entry["name"]]
    stored = admin.get("/api/library/backups/" + entry["name"])
    assert (stored.status, stored.headers.get_filename(), len(stored.content)) == (200, entry["name"], entry["size"])
    assert "albums/short/1.mp3" in members(stored.content)
    assert admin.get("/api/library/backups/unknown.tar").status == 404
    assert admin.request("DELETE", "/api/library/backups/" + entry["name"]).status == 204
    assert admin.get("/api/library/backups").json["backups"] == []
    # the schedule is data of the library and part of its backups
    assert json.loads(members(admin.get("/api/backup").content)["data/backups.json"])["time"] == "02:15"


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

    # the folder in which the library keeps its own backups
    there = ["--fingerprint", bed.library.api.fingerprint, "--library-folder"]
    tool.main([*arguments, *there, str(tmp_path / "kept")])
    assert capsys.readouterr().out == f"the library keeps its backups in {tmp_path / 'kept'}\n"
    assert bed.library.backups.folder == str(tmp_path / "kept")
    with pytest.raises(SystemExit, match="absolute path"):
        tool.main([*arguments, *there, "kept"])
    tool.main([*arguments, *there, ""])
    assert bed.library.backups.folder == str(bed.folder / "library" / "backups")
    with pytest.raises(SystemExit, match="is no folder"):
        tool.main([bed.url, str(tmp_path / "missing"), "--user", "admin"])


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


def test_the_command_says_that_a_box_keeps_no_backups():
    class Box:
        def put(self, path, body):
            raise HttpError(404, "not found.")

    with pytest.raises(HttpError, match="only the library keeps backups"):
        tool.set_library_folder(Box(), "/somewhere")  # type: ignore[arg-type]
