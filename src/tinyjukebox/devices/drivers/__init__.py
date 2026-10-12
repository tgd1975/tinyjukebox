"""All drivers - registry of driver types.

Driver modules import their hardware libraries at module level. They are therefore only imported on demand
(:func:`get_driver_class`), so tinyjukebox stays importable on machines without these libraries.
Convention: type ``foo`` → driver module ``tinyjukebox.devices.drivers.foo``, schema module
``tinyjukebox.devices.schema.foo``.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyjukebox.devices.baseclasses.adriver import ADriver

__all__ = ["DRIVERS", "get_driver_class"]

DRIVERS: dict[str, str] = {"input": "Input", "rfid": "RFID", "rgbled": "RGBLed", "bluetooth": "Bluetooth"}
"""driver type (value of ``type`` in the config, lower case) → class name"""


def get_driver_class(driver_type: str) -> type["ADriver"]:
    """Import and return the driver class for ``driver_type`` (case insensitive).

    :raises ValueError: for unknown driver types.
    """
    key = driver_type.lower()
    if key not in DRIVERS:
        raise ValueError(f"unknown driver type '{driver_type}'.")
    module = importlib.import_module(f"{__name__}.{key}")
    driver_class: type[ADriver] = getattr(module, DRIVERS[key])
    return driver_class
