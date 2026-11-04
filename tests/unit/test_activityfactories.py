"""create_activity_factory (new activity for a tag) and load_activity_factory (activity from the json file)."""

import pytest

from tests.support import noop
from tinyjukebox.data.activities import create_activity_factory, load_activity_factory
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.tag import Tag


def test_create_activity_factory_success(logger):
    tag = Tag.create_tag(logger, "abc", noop)
    tag.set_target_id(3)
    activity = create_activity_factory(tag, noop, logger)
    assert isinstance(activity, ActivityAlbum)
    assert activity.target_id == 3
    assert activity.tag_uid == "abc"
    assert activity.track_id == 0
    assert activity.track_time == 0


def test_create_activity_factory_no_tag(logger):
    with pytest.raises(ValueError, match="tag not provided"):
        create_activity_factory(None, noop, logger)


def test_create_activity_factory_no_target_id(logger):
    tag = Tag.create_tag(logger, "abc", noop)
    with pytest.raises(ValueError, match="target id of tag must be set"):
        create_activity_factory(tag, noop, logger)


def test_create_activity_factory_not_a_tag(logger):
    with pytest.raises(ValueError, match="tag not provided or not a tag"):
        create_activity_factory({}, noop, logger)


def test_create_activity_factory_unknown_target_type(logger):
    tag = Tag.create_tag(logger, "abc", noop)
    tag.set_target_id(3)
    tag.target_type = None
    with pytest.raises(ValueError, match="unknown target type"):
        create_activity_factory(tag, noop, logger)


def test_load_activity_factory_success(logger):
    activity = load_activity_factory(
        {"tag-uid": "1:2:3:4", "target-id": 3, "track-id": 1, "track-time": 2,
         "target-type": TargetType.album.name, "album-finished": False},
        noop, logger,
    )  # fmt: skip
    assert isinstance(activity, ActivityAlbum)
    assert activity.tag_uid == "1:2:3:4"
    assert activity.target_id == 3
    assert activity.track_id == 1
    assert activity.track_time == 2
    assert activity.finished is False


@pytest.mark.parametrize("activity", [None, {}])
def test_load_activity_factory_no_dict(logger, activity):
    with pytest.raises(ValueError, match="expected dict with key 'target-type'"):
        load_activity_factory(activity, noop, logger)


def test_load_activity_factory_wrong_type(logger):
    with pytest.raises(ValueError, match="unknown target type"):
        load_activity_factory(
            {"tag-uid": "1:2:3:4", "target-id": 3, "track-id": 1, "track-time": 2,
             "target-type": "_lalala_", "album-finished": False},
            noop, logger,
        )  # fmt: skip
