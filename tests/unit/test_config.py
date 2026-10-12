"""Config files and the config schema of the TinyJukebox service."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support import DATA, box_config
from tinyjukebox.core.config import load_yaml, lowercase_keys, validate_config
from tinyjukebox.messages import Button
from tinyjukebox.tinyjukebox import TinyJukebox

REPO_ROOT = Path(__file__).parents[2]
DEPLOYMENT_CONFIG_FOLDER = REPO_ROOT / "deploy" / "config"


def read(filename):
    """The config like read_config reads it (lower case keys)."""
    return lowercase_keys(load_yaml(filename))


@pytest.fixture(scope="module")
def schema():
    return TinyJukebox.get_schema()


@pytest.mark.parametrize("filename", [DATA / "config.yaml", REPO_ROOT / "docs" / "config.example.yaml"],
                         ids=["tests-data", "docs-example"])  # fmt: skip
def test_config_files_are_valid(schema, filename):
    assert validate_config(read(filename), schema) is None


@pytest.mark.parametrize("name", ["tinyjukebox.yaml", "tinyjukebox-sim.yaml"])
def test_deployment_configs_are_valid(schema, name):
    filename = DEPLOYMENT_CONFIG_FOLDER / name
    assert validate_config(read(filename), schema) is None


def test_box_config_has_all_drivers_of_the_box():
    """One input per button of the box, the rfid reader, the led and bluetooth - each exactly once, no other
    drivers. Play and pause are buttons of the headset only."""
    drivers = read(DEPLOYMENT_CONFIG_FOLDER / "tinyjukebox.yaml")["drivers"]
    types = [driver["type"] for driver in drivers]
    box_buttons = sorted(set(Button) - {Button.PLAY, Button.PAUSE})
    assert sorted(driver["button"] for driver in drivers if driver["type"] == "input") == box_buttons
    assert types.count("rfid") == types.count("rgbled") == types.count("bluetooth") == 1
    assert len(types) == len(box_buttons) + 3


def test_dumped_schema_is_up_to_date(schema):
    """docs/config_schema.json is generated with TinyJukebox.dump_schema - it must never drift from the code."""
    dumped = json.loads((REPO_ROOT / "docs" / "config_schema.json").read_text(encoding="utf-8"))
    assert dumped == schema


@pytest.fixture
def config(tmp_path):
    """Complete config of the unit tests (only the file names matter for the validation)."""
    library = SimpleNamespace(folder=tmp_path, albums_folder=str(tmp_path / "albums"),
                              tags_file=str(tmp_path / "tags.json"), targets_file=str(tmp_path / "targets.json"),
                              activities_file=str(tmp_path / "activity.json"))  # fmt: skip
    return box_config(library)


def test_box_config_is_valid(schema, config):
    assert validate_config(config, schema) is None


def _change(config, path, value):
    node = config
    keys = path.split("/")
    for key in keys[:-1]:
        node = node[key]
    if value is None:
        del node[keys[-1]]
    else:
        node[keys[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("tinyjukebox/unknown-key", 1),
        ("tinyjukebox/volume/unknown-key", 1),
        ("tinyjukebox/topics-pub", {"rgbled": "/a/rgbled"}),  # topics are no config entries any more
        ("tinyjukebox/messages", {"volume-up": "UP"}),
        ("tinyjukebox/albums-folder", None),
        ("tinyjukebox/playlist-filename", None),
        ("tinyjukebox/json/tags", None),
        ("tinyjukebox/volume/max-volume", None),
        ("tinyjukebox/volume/max-volume", "loud"),
        ("tinyjukebox/albumplayer/update-schedule", 0),
        ("tinyjukebox/rest-api", {"port": 70000}),
        ("tinyjukebox/rest-api", {"unknown": True}),
    ],
)
def test_invalid_config_is_rejected(schema, config, path, value):
    _change(config, path, value)
    assert validate_config(config, schema) is not None


@pytest.mark.parametrize(
    "path",
    [
        "tinyjukebox/volume/mixer-control",
        "tinyjukebox/rest-api",
    ],
)
def test_optional_keys_may_be_omitted_or_set(schema, config, path):
    section = config
    keys = path.split("/")
    for key in keys[:-1]:
        section = section[key]
    with_key = copy.deepcopy(config)
    _change(with_key, path, {} if keys[-1] == "rest-api" else "x")
    assert validate_config(with_key, schema) is None
    section.pop(keys[-1], None)
    assert validate_config(config, schema) is None
