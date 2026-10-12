"""Activities: the playback positions of all tags backed by the activities json file."""

import json
import shutil
import threading
from pathlib import Path

import pytest

from tests.support import DATA, file_id
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.tags import Tags

UIDS = ["1:2:3:4", "2:2:3:4", "3:2:3:4", "4:2:3:4"]


@pytest.fixture
def activities_file(tmp_path):
    return str(tmp_path / "activities.json")


@pytest.fixture
def tags(tmp_path, logger):
    """Four tags linked to the albums 123, 256, 4090 and 4090."""
    shutil.copy(DATA / "activities" / "tags.json", tmp_path / "tags.json")
    return Tags(str(tmp_path / "tags.json"), logger)


@pytest.fixture
def prepare(activities_file, tags, logger):
    """Activities of a scenario (testdata_empty_dict, testdata_one, testdata_multiple) in ``tmp_path``."""

    def _prepare(scenario):
        shutil.copy(DATA / "activities" / scenario / "activities.json", activities_file)
        return Activities(activities_file, tags, logger)

    return _prepare


@pytest.fixture
def activities(prepare):
    return prepare("testdata_multiple")


def test_init_empty_dict(prepare):
    assert len(prepare("testdata_empty_dict").activities) == 0


def test_init_one_entry(prepare):
    activities = prepare("testdata_one")
    assert len(activities.activities) == 1
    assert activities.activities["1:2:3:4"].tag_uid == "1:2:3:4"


def test_init_multiple_entries(activities):
    assert len(activities.activities) == 4
    assert list(activities.activities.keys()) == UIDS


def test_rebuild_activities(activities, activities_file):
    assert len(activities.activities) == 4
    expected = {uid: activities.activities[uid].to_dict() for uid in UIDS}
    for activity in expected.values():  # reset track info
        activity["track-id"] = 0
        activity["track-time"] = 0
        activity["album-finished"] = False

    activities.activities = {}
    assert len(activities.activities) == 0

    prev_fn = file_id(activities_file)
    activities._update_json()
    assert file_id(activities_file) != prev_fn

    prev_fn = file_id(activities_file)
    activities.rebuild_activities()
    assert file_id(activities_file) != prev_fn

    assert len(activities.activities) == 4
    for uid in UIDS:
        assert activities.activities[uid].to_dict() == expected[uid]


def test_no_file_rebuild_on_init(activities_file, tags, logger):
    activities = Activities(activities_file, tags, logger)
    assert len(activities.activities) == 4
    assert list(activities.activities.keys()) == UIDS
    assert list(json.loads(Path(activities_file).read_text(encoding="utf-8")).keys()) == UIDS


def test_broken_file_rebuild_on_init(activities_file, tags, logger):
    Path(activities_file).write_text("{broken", encoding="utf-8")
    activities = Activities(activities_file, tags, logger)
    assert list(activities.activities.keys()) == UIDS
    assert all(activity.track_id == 0 for activity in activities.activities.values())


def test_get_activity(activities):
    assert len(activities.activities) == 4

    activity = activities.get_activity("3:2:3:4")
    assert activity.tag_uid == "3:2:3:4"
    assert activity.target_type == TargetType.album
    assert activity.track_id == 89
    assert activity.track_time == 6
    assert not activity.finished

    with pytest.raises(ValueError, match="unknown tag uid"):
        activities.get_activity("_")
    with pytest.raises(ValueError, match="unknown tag uid"):
        activities.get_activity(None)


def test_add_activity_success(prepare, tags, activities_file, logger):
    tags.add_tag("5:2:3:4")
    tags.assign_target("5:2:3:4", TargetType.album, 54321)
    activities = prepare("testdata_multiple")
    assert len(activities.activities) == 4
    assert "5:2:3:4" not in activities.activities

    prev_fn = file_id(activities_file)
    added = activities.add_activity(tags.get_tag("5:2:3:4"))
    assert file_id(activities_file) != prev_fn
    assert added is activities.activities["5:2:3:4"]
    assert added.target_id == 54321

    assert "5:2:3:4" in activities.activities
    assert list(activities.activities.keys()) == [*UIDS, "5:2:3:4"]

    activities2 = Activities(activities_file, tags, logger)
    assert len(activities2.activities) == 5
    assert "5:2:3:4" in activities2.activities


def test_add_activity_duplicate_uid(activities, tags):
    assert len(activities.activities) == 4
    with pytest.raises(ValueError, match="already exists"):
        activities.add_activity(tags.get_tag("1:2:3:4"))
    with pytest.raises(ValueError, match="tag is None"):
        activities.add_activity(None)


def test_delete_activity(activities, activities_file):
    assert list(activities.activities.keys()) == UIDS

    prev_fn = file_id(activities_file)
    activities.delete_activity("1:2:3:4")
    assert file_id(activities_file) != prev_fn

    assert list(activities.activities.keys()) == UIDS[1:]

    with pytest.raises(ValueError, match="unknown tag uid"):
        activities.delete_activity("1:2:3:4")
    with pytest.raises(ValueError, match="tag is None"):
        activities.delete_activity(None)


def test_sanity_check(activities):
    assert list(activities.activities.keys()) == UIDS
    assert activities.sanity_check()

    activities.get_activity("1:2:3:4").tag_uid = "x:y:z:u"
    assert not activities.sanity_check()


def test_to_text_one(prepare):
    activities = prepare("testdata_one")
    assert len(activities.activities) == 1
    assert activities.to_text() == "key: 1:2:3:4, activity: target id: 3, track id: 1, time: 2, finished: False"


def test_to_text_none(prepare):
    activities = prepare("testdata_empty_dict")
    assert len(activities.activities) == 0
    assert activities.to_text() == ""


def test_to_text_multiple(activities):
    assert len(activities.activities) == 4
    assert activities.to_text() == (
        "key: 1:2:3:4, activity: target id: 123, track id: 1, time: 2, finished: False\n"
        "key: 2:2:3:4, activity: target id: 256, track id: 6, time: 0, finished: True\n"
        "key: 3:2:3:4, activity: target id: 4090, track id: 89, time: 6, finished: False\n"
        "key: 4:2:3:4, activity: target id: 4090, track id: 9, time: 2, finished: False"
    )


def test_all_is_a_snapshot(activities, tags):
    snapshot = activities.all()
    assert list(snapshot.keys()) == UIDS
    activities.delete_activity("1:2:3:4")
    assert list(snapshot.keys()) == UIDS
    assert list(activities.all().keys()) == UIDS[1:]


def test_activity_update_is_written(activities, activities_file, tags, logger):
    activities.get_activity("1:2:3:4").update(7, 12.5, True)
    reloaded = Activities(activities_file, tags, logger).get_activity("1:2:3:4")
    assert (reloaded.track_id, reloaded.track_time, reloaded.finished) == (7, 12.5, True)


def test_concurrent_changes_do_not_corrupt_the_file(activities, activities_file, tags, tmp_path, logger):
    """Player (position updates), rest api and cli change tags and activities at the same time - every change ends up
    in a complete file."""
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
                activity = activities.add_activity(tags.get_tag(uid))
                for position in range(3):
                    activity.update(i, position, False)
                if n < len(UIDS):
                    activities.get_activity(UIDS[n]).update(n, i, True)
            if n < len(UIDS) and n % 2:
                activities.delete_activity(UIDS[n])
        except Exception as e:  # reported in the main thread
            errors.append(e)

    def reader():
        """The file on disk is always complete json (or replaced right now - then the backup is used)."""
        barrier.wait()
        while not done.is_set():
            try:
                reads.append(json.loads(Path(activities_file).read_text(encoding="utf-8")))
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
    expected = {
        f"{n}:{i}:0:0": {"tag-uid": f"{n}:{i}:0:0", "target-id": n * 100 + i, "track-id": i, "track-time": 2,
                         "album-finished": False, "target-type": "album"}
        for n in range(threads_count)
        for i in range(per_thread)
    }  # fmt: skip
    for n in (0, 2):  # 1 and 3 were deleted
        expected[UIDS[n]] = {"tag-uid": UIDS[n], "target-id": [123, 256, 4090, 4090][n], "track-id": n,
                             "track-time": per_thread - 1, "album-finished": True, "target-type": "album"}  # fmt: skip
    assert {key: activity.to_dict() for key, activity in activities.all().items()} == expected
    assert json.loads(Path(activities_file).read_text(encoding="utf-8")) == expected
    reloaded = Activities(activities_file, tags, logger)
    assert {key: activity.to_dict() for key, activity in reloaded.all().items()} == expected
    assert reloaded.sanity_check()
    reloaded_tags = Tags(str(tmp_path / "tags.json"), logger)
    assert {key: tag.to_dict() for key, tag in reloaded_tags.tags.items()} == {
        key: tag.to_dict() for key, tag in tags.tags.items()
    }
    assert len(reloaded_tags.tags) == 4 + threads_count * per_thread
    assert not Path(activities_file + ".tmp").exists()
