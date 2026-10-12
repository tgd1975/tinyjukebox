"""Tag: an entry of tags.json - parsing, serialisation, changes and sanity check."""

import copy
from typing import Any, cast

import pytest

from tests.support import CallRecorder, noop
from tinyjukebox.data.common import TargetType, now
from tinyjukebox.data.tag import Tag


@pytest.fixture
def calls():
    return CallRecorder()


@pytest.fixture
def tag_dict():
    return {"tag": {"uid": "uid", "name": "name"}, "target": {"type": "album", "id": 1}, "created": now()}


@pytest.mark.parametrize("tag", [{}, {"tag": {"uid": ""}}, {"tag": {"uid": None}}])
def test_init_without_uid_fails(logger, tag):
    with pytest.raises(ValueError, match="uid must be a non empty string"):
        Tag(logger, tag, noop)


def test_init_uid_only(logger):
    tag = Tag(logger, {"tag": {"uid": "uid"}}, noop)
    assert tag.tag_name == ""
    assert tag.tag_uid == "uid"
    assert tag.target_id == -1
    assert tag.target_type is TargetType.album
    assert tag.created is not None
    assert tag._update_json == noop


def test_init_filled(logger, tag_dict):
    tag = Tag(logger, tag_dict, noop)
    assert tag.tag_name == tag_dict["tag"]["name"]
    assert tag.tag_uid == tag_dict["tag"]["uid"]
    assert tag.target_id == tag_dict["target"]["id"]
    assert tag.target_type is TargetType[tag_dict["target"]["type"]]
    assert tag.created == tag_dict["created"]
    assert tag._update_json == noop


def test_create_tag(logger):
    tag = Tag.create_tag(logger, "testuid", noop)
    assert tag.tag_name == ""
    assert tag.tag_uid == "testuid"
    assert tag.target_id == -1
    assert tag.target_type is TargetType.album
    assert tag.created is not None
    assert tag._update_json == noop


def test_to_dict(logger, tag_dict):
    expected = copy.deepcopy(tag_dict)
    assert Tag(logger, tag_dict, noop).to_dict() == expected


def test_set_target_id(logger, tag_dict, calls):
    expected = copy.deepcopy(tag_dict)
    tag = Tag(logger, tag_dict, calls)
    assert tag.to_dict() == expected
    tag.set_target_id(2)
    assert calls.count == 1
    expected["target"]["id"] = 2
    assert tag.to_dict() == expected


def test_set_tag_name(logger, tag_dict, calls):
    expected = copy.deepcopy(tag_dict)
    tag = Tag(logger, tag_dict, calls)
    assert tag.to_dict() == expected
    tag.set_tag_name("name2")
    assert calls.count == 1
    expected["tag"]["name"] = "name2"
    assert tag.to_dict() == expected


def test_set_target_type(logger, tag_dict, calls):
    tag = Tag(logger, tag_dict, calls)
    with pytest.raises(ValueError, match="must be a TargetType"):
        tag.set_target_type(cast(Any, "test"))
    assert calls.count == 0
    tag.set_target_type(TargetType.album)
    assert tag.target_type is TargetType.album
    assert calls.count == 1


def test_sanity_check(logger):
    tag = Tag.create_tag(logger, "uid", noop)
    assert tag.sanity_check()

    tag.tag_uid = None
    assert not tag.sanity_check()
    tag.tag_uid = ""
    assert not tag.sanity_check()
    tag.tag_uid = "uid"
    assert tag.sanity_check()

    temp = tag.created
    tag.created = None
    assert not tag.sanity_check()
    tag.created = ""
    assert not tag.sanity_check()
    tag.created = "12:13:14 2020-12-10"
    assert not tag.sanity_check()
    tag.created = temp
    assert tag.sanity_check()


def test_to_text(logger, tag_dict):
    tag_dict["created"] = "2020-11-16T20:59:07.323370Z"
    tag = Tag(logger, tag_dict, noop)
    assert tag.tag_name == tag_dict["tag"]["name"]
    assert tag.tag_uid == tag_dict["tag"]["uid"]
    assert tag.target_id == tag_dict["target"]["id"]
    assert tag.target_type is TargetType[tag_dict["target"]["type"]]
    assert tag.created == tag_dict["created"]
    assert tag._update_json == noop
    assert tag.to_text() == "tag: uid/'name' -> target: album/1. created: 2020-11-16T20:59:07.323370Z"
