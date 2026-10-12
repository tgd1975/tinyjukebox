"""tinyjukebox-migrate-01: json files of the old box (version 0.1) -> targets.json, tags.json, empty activity.json."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tinyjukebox.data.common import is_valid_timestamp
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.targets import Targets
from tinyjukebox.tools.migrate_box01 import convert_albums, main

OLD_ALBUMS: dict[str, Any] = {
    "45": {"folder": "/srv/tinyjukebox/albums/Serie/02_Teil/", "id": "45", "playlist": "playlist.m3u"},
    "7": {"folder": "/srv/tinyjukebox/albums/Album A", "id": "7", "playlist": "playlist.m3u"},
    "112": {"folder": "/srv/tinyjukebox/albums/test/", "id": "112", "playlist": "playlist.m3u"},
}


def old_tag(uid: str, album_id: str, created: str) -> dict[str, Any]:
    return {"created": created, "target": {"type": "album", "id": album_id}, "tag": {"uid": uid, "name": f"tag {uid}"}}


OLD_TAGS: dict[str, Any] = {
    "04:92": old_tag("04:92", "45", "2021-05-01T10:00:00.000000Z"),
    "04:93": old_tag("04:93", "45", "2019-12-24T10:00:00.000000Z"),
    "53:1D": old_tag("53:1D", "7", "2020-01-02T08:00:00.000000Z"),
}
OLD_ACTIVITY = {"04:92": {"timestamp": "2025-12-24T10:00:00.000000Z", "target": {"track-id": 3, "track-pos": 12.5}}}


@pytest.fixture
def old(tmp_path: Path) -> Path:
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "albums.json").write_text(json.dumps(OLD_ALBUMS), encoding="utf-8")
    (folder / "tags.json").write_text(json.dumps(OLD_TAGS), encoding="utf-8")
    (folder / "activity.json").write_text(json.dumps(OLD_ACTIVITY), encoding="utf-8")
    return folder


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_convert_albums_makes_the_folders_relative() -> None:
    targets = convert_albums(OLD_ALBUMS, OLD_TAGS, "/srv/tinyjukebox/albums")
    assert targets["45"] == {
        "target-id": "45",
        "target-type": "album",
        "album-folder": "Serie/02_Teil",
        "playlist": "playlist.m3u",
        "created": "2019-12-24T10:00:00.000000Z",  # the oldest tag of the album
    }
    assert targets["7"]["album-folder"] == "Album A"
    assert targets["7"]["created"] == "2020-01-02T08:00:00.000000Z"
    assert is_valid_timestamp(targets["112"]["created"])  # no tag: now


@pytest.mark.parametrize(
    ("album", "message"),
    [
        ({"folder": "/media/usb/Album A", "id": "1", "playlist": "playlist.m3u"}, "is not inside"),
        ({"folder": "/srv/tinyjukebox/albums/", "id": "1", "playlist": "playlist.m3u"}, "is not inside"),
        ({"folder": "/srv/tinyjukebox/albums/../Album A", "id": "1", "playlist": "playlist.m3u"}, "is not inside"),
        ({"folder": "/srv/tinyjukebox/albums/Album A", "id": "2", "playlist": "playlist.m3u"}, "its 'id' is '2'"),
        ({"folder": "/srv/tinyjukebox/albums/Album A", "id": "1"}, "are required"),
    ],
)
def test_convert_albums_rejects(album: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        convert_albums({"1": album}, {}, "/srv/tinyjukebox/albums")


def test_convert_albums_reports_all_problems() -> None:
    albums = {"1": {"folder": "/x/a", "playlist": "p.m3u"}, "2": {"folder": "/x/b", "playlist": "p.m3u"}}
    with pytest.raises(ValueError, match=r"album '1'(.|\n)*album '2'"):
        convert_albums(albums, {}, "/srv/tinyjukebox/albums")


def test_main_writes_files_the_box_can_load(old: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new = tmp_path / "new" / "json"
    main([str(old), str(new)])

    assert read(new / "tags.json") == OLD_TAGS
    assert read(new / "activity.json") == {}  # every album starts at its beginning
    assert set(read(new / "targets.json")) == {"45", "7", "112"}
    out, err = capsys.readouterr()
    assert out == "album without tag: 112  test\n"
    assert "3 albums, 3 tags" in err

    # the data classes of the box accept the files
    logger = logging.getLogger("test_migrate_box01")
    targets = Targets(str(new / "targets.json"), str(tmp_path / "albums"), logger)
    tags = Tags(str(new / "tags.json"), logger)
    assert sorted(targets.targets) == ["112", "45", "7"]
    assert len(tags.tags) == 3
    assert targets._highest_id() == 112  # new ids follow the highest old one


def test_main_other_albums_folder(old: Path, tmp_path: Path) -> None:
    albums = {"1": {"folder": "/mnt/music/Album A/", "id": "1", "playlist": "list.m3u"}}
    (old / "albums.json").write_text(json.dumps(albums), encoding="utf-8")
    (old / "tags.json").write_text("{}", encoding="utf-8")
    main([str(old), str(tmp_path / "new"), "--old-albums-folder", "/mnt/music"])
    assert read(tmp_path / "new" / "targets.json")["1"]["album-folder"] == "Album A"


def test_main_does_not_overwrite(old: Path, tmp_path: Path) -> None:
    new = tmp_path / "new"
    new.mkdir()
    (new / "tags.json").write_text('{"keep": "me"}', encoding="utf-8")
    with pytest.raises(SystemExit, match=r"already contains tags\.json"):
        main([str(old), str(new)])
    assert read(new / "tags.json") == {"keep": "me"}
    assert not (new / "targets.json").exists()

    main([str(old), str(new), "--force"])
    assert read(new / "tags.json") == OLD_TAGS


def test_main_warns_about_tags_without_album(old: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tags = dict(OLD_TAGS, **{"AA:BB": old_tag("AA:BB", "99", "2022-01-01T00:00:00.000000Z")})
    (old / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    main([str(old), str(tmp_path / "new")])
    assert "linked to album '99', which does not exist" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("albums.json", None, "cannot read"),
        ("albums.json", "{broken", "cannot read"),
        ("albums.json", "[]", "must contain a json object"),
        ("tags.json", '{"04:92": {"created": "x"}}', r"tags\.json: .*required"),
        ("albums.json", '{"1": {"folder": "/elsewhere/a", "id": "1", "playlist": "p.m3u"}}', "is not inside"),
    ],
)
def test_main_rejects_bad_input(old: Path, tmp_path: Path, name: str, content: str | None, message: str) -> None:
    if content is None:
        (old / name).unlink()
    else:
        (old / name).write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match=message):
        main([str(old), str(tmp_path / "new")])
    assert not (tmp_path / "new" / "targets.json").exists()
