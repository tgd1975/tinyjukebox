from pathlib import Path
from typing import Any, ClassVar

import pytest

from tinyjukebox.core.config import (
    load_yaml,
    lowercase_keys,
    mask_entries,
    read_config,
    validate_config,
)


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadYaml:
    def test_on_off_yes_no_stay_strings(self, tmp_path: Path) -> None:
        data = load_yaml(write(tmp_path / "c.yaml", "a: on\nb: Off\nc: yes\nd: NO\ne: true\nf: False\n"))
        assert data == {"a": "on", "b": "Off", "c": "yes", "d": "NO", "e": True, "f": False}

    def test_safe_loader_rejects_python_objects(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="python/object"):
            load_yaml(write(tmp_path / "c.yaml", "a: !!python/object/apply:os.system ['true']\n"))

    def test_global_pyyaml_state_is_untouched(self) -> None:
        import yaml

        assert yaml.safe_load("a: on") == {"a": True}


class TestLowercaseKeys:
    def test_nested(self) -> None:
        data = {"A": {"B": [{"C": 1}, "X"]}, 1: "one"}
        assert lowercase_keys(data) == {"a": {"b": [{"c": 1}, "X"]}, 1: "one"}

    def test_returns_copy(self) -> None:
        data: dict[str, Any] = {"a": {"b": 1}}
        result = lowercase_keys(data)
        result["a"]["b"] = 2
        assert data["a"]["b"] == 1


class TestReadConfig:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_config(tmp_path / "missing.yaml")

    def test_root_must_be_mapping(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="mapping"):
            read_config(write(tmp_path / "c.yaml", "- a\n- b\n"))


class TestValidateConfig:
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"b": {"type": "integer"}}}},
    }

    def test_valid(self) -> None:
        assert validate_config({"a": {"b": 1}}, self.schema) is None

    def test_invalid_reports_path(self) -> None:
        assert validate_config({"a": {"b": "x"}}, self.schema) == "'x' is not of type 'integer' (at 'a/b')"

    def test_invalid_root(self) -> None:
        assert validate_config([], self.schema) == "[] is not of type 'object' (at '<root>')"


def test_mask_entries() -> None:
    config: dict[str, Any] = {
        "api-user": "u",
        "api-password": "p",
        "credentials-file": "f",
        "port": 1883,
        "user-id": 5,  # not a string - kept
        "nested": [{"password": "p"}, {"other": "o"}],
    }
    mask_entries(config)
    assert config == {
        "api-user": "*****",
        "api-password": "*****",
        "credentials-file": "*****",
        "port": 1883,
        "user-id": 5,
        "nested": [{"password": "*****"}, {"other": "o"}],
    }


def test_mask_entries_custom_patterns() -> None:
    config = {"token": "t", "user": "u"}
    mask_entries(config, patterns=["token"], mask_string="#")
    assert config == {"token": "#", "user": "u"}
