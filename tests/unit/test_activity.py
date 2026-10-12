"""ActivityAlbum: playback position of a tag's album - parsing, creation, updates and sanity check."""

import pytest

from tests.support import CallRecorder, noop
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.tag import Tag


def params(**overrides):
    activity = {"tag-uid": "1:2:3:4", "target-id": 3, "track-id": 1, "track-time": 2,
                "target-type": TargetType.album.name, "album-finished": False}  # fmt: skip
    activity.update(overrides)
    return activity


def test_init(logger):
    activity = ActivityAlbum(params(), noop, logger)
    assert activity.tag_uid == "1:2:3:4"
    assert activity.target_id == 3
    assert activity.track_id == 1
    assert activity.track_time == 2
    assert activity.finished is False


@pytest.mark.parametrize(
    "activity",
    [
        None,
        {},
        {"target-id": 3},
        {"target-id": 3, "track-id": 1},
        {"target-id": 3, "track-id": 1, "track-time": 0},
        {"tag-uid": "1:2:3:4", "target-id": 3, "track-id": 1, "track-time": 0},  # album-finished missing
    ],
)
def test_init_incomplete(logger, activity):
    with pytest.raises(ValueError, match="activity m"):
        ActivityAlbum(activity, noop, logger)


def test_create(logger):
    tag = Tag.create_tag(logger, "abc", noop)
    tag.set_target_id(3)
    activity = ActivityAlbum.create_activity(tag, noop, logger)
    assert activity.tag_uid == "abc"
    assert activity.target_id == 3
    assert activity.track_id == 0
    assert activity.track_time == 0
    assert activity.finished is False


@pytest.mark.parametrize("tag", [None, "tag"])
def test_create_fails_type(logger, tag):
    with pytest.raises(ValueError, match="tag must be linked to a target"):
        ActivityAlbum.create_activity(tag, noop, logger)


def test_create_unlinked_tag(logger):
    tag = Tag.create_tag(logger, "abc", noop)
    with pytest.raises(ValueError, match="tag must be linked to a target"):
        ActivityAlbum.create_activity(tag, noop, logger)


def test_to_dict(logger):
    assert ActivityAlbum(params(), noop, logger).to_dict() == params()


def test_to_text(logger):
    activity = ActivityAlbum(params(**{"target-id": 33333, "track-id": 12345, "track-time": 67890}), noop, logger)
    text = activity.to_text()
    assert "1:2:3:4" not in text
    assert "33333" in text
    assert "12345" in text
    assert "67890" in text
    assert "False" in text


def test_update(logger):
    calls = CallRecorder()
    activity = ActivityAlbum(params(), calls, logger)

    activity.update(2, 2, False)
    assert calls.count == 1
    assert (activity.track_id, activity.track_time, activity.finished) == (2, 2, False)

    activity.update(2, 3, False)
    assert calls.count == 2
    assert (activity.track_id, activity.track_time, activity.finished) == (2, 3, False)

    activity.update(2, 3, True)
    assert calls.count == 3
    assert (activity.track_id, activity.track_time, activity.finished) == (2, 3, True)

    activity.update(5, 6, False)
    assert calls.count == 4
    assert (activity.track_id, activity.track_time, activity.finished) == (5, 6, False)


def test_update_unchanged_is_skipped(logger):
    calls = CallRecorder()
    activity = ActivityAlbum(params(), calls, logger)
    activity.update(1, 2, False)
    assert calls.count == 0


def test_sanity_check(logger):
    assert ActivityAlbum(params(), noop, logger).sanity_check()


@pytest.mark.parametrize(("attribute", "value"), [("track_time", -1), ("track_id", -1), ("finished", "finished")])
def test_sanity_check_fails(logger, attribute, value):
    activity = ActivityAlbum(params(), noop, logger)
    assert activity.sanity_check()
    setattr(activity, attribute, value)
    assert not activity.sanity_check()


def test_update_json_is_required(logger):
    with pytest.raises(ValueError, match="update_json method not provided"):
        ActivityAlbum(params(), None, logger)
