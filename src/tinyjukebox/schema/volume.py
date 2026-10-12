"""Schema of the volume json file (start value and maximum changed at runtime)."""

from typing import Any

__all__ = ["schema"]

_PERCENT = {"type": "number", "minimum": 0, "maximum": 100}
_LIMITS = {
    "type": "object",
    "required": ["default-volume", "max-volume"],
    "additionalProperties": False,
    "properties": {
        "default-volume": dict(_PERCENT, description="volume on startup / when the headset connects in %"),
        "max-volume": dict(_PERCENT, description="maximum volume in %"),
    },
}

schema: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "speaker": dict(_LIMITS, description="volume of the speaker - overrides tinyjukebox/volume of the config"),
        "headphones": dict(
            _LIMITS, description="volume of the headset - overrides tinyjukebox/bluetooth of the config"
        ),
    },
}
