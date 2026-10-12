"""JSON file with schema validation and a backup copy of the previous version."""

import contextlib
import json
import logging
import os
import threading
from typing import Any

import jsonschema

from tinyjukebox.core.logging import get_child

__all__ = ["JsonData"]


class JsonData:
    """Content of a JSON file (``data``), validated against ``schema``.

    Every write moves the current file to ``backup_filename`` first. If the regular file is missing, empty or
    invalid on start, the backup is used and written back to the regular file. Without a usable file and backup,
    ``FileNotFoundError`` is raised.

    ``data`` may be changed by the owner; :meth:`store_data` writes it (only if it changed). :meth:`store` replaces
    and writes the data in one step - the collections use it, so that concurrent writers never see half built data.
    """

    def __init__(self, filename: str, backup_filename: str, schema: dict[str, Any], logger: logging.Logger) -> None:
        self._logger = get_child(logger, f"{type(self).__name__}.{filename}")
        self.filename = filename
        self.backup_filename = backup_filename
        self._schema = schema
        self._lock = threading.RLock()
        self.last_string: str | None = None

        with self._lock:
            data = self._read_data(self.filename)
            if data is None:
                self._logger.error("failed to load regular file - trying the backup file instead.")
                data = self._read_data(self.backup_filename)
                if data is None:
                    raise FileNotFoundError(f"neither '{filename}' nor its backup '{backup_filename}' can be loaded.")
                self.data: dict[str, Any] = data
                self._logger.info("rewriting regular file with the content of the backup file.")
                self.store_data(force_write=True, skip_backup=True)
            else:
                self.data = data
                self.last_string = json.dumps(self.data, indent=4)
        self._logger.debug("__init__ - %s", self.data)

    def _read_data(self, filename: str) -> dict[str, Any] | None:
        self._logger.info("_read_data - from file %s", filename)
        try:
            with open(filename, encoding="utf-8") as infile:
                data = json.load(infile)
        except FileNotFoundError:
            self._logger.error("_read_data - file %s not found.", filename)
            return None
        except json.JSONDecodeError as e:
            self._logger.error("_read_data - file %s is not valid json: %s", filename, e)
            return None
        if not self._validate_data(data):
            self._logger.error("_read_data - content of file %s is not valid.", filename)
            return None
        return data  # type: ignore[no-any-return]  # validated against the schema (an object)

    def _validate_data(self, data: Any) -> bool:
        try:
            jsonschema.validate(data, self._schema)
        except jsonschema.ValidationError as e:
            self._logger.error("json validation error: %s", e.message)
            return False
        except jsonschema.SchemaError as e:
            self._logger.error("json schema error: %s", e.message)
            return False
        return True

    def store(self, data: dict[str, Any]) -> None:
        """Replace ``data`` and write it - atomically with respect to other writers."""
        with self._lock:
            previous, self.data = self.data, data
            try:
                self.store_data()
            except ValueError:
                self.data = previous
                raise

    def store_data(self, force_write: bool = False, skip_backup: bool = False) -> None:
        """Write ``data`` if it changed (or ``force_write``); the previous file becomes the backup file.

        :raises ValueError: if ``data`` does not match the schema.
        """
        with self._lock:
            if not self._validate_data(self.data):
                raise ValueError(f"store_data - data for '{self.filename}' does not match the schema.")
            new_string = json.dumps(self.data, indent=4)
            if not force_write and new_string == self.last_string:
                self._logger.debug("store_data - skipped, data not changed.")
                return
            # the box is switched off by pulling the plug: the new content is on the disk before it replaces anything -
            # otherwise the file can be empty after a power loss
            temp = self.filename + ".tmp"
            with open(temp, "w", encoding="utf-8") as outfile:
                outfile.write(new_string)
                outfile.flush()
                os.fsync(outfile.fileno())
            if not skip_backup:
                with contextlib.suppress(FileNotFoundError):  # the file does not exist yet
                    os.replace(self.filename, self.backup_filename)
            os.replace(temp, self.filename)  # the regular file is always complete - or missing (-> backup)
            self.last_string = new_string
            self._logger.debug("store_data - written to %s.", self.filename)
