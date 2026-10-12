"""Power failure while the json files are written: after a restart the last or the new state must be loaded.

JsonData writes in three steps: new content -> '<file>.tmp' (fsync), regular file -> backup file, '<file>.tmp' ->
regular file (os.replace). The power may fail at any point in between.
"""

import builtins
import json
import os

import pytest

from tests.support import TAG_ALBUM, TAG_UNMAPPED
from tinyjukebox.data import jsondata
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.tags import Tags


class PowerFailureError(Exception):
    pass


class CrashingFile:
    """File that writes only the first half of the data before the 'power fails'."""

    def __init__(self, filename, mode, **kwargs):
        self._file = builtins.open(filename, mode, **kwargs)  # noqa: SIM115 - closed on write/exit

    def write(self, data):
        self._file.write(data[: len(data) // 2])
        self._file.close()
        raise PowerFailureError()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._file.close()


class CrashingRename:
    """``os`` for jsondata: the power fails before the complete temp file is renamed to the regular file."""

    def __getattr__(self, name):
        return getattr(os, name)

    @staticmethod
    def replace(src, dst):
        if str(src).endswith(".tmp"):
            raise PowerFailureError()
        os.replace(src, dst)


@pytest.fixture
def power(monkeypatch):
    """power.fail_on_next_write() - the next write stops halfway; power.fail_before_rename() - the next write is
    complete but not renamed; power.restore() - normal writes again."""

    def crashing_open(filename, mode="r", **kwargs):
        if "w" in mode:
            return CrashingFile(filename, mode, **kwargs)
        return builtins.open(filename, mode, **kwargs)

    class Power:
        @staticmethod
        def fail_on_next_write():
            monkeypatch.setattr(jsondata, "open", crashing_open, raising=False)

        @staticmethod
        def fail_before_rename():
            monkeypatch.setattr(jsondata, "os", CrashingRename())

        @staticmethod
        def restore():
            monkeypatch.setattr(jsondata, "open", builtins.open, raising=False)
            monkeypatch.setattr(jsondata, "os", os)

    return Power


def reload_tags(library, logger):
    return Tags(library.tags_file, logger)


def load_json(filename):
    with open(filename, encoding="utf-8") as f:
        return json.load(f)


def test_crash_while_writing_tags(library, logger, power):
    power.fail_on_next_write()
    with pytest.raises(PowerFailureError):
        library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "2")
    power.restore()
    # the new content is half written to the temp file - the regular file has not been touched yet
    assert load_json(library.tags_file)[TAG_UNMAPPED]["target"]["id"] == -1
    with pytest.raises(json.JSONDecodeError):
        load_json(library.tags_file + ".tmp")
    tags = reload_tags(library, logger)
    assert tags.get_tag(TAG_UNMAPPED).target_id == -1  # previous state


def test_crash_before_rename(library, logger, power):
    power.fail_before_rename()
    with pytest.raises(PowerFailureError):
        library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "2")
    power.restore()
    assert not os.path.exists(library.tags_file)
    assert load_json(library.tags_file + ".tmp")[TAG_UNMAPPED]["target"]["id"] == "2"  # complete, not renamed
    tags = reload_tags(library, logger)
    assert tags.get_tag(TAG_UNMAPPED).target_id == -1  # previous state from the backup file
    assert TAG_UNMAPPED in load_json(library.tags_file)


def test_leftover_temp_file_does_not_matter(library, logger, power):
    power.fail_on_next_write()
    with pytest.raises(PowerFailureError):
        library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "2")
    power.restore()
    tags = reload_tags(library, logger)
    tags.assign_target(TAG_UNMAPPED, TargetType.album, "2")  # overwrites the half written temp file
    assert not os.path.exists(library.tags_file + ".tmp")
    assert str(reload_tags(library, logger).get_tag(TAG_UNMAPPED).target_id) == "2"  # the new state


def test_crash_between_backup_and_write(library, logger):
    # the regular file has already been renamed to the backup file when the power fails
    os.replace(library.tags_file, library.tags_file + "_")
    tags = reload_tags(library, logger)
    assert set(tags.tags) == set(library.tags.tags)
    assert os.path.exists(library.tags_file)


def test_empty_file_after_power_failure(library, logger):
    library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "2")  # creates the backup file
    open(library.tags_file, "w").close()  # file system lost the content
    tags = reload_tags(library, logger)
    assert tags.get_tag(TAG_UNMAPPED).target_id == -1


def test_crash_while_storing_activity(library, logger, power):
    library.activities.get_activity(TAG_ALBUM).update(1, 0.5, False)
    power.fail_on_next_write()
    with pytest.raises(PowerFailureError):
        library.activities.get_activity(TAG_ALBUM).update(2, 0.7, False)
    power.restore()
    activities = Activities(library.activities_file, library.tags, logger)
    activity = activities.get_activity(TAG_ALBUM)
    assert (activity.track_id, activity.track_time) == (1, 0.5)


def test_activities_lost_completely(library, logger):
    library.activities.get_activity(TAG_ALBUM).update(1, 0.5, False)
    for filename in [library.activities_file, library.activities_file + "_"]:
        with open(filename, "w") as f:
            f.write("{broken")
    # positions are lost but the box starts - activities are rebuilt from the tags
    activities = Activities(library.activities_file, library.tags, logger)
    activity = activities.get_activity(TAG_ALBUM)
    assert (activity.track_id, activity.track_time, activity.finished) == (0, 0, False)
    assert TAG_UNMAPPED not in activities.activities


def test_tags_lost_completely(library, logger):
    for filename in [library.tags_file, library.tags_file + "_"]:
        with open(filename, "w") as f:
            f.write("{broken")
    # without tags the links between tags and albums would be lost silently - refuse to start instead
    with pytest.raises(FileNotFoundError):
        reload_tags(library, logger)
    with open(library.tags_file) as f:
        assert f.read() == "{broken"  # nothing has been overwritten


@pytest.mark.parametrize("crash", ["fail_on_next_write", "fail_before_rename"])
@pytest.mark.parametrize("successful_writes", [0, 1, 2, 5])
def test_crash_after_several_updates(library, logger, power, successful_writes, crash):
    """After a restart the state is always the one before the interrupted write."""
    activity = library.activities.get_activity(TAG_ALBUM)
    for n in range(1, successful_writes + 1):
        activity.update(n, n / 10, False)
    getattr(power, crash)()
    with pytest.raises(PowerFailureError):
        activity.update(99, 9.9, False)
    power.restore()
    reloaded = Activities(library.activities_file, library.tags, logger).get_activity(TAG_ALBUM)
    assert (reloaded.track_id, reloaded.track_time) == (successful_writes, successful_writes / 10)
