"""create_target_factory (new targets, checked) and load_target_factory (targets from targets.json)."""

import shutil

import pytest

from tests.support import DATA, album_dict, noop
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.data.targets import create_target_factory, load_target_factory

FACTORIES = [create_target_factory, load_target_factory]


@pytest.fixture
def root(tmp_path):
    shutil.copytree(DATA / "targets" / "albums" / "simple_album_a", tmp_path / "albums" / "simple_album_a")
    return str(tmp_path / "albums")


@pytest.mark.parametrize("factory", FACTORIES)
def test_factory_success(factory, root, logger):
    json = album_dict()
    target = factory(json, root, noop, logger)
    assert isinstance(target, TargetAlbum)
    assert json["target-id"] == target.target_id
    assert target.target_type is TargetType[json["target-type"]]
    assert json["album-folder"] == target.album_folder
    assert json["playlist"] == target.playlist
    assert json["created"] == target.created


@pytest.mark.parametrize("factory", FACTORIES)
def test_factory_no_json(factory, tmp_path, logger):
    with pytest.raises(ValueError, match="target not provided"):
        factory(None, str(tmp_path), noop, logger)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("target_id", [-1, "-1"])
def test_factory_id_minus_one(factory, target_id, root, logger):
    with pytest.raises(ValueError, match="target-id must be set"):
        factory(album_dict(**{"target-id": target_id}), root, noop, logger)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("missing", ["target-id", "target-type"])
def test_factory_missing_field(factory, missing, root, logger):
    with pytest.raises(ValueError, match=f"has no attribute {missing}"):
        factory(album_dict(**{missing: None}), root, noop, logger)


@pytest.mark.parametrize("factory", FACTORIES)
def test_factory_unknown_type(factory, root, logger):
    with pytest.raises(ValueError, match="don't know what to do with target type"):
        factory(album_dict(**{"target-type": "lalelu_yaddayaddayadda"}), root, noop, logger)


def test_create_checks_the_album_but_load_does_not(tmp_path, logger):
    """A missing album must not prevent the start (load) - but it cannot be added (create)."""
    json = album_dict(**{"album-folder": "missing"})
    with pytest.raises(ValueError, match="sanity check failed"):
        create_target_factory(json, str(tmp_path), noop, logger)
    target = load_target_factory(json, str(tmp_path), noop, logger)
    assert isinstance(target, TargetAlbum)
    assert target.album_folder == "missing"
    assert not target.sanity_check()[0]
