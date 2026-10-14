"""Putting a backup back (tinyjukebox.restore): unpacked beside the data and checked first, swapped when the program
does not use the folders, finished after a power cut, the data of before removed once the restored data runs."""

import io
import json
import logging
import os
import tarfile
from pathlib import Path

import pytest

from tinyjukebox import restore as restore_module
from tinyjukebox.backup import Backup, Folder, Incomplete
from tinyjukebox.errors import BadRequest, Conflict, InsufficientStorage
from tinyjukebox.restore import BEFORE, NEW, STATE, Place, Restore, missing_albums
from tinyjukebox.sync.util import read_json

ALBUM = {"target-id": "1", "target-type": "album", "album-folder": "Album A", "playlist": "p.m3u", "created": "x"}


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)


@pytest.fixture
def box(tmp_path):
    """The folders of a box: the json files with the images inside, the albums, the folder of the synchronization."""
    root = tmp_path / "box"
    write(root / "json" / "tags.json", '{"now": 1}')
    write(root / "json" / "targets.json", json.dumps({"1": ALBUM}))
    write(root / "json" / "backups.json", '{"kept": "here"}')
    write(root / "json" / "backup" / "2026" / "tags.json", "{}")
    write(root / "json" / "images" / "album-00000001.jpg", b"new image")
    write(root / "albums" / "Album A" / "1.mp3", b"now")
    (root / "sync").mkdir()
    return root


def make(root: Path, logger: logging.Logger, **kwargs) -> Restore:
    places = [
        Place("json", str(root / "json"), keep=("backups.json", "backup")),
        Place("images", str(root / "json" / "images")),
        Place("albums", str(root / "albums")),
    ]
    return Restore("box", places, str(root / "sync"), logger, **kwargs)


def backup_of(tmp_path: Path, albums: bool, kind: str = "box", about: dict | None = None) -> str:
    """A backup as the box makes it: json files, images, the lines of the journal and, with ``albums``, an album."""
    source = tmp_path / f"source-{kind}-{albums}"
    write(source / "images" / "album-0000000a.png", b"old image")
    write(source / "albums" / "Album A" / "1.mp3", b"before")
    write(source / "albums" / "Album B" / "1.mp3", b"b")
    targets = {"1": ALBUM, "2": {**ALBUM, "target-id": "2", "album-folder": "Album B", "title": "B"}}
    data = [
        ("json/tags.json", b'{"before": 1}'),
        ("json/targets.json", json.dumps(targets).encode()),
        ("json/journal.jsonl", b'{"n": 7, "start": 3, "event": "tag"}\n'),
    ]
    folders = [Folder("images", str(source / "images"))]
    if albums:
        folders.append(Folder("albums", str(source / "albums")))
    backup = Backup(kind, albums, data, folders, created=1_800_000_000)
    target, _ = backup.save(str(tmp_path))
    if about is not None:  # another first file
        with tarfile.open(target) as archive:
            members = [(member, archive.extractfile(member).read()) for member in archive]  # type: ignore[union-attr]
        with tarfile.open(target, "w:gz" if target.endswith(".gz") else "w") as archive:
            for member, original in members:
                content = json.dumps(about).encode() if member.name == "backup.json" else original
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
    return target


def test_a_backup_is_unpacked_beside_the_data_and_swapped_in_when_it_is_confirmed(box, tmp_path, logger):
    lines: list[dict] = []
    restore = make(box, logger, journal=lines.extend, missing=lambda: [])
    archive = backup_of(tmp_path, albums=True)
    about = restore.stage(archive)
    assert (about["kind"], about["albums"], restore.state) == ("box", True, "staged")
    # nothing of the current data is touched
    assert (box / "json" / "tags.json").read_text() == '{"now": 1}'
    assert (box / "albums" / "Album A" / "1.mp3").read_bytes() == b"now"
    assert (box / f"json{NEW}" / "tags.json").read_text() == '{"before": 1}'
    assert (box / f"json{NEW}" / "images" / "album-0000000a.png").read_bytes() == b"old image"  # inside the json folder
    assert (box / f"albums{NEW}" / "Album B" / "1.mp3").read_bytes() == b"b"
    assert not (box / f"json{NEW}" / "journal.jsonl").exists()  # the lines go to the journal, not into the folder
    with pytest.raises(Conflict):
        restore.apply()  # not confirmed

    restore.confirm()
    assert restore.confirmed
    result = restore.apply()
    assert sorted(os.listdir(box)) == sorted(["albums", f"albums{BEFORE}", "json", f"json{BEFORE}", "sync"])
    assert (box / "json" / "tags.json").read_text() == '{"before": 1}'
    assert os.listdir(box / "json" / "images") == ["album-0000000a.png"]
    assert sorted(os.listdir(box / "albums")) == ["Album A", "Album B"]
    assert (box / "albums" / "Album A" / "1.mp3").read_bytes() == b"before"
    # what a restore does not replace: carried over
    assert (box / "json" / "backups.json").read_text() == '{"kept": "here"}'
    assert (box / "json" / "backup" / "2026" / "tags.json").exists()
    assert lines == [{"n": 7, "start": 3, "event": "tag"}]
    assert result["backup"]["created"] == about["created"]
    assert (result["missing"], restore.last(), restore.state) == ([], result, "idle")
    # once the program runs with it, the data of before goes
    restore.clean()
    assert wait_until(lambda: not (box / f"json{BEFORE}").exists() and not (box / f"albums{BEFORE}").exists())


def wait_until(condition) -> bool:
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_backup_without_the_albums_leaves_the_albums_and_names_those_that_are_missing(box, tmp_path, logger, caplog):
    targets = str(box / "json" / "targets.json")
    restore = make(box, logger, missing=lambda: missing_albums(read_json(targets, {}), str(box / "albums")))
    restore.stage(backup_of(tmp_path, albums=False))
    assert not (box / f"albums{NEW}").exists()
    restore.confirm()
    result = restore.apply()
    assert (box / "albums" / "Album A" / "1.mp3").read_bytes() == b"now"  # untouched
    assert not (box / f"albums{BEFORE}").exists()
    assert result["missing"] == [{"id": "2", "title": "B", "folder": "Album B"}]
    assert "restore: album 'B' (2) is in the data, but its folder 'Album B' is missing." in caplog.text
    assert read_json(str(box / "sync" / STATE))["missing"] == result["missing"]


@pytest.mark.parametrize(
    ("about", "refused", "code"),
    [
        ({"format": 1, "kind": "library", "albums": False}, BadRequest, "restore.kind"),
        ({"format": 2, "kind": "box", "albums": False, "version": "9.0.0"}, BadRequest, "restore.format"),
        ({"kind": "box", "albums": False}, BadRequest, "restore.format"),
    ],
)
def test_a_backup_of_another_kind_or_of_a_newer_format_is_refused(box, tmp_path, logger, about, refused, code):
    restore = make(box, logger)
    with pytest.raises(refused) as error:
        restore.stage(backup_of(tmp_path, albums=False, about=about))
    assert error.value.details["code"] == code
    assert (restore.state, restore.error) == ("idle", str(error.value))
    assert sorted(os.listdir(box)) == ["albums", "json", "sync"]  # nothing is left


def test_a_backup_that_was_cut_off_or_is_none_is_refused_and_nothing_is_left(box, tmp_path, logger):
    restore = make(box, logger)
    archive = backup_of(tmp_path, albums=True)
    content = Path(archive).read_bytes()
    for name, data in (("cut.tar", content[: len(content) // 2]), ("none.tar", b"no archive" * 100)):
        write(tmp_path / name, data)
        with pytest.raises(Incomplete):
            restore.stage(str(tmp_path / name))
        assert sorted(os.listdir(box)) == ["albums", "json", "sync"]
        assert restore.state == "idle"
    # an archive that does not begin with backup.json
    with tarfile.open(tmp_path / "other.tar", "w") as other:
        other.add(str(box / "json" / "tags.json"), "json/tags.json")
    with pytest.raises(Incomplete, match="does not begin"):
        restore.stage(str(tmp_path / "other.tar"))
    # a name that leads out of its folder
    with tarfile.open(archive) as archive_file:
        members = [(member, archive_file.extractfile(member).read()) for member in archive_file]  # type: ignore[union-attr]
    with tarfile.open(tmp_path / "evil.tar", "w") as evil:
        for member, data in members:
            if member.name == "complete.json":
                bad = tarfile.TarInfo("json/../../evil.txt")
                bad.size = 4
                evil.addfile(bad, io.BytesIO(b"evil"))
            evil.addfile(member, io.BytesIO(data))
    with pytest.raises(Incomplete, match="leads out"):
        restore.stage(str(tmp_path / "evil.tar"))
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(os.listdir(box)) == ["albums", "json", "sync"]


def test_a_backup_with_the_albums_needs_their_space_twice(box, tmp_path, logger, monkeypatch):
    restore = make(box, logger)
    archive = backup_of(tmp_path, albums=True)
    monkeypatch.setattr("tinyjukebox.restore.shutil.disk_usage", lambda path: Usage(1000))
    with pytest.raises(InsufficientStorage) as error:
        restore.stage(archive)
    assert error.value.details["missing"] == os.path.getsize(archive) + restore_module.KEEP_FREE - 1000
    assert sorted(os.listdir(box)) == ["albums", "json", "sync"]
    restore.stage(backup_of(tmp_path, albums=False))  # without the albums it needs no more than it has


class Usage:
    def __init__(self, free: int) -> None:
        self.free = free


def test_a_backup_is_read_from_a_stream_of_its_length(box, tmp_path, logger):
    restore = make(box, logger)
    content = Path(backup_of(tmp_path, albums=False)).read_bytes()
    about = restore.stage(io.BytesIO(content + b"more that is not read"), len(content))
    assert about["kind"] == "box"
    with pytest.raises(Incomplete):
        restore.stage(io.BytesIO(content[:100]), len(content))  # the request ended early
    assert restore.state == "idle"


def test_one_restore_at_a_time_and_one_that_is_not_wanted_is_discarded(box, tmp_path, logger):
    restore = make(box, logger)
    with pytest.raises(Conflict):
        restore.confirm()  # nothing unpacked
    archive = backup_of(tmp_path, albums=True)
    restore.stage(archive)
    restore.stage(archive)  # the one of before is replaced
    restore.discard()
    assert (restore.state, sorted(os.listdir(box))) == ("idle", ["albums", "json", "sync"])
    restore.stage(archive)
    restore.confirm()
    with pytest.raises(Conflict):
        restore.stage(archive)
    with pytest.raises(Conflict):
        restore.discard()
    told = []
    restore.on_confirm = lambda: told.append(1)
    restore.state = "staged"
    restore.confirm()
    assert told == [1]


def test_a_swap_that_a_power_cut_interrupted_is_finished_at_the_start(box, tmp_path, logger):
    restore = make(box, logger, missing=lambda: [])
    restore.stage(backup_of(tmp_path, albums=True))
    restore.confirm()
    # the power is cut after the json folder was swapped and before the albums were
    (box / "sync" / STATE).write_text(json.dumps({"swapping": ["json", "albums"], "about": {"created": "then"}}))
    os.replace(box / "json", box / f"json{BEFORE}")
    os.replace(box / f"json{NEW}", box / "json")
    again = make(box, logger, missing=lambda: [])
    again.finish()
    assert (box / "json" / "tags.json").read_text() == '{"before": 1}'
    assert (box / "albums" / "Album A" / "1.mp3").read_bytes() == b"before"
    assert (box / f"albums{BEFORE}" / "Album A" / "1.mp3").read_bytes() == b"now"
    assert (again.last() or {})["backup"]["created"] == "then"
    # unpacked and never put back: removed at the start
    again.stage(backup_of(tmp_path, albums=False))
    make(box, logger).finish()
    assert not (box / f"json{NEW}").exists()


def test_missing_albums():
    targets = {"1": ALBUM, "2": {"target-type": "radio", "name": "FM4"}, "10": {**ALBUM, "album-folder": "x/Album C"}}
    assert missing_albums(targets, "/nowhere") == [
        {"id": "1", "title": "Album A", "folder": "Album A"},
        {"id": "10", "title": "Album C", "folder": "x/Album C"},
    ]
