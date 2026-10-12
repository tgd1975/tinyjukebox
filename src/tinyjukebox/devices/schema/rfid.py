"""Schema of the driver ``rfid``."""

from typing import Any

from tinyjukebox.devices.schema import adriver

__all__ = ["get_schema"]


def get_schema() -> dict[str, Any]:
    driver_specific_properties = {
        "pins": {
            "description": "rfid reader pins (BCM numbers)",
            "type": "object",
            "properties": {
                "pin_rst": {"description": "reset pin", "type": "integer", "minimum": 0, "maximum": 53},
                "pin_cs": {
                    "description": "chip select, e.g. 8 for SPI0 CE0",
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 53,
                },
            },
            "required": ["pin_rst", "pin_cs"],
            "additionalProperties": False,
        },
    }
    schema = adriver.get_schema("rfid", driver_specific_properties)
    schema["driver"]["properties"]["command"] = {
        "description": "command that starts rfid-reader (optional, default: sudo -n /usr/local/bin/rfid-reader); "
        "the pins are appended as arguments",
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
    }
    return schema
