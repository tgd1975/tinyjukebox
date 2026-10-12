"""Schema of the driver ``bluetooth``."""

from typing import Any

from tinyjukebox.devices.schema import adriver

__all__ = ["AVRCP_MODES", "get_schema"]

AVRCP_MODES = ["player", "input"]


def get_schema() -> dict[str, Any]:
    schema = adriver.get_schema("bluetooth", {})
    schema["driver"]["properties"].update(
        {
            "adapter": {"description": "bluetooth adapter (optional, default: hci0)", "type": "string"},
            "avrcp": {
                "description": "how the buttons of the headset arrive (optional, default: player) - player: BlueZ "
                "calls the media player the box registers; input: BlueZ passes them to the linux input device "
                "'<headset> (AVRCP)' (the user needs the group input)",
                "type": "string",
                "enum": AVRCP_MODES,
            },
            "play-pause-toggle": {
                "description": "play and pause of the headset both switch between play and pause (optional, default: "
                "false) - for headsets that send the same button for both",
                "type": "boolean",
            },
        }
    )
    return schema
