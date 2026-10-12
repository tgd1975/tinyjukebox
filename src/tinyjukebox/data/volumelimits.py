"""Start value and maximum of the volumes, changed at runtime (``volume.json`` next to ``tags.json``)."""

import logging
import os
import threading
from collections.abc import Mapping
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import Listener
from tinyjukebox.data.jsondata import JsonData
from tinyjukebox.schema.volume import schema

__all__ = ["VolumeLimits"]


class VolumeLimits:
    """``{"speaker": {"default-volume": 50, "max-volume": 80}, "headphones": {...}}`` - a profile that has never been
    changed is missing: the config applies to it. A missing or broken file is not fatal (the config applies)."""

    def __init__(self, filename: str, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.Lock()
        self._listener: Listener | None = None
        backup = filename + "_"
        if not os.path.exists(filename) and not os.path.exists(backup):
            self._logger.info("__init__ - no volume file yet - the config applies until a limit is changed.")
            self._create(filename)
        try:
            self._json = JsonData(filename, backup, schema, self._logger)
        except FileNotFoundError:
            self._logger.warning("__init__ - no usable volume file - the config applies.")
            self._create(filename)
            self._json = JsonData(filename, backup, schema, self._logger)

    @staticmethod
    def _create(filename: str) -> None:
        with open(filename, "w", encoding="utf-8") as outfile:
            outfile.write("{}")

    def apply(self, profile: str, config: Mapping[str, Any]) -> dict[str, Any]:
        """``config`` (keys ``default-volume``, ``max-volume``, ...) with the stored limits of ``profile``."""
        with self._lock:
            return {**config, **self._json.data.get(profile, {})}

    def store(self, profile: str, default_volume: float, max_volume: float) -> None:
        with self._lock:
            data = dict(self._json.data)
            data[profile] = {"default-volume": default_volume, "max-volume": max_volume}
            self._json.store(data)
            if self._listener is not None:
                self._listener(profile, data[profile])

    def listen(self, listener: Listener) -> None:
        """``listener(profile, limits)`` is called whenever the limits of a profile are changed."""
        self._listener = listener

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._json.data)

    def sync(self, data: dict[str, Any]) -> bool:
        """Make the stored limits ``data``; they apply from the next start. The listener is not called."""
        with self._lock:
            if data == self._json.data:
                return False
            self._json.store(dict(data))
            return True
