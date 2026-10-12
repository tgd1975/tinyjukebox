"""Schema builder for the config section ``driver`` - used by the schema modules of all drivers."""

from collections.abc import Mapping
from typing import Any

__all__ = ["add_schema_part", "get_schema"]


def get_schema(driver_type: str, driver_specific_properties: Mapping[str, Any]) -> dict[str, Any]:
    """Schema ``{"driver": {...}}`` of a driver config section.

    All driver specific properties are required. Unknown keys are rejected. Topics and payloads are no config
    entries - they are defined in :mod:`tinyjukebox.messages`.
    """
    driver: dict[str, Any] = {
        "description": f"Driver configuration '{driver_type}'.",
        "type": "object",
        "properties": {
            "name": {"description": "name of driver", "type": "string"},
            "type": {"description": "type of driver", "type": "string", "enum": [driver_type]},
        },
        "required": ["type"],
        "additionalProperties": False,
    }
    schema = {"driver": driver}
    add_schema_part(schema, {"properties": driver_specific_properties, "required": list(driver_specific_properties)})
    return schema


def add_schema_part(schema: dict[str, Any], schema_part: Mapping[str, Any]) -> None:
    """Add the ``properties`` and ``required`` entries of ``schema_part`` to ``schema["driver"]``."""
    schema["driver"]["properties"].update(schema_part["properties"])
    schema["driver"]["required"].extend(schema_part["required"])
