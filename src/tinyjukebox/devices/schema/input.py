"""Schema of the driver ``input``."""

from typing import Any

from tinyjukebox.devices.schema import adriver
from tinyjukebox.messages import Button

__all__ = ["get_schema"]


def get_schema() -> dict[str, Any]:
    driver_specific_properties = {
        "pin": {"description": "gpio pin", "type": "integer"},
        "hold_timeout": {"description": "hold time in seconds until the hold event is triggered", "type": "number"},
        "button": {"description": "which button of the box", "type": "string", "enum": [b.value for b in Button]},
    }
    schema = adriver.get_schema("input", driver_specific_properties)
    schema["driver"]["properties"]["hold_repeat"] = {
        "description": "repeat the hold event every hold_timeout seconds while the button stays pressed (optional, "
        "default: false)",
        "type": "boolean",
    }
    return schema
