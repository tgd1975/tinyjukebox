"""JSON file with schema validation and a backup copy of the previous version."""

import contextlib
import json
import logging
import os
import threading
import time
from typing import Any

import jsonschema

from tinyjukebox.core.logging import get_child
from tinyjukebox.data import degraded

__all__ = ["JsonData", "tolerant"]


def tolerant(schema: Any) -> Any:
    """``schema`` without ``"additionalProperties": false``: a field that a newer version added is accepted."""
    if isinstance(schema, list):
        return [tolerant(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: tolerant(value) for key, value in schema.items() if not (key == "additionalProperties" and value is False)
    }


class JsonData:
    """Content of a JSON file (``data``), validated against ``schema``.

    Every write moves the current file to ``backup_filename`` first. If the regular file is missing, empty or
    invalid on start, the backup is used and written back to the regular file. Without a usable file and backup,
    ``FileNotFoundError`` is raised.

    ``data`` may be changed by the owner; :meth:`store_data` writes it (only if it changed). :meth:`store` replaces
    and writes the data in one step - the collections use it, so that concurrent writers never see half built data.

    ``entries``: the file is a collection - an object whose values are entries of the same kind (tags, targets,
    activities), and the box works with what it can read of it (docs/developer/library.md, section 4.21):

    - Every entry is checked by itself. One that is damaged, or of a kind this version does not know, is set aside
      (``aside``) instead of making the whole file unusable: it is not in ``data``, and it is written back unchanged
      with every write - this version never deletes what a newer one wrote. An entry of ``data`` with the same key
      replaces it.
    - A field this version does not know is accepted; the owner keeps it (``extra`` of the entries).
    - If neither the file nor its backup can be read, both are kept under a name of their own (``.damaged-<time>``)
      and the collection starts empty instead of not starting.

    What is set aside is noted in :mod:`tinyjukebox.data.degraded`.
    """

    def __init__(
        self,
        filename: str,
        backup_filename: str,
        schema: dict[str, Any],
        logger: logging.Logger,
        entries: bool = False,
    ) -> None:
        self._logger = get_child(logger, f"{type(self).__name__}.{filename}")
        self.filename = filename
        self.backup_filename = backup_filename
        self._entries = entries
        self._schema = tolerant(schema) if entries else schema
        self._lock = threading.RLock()
        self.last_string: str | None = None
        self.aside: dict[str, Any] = {}
        self.started_empty = False  # neither the file nor its backup could be read
        self._source = os.path.basename(filename)

        with self._lock:
            read = self._read_data(self.filename)
            if read is None:
                self._logger.error("failed to load regular file - trying the backup file instead.")
                read = self._read_data(self.backup_filename)
                if read is None and not entries:
                    raise FileNotFoundError(f"neither '{filename}' nor its backup '{backup_filename}' can be loaded.")
                if read is None:
                    read = self._start_empty()
                self.data: dict[str, Any] = read[0]
                self.aside = read[1]
                self._logger.info("rewriting regular file with the content of the backup file.")
                self.store_data(force_write=True, skip_backup=True)
            else:
                self.data, self.aside = read
                self.last_string = self._serialize()
            if entries:
                self._note_aside()
        self._logger.debug("__init__ - %s", self.data)

    def _read_data(self, filename: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The data of the file and the entries set aside - or None if the file cannot be used at all."""
        self._logger.info("_read_data - from file %s", filename)
        try:
            with open(filename, encoding="utf-8") as infile:
                data = json.load(infile)
        except FileNotFoundError:
            self._logger.error("_read_data - file %s not found.", filename)
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._logger.error("_read_data - file %s is not valid json: %s", filename, e)
            return None
        if self._entries:
            if not isinstance(data, dict):
                self._logger.error("_read_data - file %s holds no object.", filename)
                return None
            return self.split(data)
        if not self._validate_data(data):
            self._logger.error("_read_data - content of file %s is not valid.", filename)
            return None
        return data, {}

    def split(self, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """The entries of a collection that this version can use, and the ones it sets aside."""
        valid, aside = {}, {}
        for key, entry in data.items():
            error = self.entry_error(entry)
            if error is None:
                valid[key] = entry
            else:
                self._logger.warning("entry '%s' set aside: %s", key, error)
                aside[key] = entry
        return valid, aside

    def entry_error(self, entry: Any) -> str | None:
        """Why an entry of a collection cannot be used - None if it can."""
        try:
            jsonschema.validate(entry, self._schema["additionalProperties"])
        except jsonschema.ValidationError as e:
            return e.message
        return None

    def set_aside(self, key: str, entry: Any, why: str) -> None:
        """The owner cannot use an entry that passed the schema: it is kept like a damaged one."""
        with self._lock:
            self._logger.warning("entry '%s' set aside: %s", key, why)
            self.data.pop(key, None)
            self.aside[key] = entry
            self._note_aside()

    def _note_aside(self) -> None:
        message = "{}: entry '{}' cannot be used by this version and is left out"
        degraded.replace(self._source, {key: message.format(self._source, key) for key in self.aside})

    def _start_empty(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Neither file nor backup can be read: both stay as they are under a name of their own - somebody may repair
        them - and the collection starts empty. Nothing is overwritten."""
        self.started_empty = True
        stamp = time.strftime("%Y%m%d-%H%M%S")
        kept = [name for name in (self.filename, self.backup_filename) if os.path.exists(name)]
        for name in kept:
            os.replace(name, f"{name}.damaged-{stamp}")
        if not kept:  # nothing was lost: e.g. the positions of a box that has never played
            self._logger.warning("%s: neither the file nor its backup exists; starting without entries", self._source)
            return {}, {}
        message = f"{self._source}: neither the file nor its backup can be read - kept as .damaged-{stamp}"
        self._logger.error("%s; starting without its entries", message)
        degraded.note(self._source + " (file)", "", message)
        return {}, {}

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

    def _serialize(self) -> str:
        aside = {key: entry for key, entry in self.aside.items() if key not in self.data}
        return json.dumps({**self.data, **aside}, indent=4)

    def store(self, data: dict[str, Any], aside: dict[str, Any] | None = None) -> None:
        """Replace ``data`` - and the entries set aside, if given - and write it, atomically with respect to other
        writers."""
        with self._lock:
            previous, self.data = self.data, data
            previous_aside = self.aside
            if aside is not None:
                self.aside = aside
            try:
                self.store_data()
            except ValueError:
                self.data, self.aside = previous, previous_aside
                raise
            if aside is not None and self._entries:
                self._note_aside()

    def store_data(self, force_write: bool = False, skip_backup: bool = False) -> None:
        """Write ``data`` if it changed (or ``force_write``); the previous file becomes the backup file.

        :raises ValueError: if ``data`` does not match the schema.
        """
        with self._lock:
            if not self._validate_data(self.data):
                raise ValueError(f"store_data - data for '{self.filename}' does not match the schema.")
            new_string = self._serialize()
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
