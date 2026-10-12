"""Tags: the collection of all rfid tags backed by tags.json."""

import json
import shutil
import threading
from pathlib import Path

import pytest

from tests.support import DATA, file_id
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.tags import Tags

UIDS = ["1:2:3:4", "2:2:3:4", "3:2:3:4", "4:2:3:4"]


@pytest.fixture
def tags_file(tmp_path):
    return str(tmp_path / "tags.json")


@pytest.fixture
def prepare(tags_file):
    """Copy tags.json of a scenario (testdata_empty_dict, testdata_one, testdata_multiple) to ``tmp_path``."""

    def _prepare(scenario):
        shutil.copy(DATA / "tags" / scenario / "tags.json", tags_file)
        return tags_file

    return _prepare


@pytest.fixture
def tags(prepare, logger):
    return Tags(prepare("testdata_multiple"), logger)


def test_init_empty_dict(prepare, logger):
    assert len(Tags(prepare("testdata_empty_dict"), logger).tags) == 0


def test_init_one_entry(prepare, logger):
    tags = Tags(prepare("testdata_one"), logger)
    assert len(tags.tags) == 1
    assert tags.tags["1:2:3:4"].tag_uid == "1:2:3:4"


def test_init_multiple_entries(tags):
    assert len(tags.tags) == 4
    assert list(tags.tags.keys()) == UIDS


def test_init_no_file(tags_file, logger):
    with pytest.raises(FileNotFoundError):
        Tags(tags_file, logger)


def test_add_tag_to_empty_tags(prepare, tags_file, logger):
    tags = Tags(prepare("testdata_empty_dict"), logger)
    assert len(tags.tags) == 0
    added = tags.add_tag("1:2:3:4")
    assert len(tags.tags) == 1
    tag = tags.tags["1:2:3:4"]
    assert tag is added
    assert tag.tag_uid == "1:2:3:4"
    assert tag.tag_name == ""
    assert tag.target_id == -1
    assert tag.target_type is TargetType.album

    tags2 = Tags(tags_file, logger)
    assert len(tags2.tags) == 1
    assert "1:2:3:4" in tags2.tags


def test_add_tag_to_multiple_tags(tags, tags_file, logger):
    assert len(tags.tags) == 4
    assert "5:2:3:4" not in tags.tags
    tags.add_tag("5:2:3:4")
    assert len(tags.tags) == 5

    tags2 = Tags(tags_file, logger)
    assert len(tags2.tags) == 5
    assert list(tags2.tags.keys()) == [*UIDS, "5:2:3:4"]


def test_add_existing_tag(tags):
    assert "1:2:3:4" in tags.tags
    with pytest.raises(ValueError, match="already exists"):
        tags.add_tag("1:2:3:4")


@pytest.mark.parametrize("uid", ["", None])
def test_add_empty_tag(tags, uid):
    assert uid not in tags.tags
    with pytest.raises(ValueError, match="must not be None or empty"):
        tags.add_tag(uid)


def test_get_existing_tag(tags):
    assert tags.get_tag("1:2:3:4").tag_uid == "1:2:3:4"


def test_get_non_existing_tag(tags):
    assert "5:2:3:4" not in tags.tags
    with pytest.raises(ValueError, match="unknown tag"):
        tags.get_tag("5:2:3:4")


def test_get_album_ids(tags):
    album_ids = tags.get_used_target_ids(TargetType.album)
    assert len(album_ids) == 3
    assert album_ids == [123, 256, 4090]


@pytest.mark.parametrize("target_type", [None, "album"])
def test_get_album_ids_wrong_type(tags, target_type):
    with pytest.raises(ValueError, match="must be a TargetType"):
        tags.get_used_target_ids(target_type)


def test_assign_album_to_tag(tags, tags_file):
    assert "1:2:3:4" in tags.tags
    assert tags.get_tag("1:2:3:4").target_id != 4711
    prev_fn = file_id(tags_file)
    tags.assign_target("1:2:3:4", TargetType.album, 4711)
    assert file_id(tags_file) != prev_fn
    assert tags.get_tag("1:2:3:4").target_id == 4711


def test_assign_album_to_non_existing_tag(tags):
    assert "5:2:3:4" not in tags.tags
    with pytest.raises(ValueError, match="unknown tag"):
        tags.assign_target("5:2:3:4", TargetType.album, 4711)


def test_assign_album_to_tag_no_id(tags):
    assert "1:2:3:4" in tags.tags
    with pytest.raises(ValueError, match="target_id is None or empty"):
        tags.assign_target("1:2:3:4", TargetType.album, None)
    with pytest.raises(ValueError, match="target_id is None or empty"):
        tags.assign_target("1:2:3:4", TargetType.album, "")


def test_assign_album_to_tag_no_type(tags):
    assert "1:2:3:4" in tags.tags
    with pytest.raises(ValueError, match="must be a TargetType"):
        tags.assign_target("1:2:3:4", None, 4711)
    with pytest.raises(ValueError, match="must be a TargetType"):
        tags.assign_target("1:2:3:4", "album", 4711)


def test_sanity_check(tags):
    assert tags.sanity_check()


def test_sanity_check_uid_and_key_differ(tags):
    tags.get_tag("1:2:3:4").tag_uid = "5:2:3:4"
    assert not tags.sanity_check()


def test_sanity_check_tag_sanity_check_fails(tags):
    tags.get_tag("1:2:3:4").created = "lalala"
    assert not tags.sanity_check()


def test_delete_tag(tags, tags_file):
    assert "1:2:3:4" in tags.tags
    prev_fn = file_id(tags_file)
    tags.delete_tag("1:2:3:4")
    assert file_id(tags_file) != prev_fn
    assert "1:2:3:4" not in tags.tags


def test_delete_tag_unknown_uid(tags):
    assert "5:2:3:4" not in tags.tags
    for uid in ("5:2:3:4", "", None):
        with pytest.raises(ValueError, match="unknown tag"):
            tags.delete_tag(uid)


def test_to_text(tags):
    lines = tags.to_text().split("\n")
    assert len(lines) == 4
    assert lines[0] == (
        "key: 1:2:3:4, tag: tag: 1:2:3:4/'1234' -> target: album/123. created: 2020-11-08T14:55:55.832228Z"
    )


def test_all_is_a_snapshot(tags):
    snapshot = tags.all()
    assert [tag.tag_uid for tag in snapshot] == UIDS
    tags.add_tag("5:2:3:4")
    assert len(snapshot) == 4
    assert len(tags.all()) == 5


def test_concurrent_changes_do_not_corrupt_the_file(tags, tags_file, logger):
    """State machine, rest api and cli change tags at the same time - every change ends up in a complete file."""
    threads_count, per_thread = 6, 5
    barrier = threading.Barrier(threads_count + 1)
    done = threading.Event()
    errors = []
    reads = []

    def writer(n):
        try:
            barrier.wait()
            for i in range(per_thread):
                uid = f"{n}:{i}:0:0"
                tags.add_tag(uid)
                tags.assign_target(uid, TargetType.album, n * 100 + i)
                tags.get_tag(uid).set_tag_name(f"tag {n}/{i}")
            if n < len(UIDS):
                tags.delete_tag(UIDS[n])
        except Exception as e:  # reported in the main thread
            errors.append(e)

    def reader():
        """The file on disk is always complete json (or replaced right now - then the backup is used)."""
        barrier.wait()
        while not done.is_set():
            try:
                reads.append(json.loads(Path(tags_file).read_text(encoding="utf-8")))
            except FileNotFoundError:
                pass
            except Exception as e:  # reported in the main thread
                errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(threads_count)]
    threads.append(threading.Thread(target=reader))
    for thread in threads:
        thread.start()
    for thread in threads[:-1]:
        thread.join()
    done.set()
    threads[-1].join()

    assert errors == []
    assert reads
    expected = {f"{n}:{i}:0:0": (n * 100 + i, f"tag {n}/{i}") for n in range(threads_count) for i in range(per_thread)}
    assert {key: (tag.target_id, tag.tag_name) for key, tag in tags.tags.items()} == expected
    on_disk = json.loads(Path(tags_file).read_text(encoding="utf-8"))
    assert on_disk == {key: tag.to_dict() for key, tag in tags.tags.items()}
    reloaded = Tags(tags_file, logger)
    assert {key: (tag.target_id, tag.tag_name) for key, tag in reloaded.tags.items()} == expected
    assert not Path(tags_file + ".tmp").exists()
