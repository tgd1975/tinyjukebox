"""All targets (``targets.json``): albums, radio stations, entries of Spotify and advent calendars."""

import json
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.data.common import Listener, Mirror, TargetType, UpdateJson, is_unmapped
from tinyjukebox.data.jsondata import JsonData
from tinyjukebox.data.target import Target, TargetAlbum, TargetCalendar, TargetRadio, TargetSpotify
from tinyjukebox.schema.targets import schema

__all__ = ["LAST_ID_SUFFIX", "Targets", "create_target_factory", "load_target_factory"]

LAST_ID_SUFFIX = "-last-id.json"
"""``targets.json`` -> ``targets-last-id.json``: the highest album id given so far."""


def _is_number(target_id: str | None) -> bool:
    return target_id is not None and target_id.isascii() and target_id.isdigit()


def _check_target_dict(target: dict[str, Any] | None, logger: logging.Logger) -> dict[str, Any]:
    if target is None:
        logger.error("target not provided")
        raise ValueError("target not provided")
    if "target-id" not in target:
        logger.error("provided target has no attribute target-id")
        raise ValueError("provided target has no attribute target-id")
    if is_unmapped(target["target-id"]):
        logger.error("target-id must be set.")
        raise ValueError("target-id must be set.")
    if "target-type" not in target:
        raise ValueError("provided target has no attribute target-type")
    if target["target-type"] not in {kind.name for kind in TargetType}:
        logger.error("unknown target type: %s.", target["target-type"])
        raise ValueError(f"unknown target type: {target['target-type']}.")
    return target


def create_target_factory(
    target: dict[str, Any] | None, root_folder: str, update_json: UpdateJson | None, logger: logging.Logger
) -> Target:
    """A new target - checked for sanity (e.g. files exist, valid url). :raises ValueError: otherwise."""
    factory_logger = get_child(logger, "create_target_factory")
    target = _check_target_dict(target, factory_logger)
    if target["target-type"] == TargetType.radio.name:
        return TargetRadio.create_target(target, logger, update_json)
    if target["target-type"] == TargetType.spotify.name:
        return TargetSpotify.create_target(target, logger, update_json)
    if target["target-type"] == TargetType.calendar.name:
        return TargetCalendar.create_target(target, logger, update_json)
    return TargetAlbum.create_target(target, root_folder, logger, update_json)


def load_target_factory(
    target: dict[str, Any] | None, root_folder: str, update_json: UpdateJson, logger: logging.Logger
) -> Target:
    """A target from the json file - not checked for sanity (a missing album must not prevent the start)."""
    factory_logger = get_child(logger, "load_target_factory")
    target = _check_target_dict(target, factory_logger)
    if target["target-type"] == TargetType.radio.name:
        return TargetRadio(target, logger, update_json)
    if target["target-type"] == TargetType.spotify.name:
        return TargetSpotify(target, logger, update_json)
    if target["target-type"] == TargetType.calendar.name:
        return TargetCalendar(target, logger, update_json)
    return TargetAlbum(target, root_folder, logger, update_json)


class Targets:
    """Targets by id. Changes are written to the json file immediately (see :class:`~tinyjukebox.data.tags.Tags`
    for the locking rules).

    Albums, radio stations, entries of Spotify and calendars share the ids. New ids count up and are never given twice -
    also not after the target with the highest id has been deleted: the new target would inherit everything that still
    refers to the old id (e.g. the cover cached by the browser). The highest id given so far is kept in a file of its
    own (:data:`LAST_ID_SUFFIX`) - older versions would reject an additional entry in the targets json. Without that
    file, new ids follow the highest existing id.

    With ``id_source`` the ids come from there: the library assigns them for all boxes, and a box that works with a
    library creates a target under a provisional id (:mod:`tinyjukebox.sync`).
    """

    def __init__(self, filename: str, root_folder: str, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._lock = threading.RLock()
        self._json = JsonData(filename, filename + "_", schema, self._logger, entries=True)
        self._last_id_file = os.path.splitext(filename)[0] + LAST_ID_SUFFIX
        self.root_folder = root_folder
        self.id_source: Callable[[], str] | None = None
        self._mirror = Mirror()
        self.targets: dict[str, Target] = {}
        for key, target in list(self._json.data.items()):
            try:
                self.targets[key] = load_target_factory(target, root_folder, self._update_json, self._logger)
            except ValueError as e:  # a target this version cannot use does not keep the box from starting
                self._json.set_aside(key, target, str(e))

    def _to_dict(self) -> dict[str, Any]:
        return {key: target.to_dict() for key, target in self.targets.items()}

    def _update_json(self) -> None:
        with self._lock:
            data = self._to_dict()
            self._json.store(data)
            self._mirror.changed(data)

    def listen(self, listener: Listener) -> None:
        """``listener(target_id, entry)`` is called for every target that changes from now on (``None``: deleted)."""
        with self._lock:
            self._mirror.listen(listener, self._to_dict())

    def sync(self, get: Callable[[], dict[str, Any]], renamed: dict[str, str] | None = None) -> bool:
        """Make the targets what ``get()`` returns (the content of ``targets.json``), keeping the targets that stay
        - also one whose id changed from a key of ``renamed`` to its value. The listener is not called. Returns
        whether something changed."""
        with self._lock:
            data, aside = self._json.split(get())
            if data == self._to_dict() and aside == self._json.aside:
                return False
            for old, new in (renamed or {}).items():
                if old in self.targets and new in data and new not in self.targets:
                    self.targets[new] = self.targets.pop(old)
            for key in [key for key in self.targets if key not in data]:
                del self.targets[key]
            for key, entry in data.items():
                target = self.targets.get(key)
                try:
                    if (
                        target is not None
                        and target.target_type is not None
                        and target.target_type.name == entry.get("target-type")
                    ):
                        target.load(entry)
                    else:
                        self.targets[key] = load_target_factory(
                            entry, self.root_folder, self._update_json, self._logger
                        )
                except ValueError as e:
                    self._logger.warning("sync - target '%s' set aside: %s", key, e)
                    self.targets.pop(key, None)
                    aside[key] = entry
                self._remember_id(key)
            data = self._to_dict()
            self._json.store(data, aside)
            self._mirror.replaced(data)
            return True

    def all(self) -> list[Target]:
        """Snapshot of all targets."""
        with self._lock:
            return list(self.targets.values())

    def albums(self) -> list[TargetAlbum]:
        return [target for target in self.all() if isinstance(target, TargetAlbum)]

    def radios(self) -> list[TargetRadio]:
        return [target for target in self.all() if isinstance(target, TargetRadio)]

    def spotify_entries(self) -> list[TargetSpotify]:
        """The entries of Spotify - not the calendars, which play like one."""
        return [
            target
            for target in self.all()
            if isinstance(target, TargetSpotify) and not isinstance(target, TargetCalendar)
        ]

    def calendars(self) -> list[TargetCalendar]:
        return [target for target in self.all() if isinstance(target, TargetCalendar)]

    def get_target(self, target_id: str) -> Target:
        """:raises ValueError: for unknown ids."""
        target = self.targets.get(target_id)
        if target is None:
            self._logger.warning("get_target - unknown target_id %s.", target_id)
            raise ValueError(f"get_target - unknown target_id {target_id}.")
        return target

    def get_targets_of_type(self, target_type: TargetType) -> list[Target]:
        return [target for target in self.all() if target.target_type == target_type]

    def add_target(self, target: dict[str, Any] | None, create_id: bool = True) -> Target:
        """Add a target (``create_id``: with a new id). :raises ValueError: if it is invalid or the id exists."""
        if target is None:
            self._logger.warning("add_target - target is None.")
            raise ValueError("add_target - target is None.")
        with self._lock:
            if create_id:
                target["target-id"] = self.id_source() if self.id_source else str(self._highest_id() + 1)
            self._logger.info("add_target - target: %s.", target)
            if str(target.get("target-id")) in self.targets or str(target.get("target-id")) in self._json.aside:
                self._logger.warning("add_target - target_id %s exists already.", target["target-id"])
                raise ValueError(f"add_target - target_id {target['target-id']} exists already.")
            new_target = create_target_factory(target, self.root_folder, self._update_json, self._logger)
            target_id = new_target.target_id
            assert target_id is not None  # noqa: S101 - set by the constructor
            self._remember_id(target_id)
            self.targets[target_id] = new_target
            self._update_json()
        return new_target

    def delete_target(self, target_id: str | None) -> None:
        if target_id is None:
            self._logger.warning("delete_target - target_id is None.")
            raise ValueError("delete_target - target_id is None.")
        with self._lock:
            if target_id not in self.targets:
                self._logger.warning("delete_target - unknown target_id %s.", target_id)
                raise ValueError(f"delete_target - unknown target_id {target_id}.")
            self._remember_id(target_id)  # e.g. the file does not exist yet - the id would be given again
            del self.targets[target_id]
            self._update_json()

    def sanity_check(self) -> tuple[bool, str]:
        errors = []
        for key, target in list(self.targets.items()):
            if key != target.target_id:
                message = f"sanity_check - key '{key}' != target.target_id '{target.target_id}'"
                self._logger.warning(message)
                errors.append(message)
            passed, error = target.sanity_check()
            if not passed:
                errors.append(error)
        return not errors, "\n".join(errors)

    def to_text(self) -> str:
        return "\n".join(f"key: {key}, target: {target.to_text()}" for key, target in list(self.targets.items()))

    def _highest_id(self) -> int:
        """The highest id given so far (0: none) - stored or, if higher, of the existing targets."""
        keys = [*self.targets, *self._json.aside]  # a target set aside keeps its id, too
        return max([self._read_last_id(), *(int(key) for key in keys if _is_number(key))])

    def _remember_id(self, target_id: str) -> None:
        """Raise the stored last id to ``target_id`` - before the targets json is written: a crash leaves a gap,
        never an id that is given twice."""
        if _is_number(target_id) and int(target_id) > self._read_last_id():
            self._write_last_id(int(target_id))

    def _read_last_id(self) -> int:
        try:
            with open(self._last_id_file, encoding="utf-8") as f:
                last_id = json.load(f)["last-id"]
        except FileNotFoundError:
            return 0
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._logger.warning(
                "cannot read '%s' (%s) - new ids follow the highest existing id.", self._last_id_file, e
            )
            return 0
        if not isinstance(last_id, int) or isinstance(last_id, bool):
            self._logger.warning(
                "'%s': last-id must be a number - new ids follow the highest existing id.", self._last_id_file
            )
            return 0
        return last_id

    def _write_last_id(self, last_id: int) -> None:
        temp = self._last_id_file + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"last-id": last_id}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self._last_id_file)
