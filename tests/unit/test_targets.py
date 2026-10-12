"""Targets - all targets of targets.json."""

import json
import os

import pytest

from tests.support import prepare_targets
from tinyjukebox.data.common import TargetType, now
from tinyjukebox.data.targets import Targets

SIMPLE = ("simple_album_a", "simple_album_b", "simple_album_c")


@pytest.fixture
def empty(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict")
    return Targets(filename, root, logger)


@pytest.fixture
def one(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_one", ["simple_album_a"])
    return Targets(filename, root, logger)


@pytest.fixture
def multiple(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_multiple", SIMPLE)
    return Targets(filename, root, logger)


def new_album(**fields):
    return {
        "target-type": TargetType.album.name,
        "album-folder": "simple_album_a",
        "playlist": "playlist.m3u",
        "created": now(),
        **fields,
    }


def assert_target(target, target_id, created, album_folder, root):
    assert target.target_id == target_id
    assert target.target_type == TargetType.album
    assert target.created == created
    assert target.full_album_folder == os.path.join(root, album_folder)
    assert target.root_folder == root
    assert target.album_folder == album_folder
    assert target.playlist == "playlist.m3u"


def assert_added(target, json, root):
    assert json["target-type"] == target.target_type.name
    assert os.path.join(root, json["album-folder"]) == target.full_album_folder
    assert json["playlist"] == target.playlist
    assert json["created"] == target.created


def test_init_empty_dict(empty):
    assert len(empty.targets) == 0


def test_init_one_entry(one):
    assert len(one.targets) == 1
    assert_target(one.targets["1"], "1", "2020-12-01T12:34:56.789Z", "simple_album_a", one.root_folder)


def test_init_multiple_entries(multiple):
    root = multiple.root_folder
    assert len(multiple.targets) == 3
    assert_target(multiple.targets["1"], "1", "2020-12-01T12:34:56.789Z", "simple_album_a", root)
    assert_target(multiple.targets["2"], "2", "2020-11-21T12:34:56.789Z", "simple_album_b", root)
    assert_target(multiple.targets["3"], "3", "2019-11-21T12:34:56.789Z", "simple_album_c", root)


def test_get_target(multiple):
    target = multiple.get_target("2")
    assert target.target_id == "2"
    assert target.target_type == TargetType.album
    assert target.created == "2020-11-21T12:34:56.789Z"
    assert target.full_album_folder == os.path.join(multiple.root_folder, "simple_album_b")
    assert target.playlist == "playlist.m3u"

    assert "4" not in multiple.targets
    with pytest.raises(ValueError, match="unknown target_id 4"):
        multiple.get_target("4")


def test_get_targets_of_type(multiple):
    assert len(multiple.get_targets_of_type(TargetType.album)) == 3
    assert len(multiple.get_targets_of_type("test_fas_Fsa")) == 0


def test_delete_target_success(multiple, tmp_path, logger):
    filename = str(tmp_path / "targets.json")
    assert "2" in multiple.targets
    prev_fn = os.stat(filename)
    multiple.delete_target("2")
    assert os.stat(filename) != prev_fn
    assert "2" not in Targets(filename, multiple.root_folder, logger).targets


def test_delete_target_failure(multiple, tmp_path):
    filename = str(tmp_path / "targets.json")
    assert "4" not in multiple.targets
    prev_fn = os.stat(filename)
    with pytest.raises(ValueError, match="unknown target_id 4"):
        multiple.delete_target("4")
    assert os.stat(filename) == prev_fn


def test_to_text_multiple(multiple):
    assert multiple.to_text() == (
        "key: 1, target: id: 1, type: album, created: 2020-12-01T12:34:56.789Z, "
        "album-folder: simple_album_a, playlist: playlist.m3u\n"
        "key: 2, target: id: 2, type: album, created: 2020-11-21T12:34:56.789Z, "
        "album-folder: simple_album_b, playlist: playlist.m3u\n"
        "key: 3, target: id: 3, type: album, created: 2019-11-21T12:34:56.789Z, "
        "album-folder: simple_album_c, playlist: playlist.m3u"
    )


def test_to_text_one(one):
    assert one.to_text() == (
        "key: 1, target: id: 1, type: album, created: 2020-12-01T12:34:56.789Z, "
        "album-folder: simple_album_a, playlist: playlist.m3u"
    )


def test_to_text_none(empty):
    assert empty.to_text() == ""


def test_add_target_to_empty_create_key(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", ["simple_album_a"])
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    json = new_album()
    prev_fn = os.stat(filename)
    target = targets.add_target(json, create_id=True)
    assert os.stat(filename) != prev_fn

    assert len(targets.targets) == 1
    assert target == next(iter(targets.targets.values()))
    assert target.target_id == "1"
    assert_added(target, json, root)


def test_add_target_to_empty_provide_key(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_empty_dict", ["simple_album_a"])
    targets = Targets(filename, root, logger)
    assert len(targets.targets) == 0
    json = new_album(**{"target-id": "myid"})
    prev_fn = os.stat(filename)
    target = targets.add_target(json, create_id=False)
    assert os.stat(filename) != prev_fn

    assert len(targets.targets) == 1
    assert target == next(iter(targets.targets.values()))
    assert json["target-id"] == target.target_id
    assert_added(target, json, root)


def test_add_target_to_multiple_create_key(multiple, tmp_path):
    filename = str(tmp_path / "targets.json")
    assert len(multiple.targets) == 3
    json = new_album()
    prev_fn = os.stat(filename)
    target = multiple.add_target(json, create_id=True)
    assert os.stat(filename) != prev_fn

    assert len(multiple.targets) == 4
    assert target.target_id == "4"
    assert target == multiple.get_target("4")
    assert_added(target, json, multiple.root_folder)


def test_add_target_to_multiple_provide_key_success(multiple, tmp_path):
    filename = str(tmp_path / "targets.json")
    assert len(multiple.targets) == 3
    json = new_album(**{"target-id": "myid"})
    prev_fn = os.stat(filename)
    target = multiple.add_target(json, create_id=False)
    assert os.stat(filename) != prev_fn

    assert len(multiple.targets) == 4
    assert json["target-id"] == target.target_id
    assert target == multiple.get_target(json["target-id"])
    assert_added(target, json, multiple.root_folder)


def test_add_target_to_multiple_provide_key_failure(multiple, tmp_path):
    filename = str(tmp_path / "targets.json")
    assert len(multiple.targets) == 3
    assert "1" in multiple.targets
    json = new_album(**{"target-id": "1"})
    prev_fn = os.stat(filename)
    with pytest.raises(ValueError, match="target_id 1 exists already"):
        multiple.add_target(json, create_id=False)
    assert os.stat(filename) == prev_fn
    assert len(multiple.targets) == 3


def test_generate_new_id_with_gap(multiple, tmp_path):
    filename = str(tmp_path / "targets.json")
    assert len(multiple.targets) == 3
    assert {"1", "2", "3"} <= multiple.targets.keys()
    multiple.delete_target("2")
    assert "2" not in multiple.targets
    json = new_album()
    prev_fn = os.stat(filename)
    target = multiple.add_target(json, create_id=True)
    assert os.stat(filename) != prev_fn

    assert len(multiple.targets) == 3
    assert target.target_id == "4"
    assert target == multiple.get_target(json["target-id"])
    assert_added(target, json, multiple.root_folder)


def last_id_file(tmp_path):
    return tmp_path / "targets-last-id.json"


def test_ids_of_deleted_albums_are_not_given_again(multiple, tmp_path, logger):
    assert not last_id_file(tmp_path).exists()  # e.g. an installation of an older version
    multiple.delete_target("3")  # the highest id
    assert multiple.add_target(new_album()).target_id == "4"
    assert json.loads(last_id_file(tmp_path).read_text()) == {"last-id": 4}
    multiple.delete_target("4")
    reloaded = Targets(str(tmp_path / "targets.json"), multiple.root_folder, logger)  # e.g. after a restart
    assert reloaded.add_target(new_album()).target_id == "5"


def test_failed_add_does_not_use_up_an_id(multiple, tmp_path):
    with pytest.raises(ValueError, match="sanity check failed"):
        multiple.add_target(new_album(**{"album-folder": "missing"}))
    assert not last_id_file(tmp_path).exists()
    assert multiple.add_target(new_album()).target_id == "4"


@pytest.mark.parametrize("content", ["{broken", '{"last-id": "7"}', "[]", '{"last-id": true}'])
def test_unusable_last_id_file(multiple, tmp_path, content):
    """New ids follow the highest existing id - as without the file (installations of older versions)."""
    last_id_file(tmp_path).write_text(content)
    assert multiple.add_target(new_album()).target_id == "4"
    assert json.loads(last_id_file(tmp_path).read_text()) == {"last-id": 4}


def test_stored_last_id_wins_over_existing_ids(multiple, tmp_path):
    last_id_file(tmp_path).write_text('{"last-id": 41}')
    assert multiple.add_target(new_album()).target_id == "42"


def test_own_numeric_id_raises_the_last_id(multiple, tmp_path):
    multiple.add_target(new_album(**{"target-id": "10"}), create_id=False)
    multiple.delete_target("10")
    assert multiple.add_target(new_album()).target_id == "11"


def test_sanity_check_empty(empty):
    assert empty.sanity_check() == (True, "")


def test_sanity_check_one(one):
    assert one.sanity_check() == (True, "")


def test_sanity_check_multiple(multiple):
    assert multiple.sanity_check() == (True, "")


def test_sanity_check_one_album_missing(tmp_path, logger):
    filename, root = prepare_targets(tmp_path, "testdata_one")
    targets = Targets(filename, root, logger)
    passed, errors = targets.sanity_check()
    assert not passed
    assert errors == f"sanity_check - full_album_folder '{root}/simple_album_a' not found."


def test_sanity_check_key_mismatch(multiple):
    multiple.targets["2"].target_id = "22"
    passed, errors = multiple.sanity_check()
    assert not passed
    assert errors == "sanity_check - key '2' != target.target_id '22'"


def test_all(multiple):
    assert [target.target_id for target in multiple.all()] == ["1", "2", "3"]
