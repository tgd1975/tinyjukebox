"""Schema of the driver ``rfid``."""

from typing import Any

from tinyjukebox.devices.schema import adriver

__all__ = ["SPI_PINS", "get_schema"]

SPI_PINS = (9, 10, 11)
"""MISO, MOSI and SCLK of SPI0 - rfid-reader would make them outputs, and SPI would not work until the next boot"""


def _pin(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "type": "integer",
        "minimum": 0,
        "maximum": 53,
        "not": {"enum": list(SPI_PINS)},
    }


def get_schema() -> dict[str, Any]:
    driver_specific_properties = {
        "pins": {
            "description": "rfid reader pins (BCM numbers); two different ones, none of 9, 10, 11 (SPI0)",
            "type": "object",
            "properties": {
                "pin_rst": _pin("reset pin"),
                "pin_cs": _pin("chip select - rfid-reader drives it itself, any free pin; e.g. 8 for SPI0 CE0"),
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
