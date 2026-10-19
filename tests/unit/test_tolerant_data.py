"""Damaged and unknown data (docs/developer/library.md, section 4.21): a version uses what it can read of its json
files, leaves out the rest without deleting it, and says so."""

import json

import pytest

from tinyjukebox.data import degraded
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.target import TargetAlbum, TargetRadio
from tinyjukebox.data.targets import Targets

CREATED = "2026-10-01T10:00:00.000000Z"
ALBUM = {"target-id": "1", "target-type": "album", "album-folder": "a", "playlist": "playlist.m3u", "created": CREATED}
RADIO = {"target-id": "2", "target-type": "radio", "name": "FM4", "url": "https://example.org/fm4", "created": CREATED}
NEWER = {
    "target-id": "3",
    "target-type": "podcast",
    "name": "News",
    "feed": "https://example.org/feed",
    "created": CREATED,
}


def tag(uid, kind, target_id, **extra):
    return {"tag": {"uid": uid, "name": uid}, "target": {"type": kind, "id": target_id}, "created": CREATED, **extra}


def test_a_catalog_of_a_newer_version_loads_with_what_this_version_knows(tmp_path, logger):
    """A newer field on an album and a kind of target this version does not know: albums and stations stay
    usable, the unknown target is left out and reported, and nothing of it is lost when this version writes."""
    targets_file, tags_file = tmp_path / "targets.json", tmp_path / "tags.json"
    targets_file.write_text(json.dumps({"1": {**ALBUM, "rating": 5}, "2": RADIO, "3": NEWER}))
    tags_file.write_text(
        json.dumps({"A": tag("A", "album", "1"), "N": tag("N", "podcast", "3"), "R": tag("R", "radio", "2")})
    )
    targets = Targets(str(targets_file), str(tmp_path), logger)
    tags = Tags(str(tags_file), logger)
    assert sorted(targets.targets) == ["1", "2"]
    assert sorted(tags.tags) == ["A", "R"]
    assert tags.unavailable == {"N"}
    assert any("targets.json: entry '3'" in note for note in degraded.notes())
    assert any("tags.json: entry 'N'" in note for note in degraded.notes())
    # this version writes its own state
    changed = []
    targets.listen(lambda key, value: changed.append((key, value)))
    station = targets.targets["2"]
    assert isinstance(station, TargetRadio)
    station.update(name="FM 4")
    tags.tags["A"].set_tag_name("Album")
    written = json.loads(targets_file.read_text())
    assert written["3"] == NEWER
    assert written["1"]["rating"] == 5
    assert changed == [("2", {**RADIO, "name": "FM 4"})]
    assert json.loads(tags_file.read_text())["N"] == tag("N", "podcast", "3")
    # a new tag with the uid of a tag set aside is refused: it would replace what the library has
    with pytest.raises(ValueError, match="exists"):
        tags.add_tag("N")


def test_a_change_keeps_the_fields_of_a_newer_version(tmp_path, logger):
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(json.dumps({"1": {**ALBUM, "rating": 5}}))
    targets = Targets(str(targets_file), str(tmp_path), logger)
    changed = []
    targets.listen(lambda key, value: changed.append(value))
    album = targets.targets["1"]
    assert isinstance(album, TargetAlbum)
    album.set_title("Title")
    assert changed == [{**ALBUM, "rating": 5, "title": "Title"}]  # what goes to the library keeps the field


def test_one_damaged_entry_is_reported_and_skipped(tmp_path, logger):
    targets_file = tmp_path / "targets.json"
    broken = {k: v for k, v in ALBUM.items() if k != "playlist"}
    targets_file.write_text(json.dumps({"1": broken, "2": RADIO}))
    targets = Targets(str(targets_file), str(tmp_path), logger)
    assert list(targets.targets) == ["2"]
    assert degraded.notes() == ["targets.json: entry '1' cannot be used by this version and is left out"]


def test_what_the_library_delivers_is_taken_entry_by_entry(tmp_path, logger):
    """With a library, the json files are made from what it delivered: an entry this version cannot use is left out
    of the collection, kept in the file, and forgotten when the library no longer has it."""
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(json.dumps({"1": ALBUM}))
    targets = Targets(str(targets_file), str(tmp_path), logger)
    assert targets.sync(lambda: {"1": ALBUM, "3": NEWER})
    assert list(targets.targets) == ["1"]
    assert json.loads(targets_file.read_text()) == {"1": ALBUM, "3": NEWER}
    assert degraded.notes() == ["targets.json: entry '3' cannot be used by this version and is left out"]
    assert targets.sync(lambda: {"1": ALBUM})
    assert json.loads(targets_file.read_text()) == {"1": ALBUM}
    assert degraded.notes() == []


def test_a_position_of_an_unknown_kind_is_set_aside(tmp_path, logger):
    (tmp_path / "tags.json").write_text(json.dumps({"A": tag("A", "album", "1")}))
    position = {"tag-uid": "A", "target-id": "1", "track-id": 1, "track-time": 2.0, "album-finished": False,
                "target-type": "album", "chapter": 3}  # fmt: skip
    newer = {"tag-uid": "N", "target-id": "3", "track-id": 0, "track-time": 0, "album-finished": False,
             "target-type": "podcast"}  # fmt: skip
    (tmp_path / "activity.json").write_text(json.dumps({"A": position, "N": newer}))
    activities = Activities(str(tmp_path / "activity.json"), Tags(str(tmp_path / "tags.json"), logger), logger)
    assert list(activities.activities) == ["A"]
    activities.activities["A"].update(2, 0.0, False)
    written = json.loads((tmp_path / "activity.json").read_text())
    assert written["N"] == newer
    assert written["A"]["chapter"] == 3
