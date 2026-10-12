"""Test doubles and helpers shared by unit and integration tests."""

import sys
from pathlib import Path
from typing import Any

from tinyjukebox.core.config import read_config

CONFIGS = Path(__file__).parent / "configs"


def load_config(name: str) -> dict[str, Any]:
    """Read ``tests/configs/<name>``."""
    return read_config(CONFIGS / name)


def driver_config(driver: dict[str, Any], log_file: str) -> dict[str, Any]:
    """Complete config for a standalone driver."""
    return {"logger": {"log-level": "DEBUG", "log-file": log_file}, "driver": driver}


INPUT: dict[str, Any] = {
    "name": "button",
    "type": "input",
    "pin": 21,
    "hold_timeout": 0.05,
    "button": "volume-up",
}

RGBLED: dict[str, Any] = {
    "name": "led",
    "type": "rgbled",
    "pin-red": 22,
    "pin-green": 27,
    "pin-blue": 17,
    "initial-color": "RED",
    "physical-closed-red": "low",
    "physical-closed-green": "high",
    "physical-closed-blue": "high",
}

BLUETOOTH: dict[str, Any] = {"name": "bluetooth", "type": "bluetooth"}

RFID: dict[str, Any] = {
    "name": "rfid",
    "type": "rfid",
    "pins": {"pin_rst": 26, "pin_cs": 8},
    # a reader that never reports a tag - tests that need tags use the fixture rfid_reader
    "command": [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(3600)"],
}
