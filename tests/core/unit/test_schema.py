import json
from pathlib import Path
from typing import Any

import pytest

from tests.core.support import EXAMPLE_SCHEMA, service_config
from tinyjukebox.core.config import validate_config
from tinyjukebox.core.schema.microservice import dump_schema, get_schema


@pytest.fixture
def schema() -> dict[str, Any]:
    return get_schema(EXAMPLE_SCHEMA, {"topic": {"type": "string"}})


def test_structure(schema: dict[str, Any]) -> None:
    assert schema["required"] == ["test", "logger"]
    assert set(schema["properties"]) == {"test", "logger"}
    assert schema["definitions"] == {"topic": {"type": "string"}}


def test_valid_configs(schema: dict[str, Any]) -> None:
    assert validate_config(service_config("x.log"), schema) is None
    config = service_config("x.log")
    config["logger"]["log-rotation"] = {"maxbytes": 1024, "backupcount": 3}
    assert validate_config(config, schema) is None


@pytest.mark.parametrize(
    ("section", "change", "message"),
    [
        ("logger", {"log-level": "VERBOSE", "log-file": "x"}, "is not one of"),
        ("logger", {"log-level": "INFO"}, "'log-file' is a required property"),
        ("test", {"start": "x"}, "'stop' is a required property"),
    ],
)
def test_invalid_configs(schema: dict[str, Any], section: str, change: dict[str, Any], message: str) -> None:
    config = service_config("x.log")
    config[section] = change
    result = validate_config(config, schema)
    assert result is not None
    assert message in result


def test_dump_schema(tmp_path: Path, schema: dict[str, Any]) -> None:
    target = tmp_path / "schema.json"
    assert dump_schema(schema, target) is True
    assert json.loads(target.read_text()) == schema
    assert dump_schema(schema, target) is False
