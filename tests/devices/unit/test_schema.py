"""Config schemas: all test configs and the deployment config validate, errors name the wrong key."""

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

from tests.devices.support import CONFIGS, INPUT, RGBLED, load_config
from tinyjukebox.core.config import validate_config
from tinyjukebox.core.schema.microservice import get_schema as microservice_schema
from tinyjukebox.devices.drivermanager.drivermanager import DriverManager
from tinyjukebox.devices.drivers import DRIVERS
from tinyjukebox.devices.schema.drivermanager import get_driver_schema

SRC = Path(__file__).parents[3] / "src" / "tinyjukebox" / "devices"
MANAGER_CONFIGS = ["config_full.yaml", "config_drivermanager.yaml"]
DRIVER_CONFIGS = sorted(p.name for p in CONFIGS.glob("config_*.yaml") if p.name not in MANAGER_CONFIGS)


def driver_type_of(filename: str) -> str:
    return filename.removeprefix("config_").removesuffix(".yaml")


@pytest.mark.parametrize("filename", DRIVER_CONFIGS)
def test_driver_configs_validate(filename: str) -> None:
    schema = microservice_schema(get_driver_schema(driver_type_of(filename)))
    assert validate_config(load_config(filename), schema) is None


def test_every_driver_has_a_test_config() -> None:
    assert {driver_type_of(f) for f in DRIVER_CONFIGS} == set(DRIVERS)


@pytest.mark.parametrize("filename", MANAGER_CONFIGS)
def test_drivermanager_configs_validate(filename: str) -> None:
    assert validate_config(load_config(filename), DriverManager.get_schema()) is None


def manager_config(*drivers: dict[str, Any]) -> dict[str, Any]:
    return {
        "logger": {"log-level": "INFO", "log-file": "/tmp/devices.log"},
        "drivers": [{**driver, "active": True} for driver in drivers],
    }


def test_error_names_the_wrong_key() -> None:
    led = copy.deepcopy(RGBLED)
    led["pin-gren"] = led.pop("pin-green")
    error = validate_config(manager_config(led), DriverManager.get_schema())
    assert error is not None
    assert "pin-green" in error or "pin-gren" in error
    assert "drivers/0" in error


def test_unknown_keys_and_types_are_rejected() -> None:
    schema = DriverManager.get_schema()
    assert validate_config(manager_config({**RGBLED, "colour": "RED"}), schema) is not None
    assert validate_config(manager_config({**RGBLED, "type": "laser"}), schema) is not None
    assert validate_config(manager_config({**INPUT, "button": "stop"}), schema) is not None
    assert validate_config(manager_config({**RGBLED, "topics-sub": {"command": "/x"}}), schema) is not None


def test_name_and_active_are_required_in_drivermanager() -> None:
    schema = DriverManager.get_schema()
    config = manager_config(RGBLED)
    del config["drivers"][0]["active"]
    assert "active" in str(validate_config(config, schema))
    config = manager_config(RGBLED)
    del config["drivers"][0]["name"]
    assert "name" in str(validate_config(config, schema))


def test_optional_rfid_entries() -> None:
    schema = microservice_schema(get_driver_schema("rfid"))
    config = load_config("config_rfid.yaml")
    del config["driver"]["command"]
    assert validate_config(config, schema) is None
    config["driver"]["command"] = []
    assert validate_config(config, schema) is not None
    del config["driver"]["command"]
    del config["driver"]["pins"]["pin_cs"]
    assert validate_config(config, schema) is not None
    config["driver"]["pins"]["pin_cs"] = 8
    config["driver"]["poll-interval"] = 0.5  # of the former driver (pi-rc522)
    assert validate_config(config, schema) is not None


@pytest.mark.parametrize("driver_type", sorted(DRIVERS))
def test_registry_matches_modules(driver_type: str) -> None:
    """Each registered type has a driver module defining the class (checked via AST: the driver modules cannot be
    imported without their hardware libraries)."""
    tree = ast.parse((SRC / "drivers" / f"{driver_type}.py").read_text())
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert DRIVERS[driver_type] in classes
    assert get_driver_schema(driver_type)["driver"]["properties"]["type"]["enum"] == [driver_type]
