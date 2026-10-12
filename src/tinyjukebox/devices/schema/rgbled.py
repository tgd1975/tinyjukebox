"""Schema of the driver ``rgbled``."""

from typing import Any

from tinyjukebox.devices.schema import adriver
from tinyjukebox.messages import Color

__all__ = ["COLOR_NAMES", "get_schema"]

COLOR_NAMES = [color.name for color in Color]

PHYSICAL_CLOSED = {
    "description": "low/high - gpio level that switches the color on",
    "type": "string",
    "enum": ["low", "high"],
}


def get_schema() -> dict[str, Any]:
    driver_specific_properties: dict[str, Any] = {
        "initial-color": {"description": "color at startup", "type": "string", "enum": COLOR_NAMES},
    }
    for color in ("red", "green", "blue"):
        driver_specific_properties[f"pin-{color}"] = {"description": f"gpio pin {color}", "type": "integer"}
        driver_specific_properties[f"physical-closed-{color}"] = PHYSICAL_CLOSED
    return adriver.get_schema("rgbled", driver_specific_properties)
