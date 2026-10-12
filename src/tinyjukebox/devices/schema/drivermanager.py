"""Schema of the config section ``drivers`` (DriverManager)."""

import importlib
from typing import Any

from tinyjukebox.devices.drivers import DRIVERS

__all__ = ["get_driver_schema", "get_schema"]


def get_driver_schema(driver_type: str) -> dict[str, Any]:
    """Schema ``{"driver": {...}}`` of a single driver (from module ``tinyjukebox.devices.schema.<driver_type>``)."""
    module = importlib.import_module(f"tinyjukebox.devices.schema.{driver_type}")
    schema: dict[str, Any] = module.get_schema()
    return schema


def get_schema() -> dict[str, Any]:
    """Schema of the list ``drivers``: each entry is a driver section plus ``name`` (required) and ``active``.

    The entries are validated with ``if type == ... then ...`` instead of ``oneOf``; errors thus name the actual
    wrong key instead of "not valid under any of the given schemas".
    """
    conditions = []
    for driver_type in DRIVERS:
        driver = get_driver_schema(driver_type)["driver"]
        driver["properties"]["active"] = {"description": "if set to false, the driver is not loaded", "type": "boolean"}
        driver["required"].extend(["name", "active"])
        conditions.append({"if": {"properties": {"type": {"const": driver_type}}}, "then": driver})
    return {
        "drivers": {
            "description": "Drivermanager configuration.",
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"type": {"description": "type of driver", "enum": list(DRIVERS)}},
                "required": ["type"],
                "allOf": conditions,
            },
        }
    }
