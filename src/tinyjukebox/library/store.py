"""What the library stores, and the rules by which the changes of the boxes are merged into it
(docs/developer/library.md, section 4).

- The catalog are JSON files that a box receives whole: tags and targets (albums and stations), and the list of the
  covers of albums and stations.
  Every entry has a revision; a deleted entry stays as a mark (``value`` is null).
- Per box: positions and settings (written by the box), the answers to its changes, the albums it is uploading.
- The id table resolves the provisional ids of the boxes. It is never emptied.
- The albums themselves lie in the albums folder, their manifests next to the catalog.
- The broken files of the albums: what the library found itself and what the boxes reported.

The lock of the store is never held while the working data of the library (``tinyjukebox.data``) is called: a change
that a box made is announced with ``changed`` after the lock was released.
"""

import contextlib
import glob
import json
import logging
import os
import secrets
import shutil
import threading
from collections.abc import Callable, Iterator
from typing import Any

import jsonschema
import mutagen

from tinyjukebox.data.common import is_unmapped
from tinyjukebox.images import ALBUM_PREFIX, STATION_PREFIX, image_extension, is_cover
from tinyjukebox.library.journal import Journal
from tinyjukebox.metadata import mp3_info, stored_cover
from tinyjukebox.schema.activities import schema as activities_schema
from tinyjukebox.schema.tags import schema as tags_schema
from tinyjukebox.schema.targets import schema as targets_schema
from tinyjukebox.sync import KINDS, LIBRARY, SOFTWARE
from tinyjukebox.sync import manifest as manifests
from tinyjukebox.sync.handshake import hash_secret, same
from tinyjukebox.sync.util import (
    read_json,
    sha256_bytes,
    sha256_file,
    timestamp,
    version_key,
    write_atomic,
    write_json,
)

__all__ = [
    "APP",
    "BOX",
    "CATALOG",
    "FORMAT",
    "IMAGES",
    "NO_MP3",
    "STAGING_FOLDER",
    "STATES",
    "Invalid",
    "Store",
    "Unknown",
    "knows_album_covers",
]

CATALOG = {"tag": "tags.json", "target": "targets.json"}
IMAGES = "images.json"  # the covers of albums and stations: a list of files, no entries
CATALOG_FILES = (*CATALOG.values(), IMAGES)
FORMAT = 1  # format version of the catalog files and of the state of a box
STATES = ("use", "manual", "dontuse")
STAGING_FOLDER = ".tinyjukebox-staging"  # in the albums folder: albums that boxes are uploading
BACKUP_SKIPS = ("archive", "json", "settings.json")  # in the data folder: what a backup leaves out
APP, BOX = "app", "box"  # the kinds of devices: a box, and the app on a phone (docs/developer/library.md, section 5.2)
NO_MP3 = "not a valid mp3 file"  # what the library notes about a track that it cannot read
_SCHEMAS = {
    "tag": tags_schema["additionalProperties"],
    "target": targets_schema["additionalProperties"],
    "position": activities_schema["additionalProperties"],
}


class Unknown(Exception):  # noqa: N818
    """Something the library does not have."""


class Invalid(Exception):  # noqa: N818
    """A change that the library rejects. ``details`` go into the answer to a request of the web page, as the
    details of an :class:`~tinyjukebox.errors.ApiError` do (its ``code``, the values of its text)."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class _Waiting(Exception):  # noqa: N818
    """A change that depends on an album whose files have not all arrived."""

    def __init__(self, missing: list[str] | None = None) -> None:
        super().__init__("waiting")
        self.missing = missing


class _Gone(Exception):  # noqa: N818
    """A change that refers to something the library deleted."""


def _may_change(entry: dict[str, Any], side: str, base: int) -> bool:
    """Whether a change by ``side`` that is based on revision ``base`` fits ``entry`` (section 4.4).

    Equal: the normal case. Higher: the library lost data, the box wins. Lower: someone else changed the entry in the
    meantime - unless every change since ``base`` was made by ``side`` itself, e.g. two changes made without
    connection.
    """
    return base >= entry["rev"] or (entry.get("by") == side and base >= entry.get("since", entry["rev"]))


def _changed(entry: dict[str, Any], side: str, base: int, who: str) -> None:
    if entry.get("by") != side:
        entry["by"] = side
        entry["since"] = base
    entry["rev"] = max(entry["rev"], base) + 1
    entry["who"] = who
    entry["when"] = timestamp()


def _new_entry(side: str) -> dict[str, Any]:
    return {"value": None, "rev": 0, "by": side, "since": 0}


def _is_album(value: Any) -> bool:
    return isinstance(value, dict) and value.get("target-type") == "album"


def knows_album_covers(version: object) -> bool:
    """Whether the box software ``version`` knows the cover of an album as an image of the images folder, named by
    the album (since 0.32). An older one gets the catalog without them (:meth:`Store.catalog_file`)."""
    try:
        return version_key(str(version)) >= (0, 32, 0)
    except ValueError:
        return True


class Store:
    def __init__(
        self,
        folder: str,
        albums_folder: str,
        images_folder: str,
        logger: logging.Logger,
        notify: Callable[[str], None],
        changed: Callable[[], None] | None = None,
    ) -> None:
        """``notify`` tells the boxes that "catalog" or "versions" changed; ``changed`` tells the library itself that
        a box changed the catalog."""
        self.folder = folder
        self.albums_folder = albums_folder
        self.images_folder = images_folder
        self.lock = threading.RLock()
        self._logger = logger
        self._notify = notify
        self.changed = changed
        self._depth = 0
        self._dirty = False
        for name in ("catalog", "boxes", "manifests", "archive"):
            os.makedirs(os.path.join(folder, name), exist_ok=True)
        for name in (albums_folder, images_folder, self._path_staging()):
            os.makedirs(name, exist_ok=True)
        self._catalog: dict[str, dict[str, Any]] = {}
        for kind, name in CATALOG.items():
            self._catalog[kind] = read_json(self._path("catalog", name), {"format": FORMAT, "entries": {}})
        self._catalog["target"].setdefault("last-id", 0)
        self._images: dict[str, Any] = read_json(self._path("catalog", IMAGES), None) or self._take_over_images()
        self._ids: dict[str, str] = read_json(self._path("ids.json"), {})
        self._versions: dict[str, Any] = read_json(self._path("versions.json"), {})
        self._broken: dict[str, dict[str, Any]] = read_json(self._path("broken.json"), {})
        self._boxes: dict[str, dict[str, Any]] = {}
        for filename in glob.glob(self._path("boxes", "*.json")):
            box = read_json(filename)
            box.setdefault("kind", BOX)  # until 0.39 every device was a box
            self._boxes[box["id"]] = box
        self.journal = Journal(self._path("journal.sqlite"))
        for kind, name in CATALOG.items():
            if not os.path.exists(self._path("catalog", name)):
                self._save_catalog(kind, quiet=True)
        if not os.path.exists(self._path("catalog", IMAGES)):
            write_json(self._path("catalog", IMAGES), self._images)
        self._take_over_covers()
        self.verify_albums()
        self.scan_images()

    def close(self) -> None:
        self.journal.close()

    def backup_data(self) -> list[tuple[str, bytes]]:
        """The files of the data folder at one moment, for a backup (:mod:`tinyjukebox.backup`): (name, content).

        Left out: the settings, which are all secret; the working files, which are generated; and the archive of the
        box versions - their entries say that the files are to be fetched again. The key of a box stays as the
        library stores it: the hash of a long random number, which lets nobody in."""
        found: list[tuple[str, bytes]] = []
        with self.lock:
            for root, folders, names in os.walk(self.folder):
                if os.path.abspath(root) == os.path.abspath(self.folder):
                    folders[:] = [name for name in folders if name not in BACKUP_SKIPS]
                folders.sort()
                for name in sorted(names):
                    inside = os.path.relpath(os.path.join(root, name), self.folder).replace(os.sep, "/")
                    if inside in BACKUP_SKIPS or inside.startswith("journal.sqlite") or name.endswith(".tmp"):
                        continue
                    with open(os.path.join(root, name), "rb") as infile:
                        found.append((f"data/{inside}", infile.read()))
            versions = {version: dict(entry, files={}) for version, entry in self._versions.items()}
        content = json.dumps(versions, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        found = [item for item in found if item[0] != "data/versions.json"]
        found.append(("data/versions.json", content.encode("utf-8")))
        found.append(("data/journal.sqlite", self.journal.snapshot()))
        return sorted(found)

    def _path(self, *parts: str) -> str:
        return os.path.join(self.folder, *parts)

    def _path_staging(self, *parts: str) -> str:
        return os.path.join(self.albums_folder, STAGING_FOLDER, *parts)

    @contextlib.contextmanager
    def _section(self) -> Iterator[None]:
        """The lock of the store. When the outermost section ends and a box changed the catalog in it, the library
        is told - after the lock was released."""
        with self.lock:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                tell = self._dirty and self._depth == 0
                if tell:
                    self._dirty = False
        if tell and self.changed is not None:
            self.changed()

    # --- catalog -----------------------------------------------------------------------------------------------------

    def _entries(self, kind: str) -> dict[str, Any]:
        return self._catalog[kind]["entries"]  # type: ignore[no-any-return]

    def _save_catalog(self, kind: str, quiet: bool = False) -> None:
        write_json(self._path("catalog", CATALOG[kind]), self._catalog[kind])
        if not quiet:
            self._notify("catalog")

    def catalog_index(self, album_covers: bool = True) -> dict[str, Any]:
        """Checksum and format version of every catalog file, as :meth:`catalog_file` delivers it."""
        with self.lock:
            index = {}
            for name in CATALOG_FILES:
                data = self.catalog_file(name, album_covers)
                index[name] = {"sha256": sha256_bytes(data), "format": FORMAT, "size": len(data)}
            return index

    def catalog_file(self, name: str, album_covers: bool = True) -> bytes:
        """A file of the catalog. Without ``album_covers`` it is what a box before 0.32 knows: no album names a
        cover, and the list of the images holds the covers of the stations only. Such a box would not keep the file
        it reads at its start, and it would fetch every cover of an album with every pass."""
        if name not in CATALOG_FILES:
            raise Unknown(name)
        with self.lock, open(self._path("catalog", name), "rb") as infile:
            data = infile.read()
        if album_covers or name == CATALOG["tag"]:
            return data
        content = json.loads(data)
        if name == IMAGES:
            content["images"] = {
                key: image for key, image in content["images"].items() if key.startswith(STATION_PREFIX)
            }
        else:
            for entry in content["entries"].values():
                if _is_album(entry["value"]):
                    entry["value"] = {field: value for field, value in entry["value"].items() if field != "cover"}
        return (json.dumps(content, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    def catalog_path(self, name: str) -> str:
        if name not in CATALOG_FILES:
            raise Unknown(name)
        return self._path("catalog", name)

    def working(self, kind: str) -> dict[str, Any]:
        """What the library itself works with: the entries of ``kind`` without the marks of deleted ones."""
        with self.lock:
            return {key: entry["value"] for key, entry in self._entries(kind).items() if entry["value"] is not None}

    def entry(self, kind: str, key: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self._entries(kind).get(key)
            return dict(entry) if entry is not None else None

    def new_target_id(self) -> str:
        """An id is never assigned twice, also not after its entry was deleted."""
        with self.lock:
            targets = self._catalog["target"]
            numbers = [int(key) for key in targets["entries"] if key.isascii() and key.isdigit()]
            targets["last-id"] = max([targets["last-id"], *numbers]) + 1
            self._save_catalog("target", quiet=True)
            return str(targets["last-id"])

    # --- changes on the page of the library --------------------------------------------------------------------------

    def set(self, kind: str, key: str, value: Any, who: str, imported: bool = False) -> None:
        """A change made in the library itself; ``value`` None deletes the entry.

        ``imported`` is the first filling (section 3.5): the entry keeps the revision 0, so that a box that brings
        the same entry along with a change of its own wins.
        """
        album = self._describe_album(value) if isinstance(value, dict) and _is_album(value) else None
        with self._section():
            entries = self._entries(kind)
            entry = entries.get(key)
            if entry is None:
                if value is None:
                    return
                entry = entries[key] = _new_entry(LIBRARY)
            elif entry["value"] == value and entry.get("album", {}).get("checksum") == (album or {}).get("checksum"):
                return
            if imported:
                entry.update({"who": who, "when": timestamp()})
            else:
                _changed(entry, LIBRARY, entry["rev"], who)
            self._put(kind, key, entry, value, album)
            self._save_catalog(kind)
            if value is not None:
                self._apply_all_waiting()

    def _put(self, kind: str, key: str, entry: dict[str, Any], value: Any, album: dict[str, Any] | None) -> None:
        """Store ``value`` in ``entry``; for an album also what the library knows of its files."""
        was_album = _is_album(entry["value"])
        entry["value"] = value
        if album is not None:
            manifest = album.pop("manifest")
            write_json(self._path("manifests", f"{key}.json"), manifest)
            entry["album"] = album
            self._check(key, entry, manifest)
        elif kind == "target" and was_album and value is None:
            entry.pop("album", None)
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._path("manifests", f"{key}.json"))
            if self._broken.pop(key, None) is not None:
                write_json(self._path("broken.json"), self._broken)
        if kind == "target" and key.isascii() and key.isdigit():
            self._catalog["target"]["last-id"] = max(self._catalog["target"]["last-id"], int(key))

    def _describe_album(self, value: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """What the catalog says about the files of an album, and its manifest. None for an album that cannot be
        read: it is an entry of the catalog without files, and no box fetches anything for it."""
        folder = os.path.join(self.albums_folder, value["album-folder"])
        if manifest is None:
            try:
                manifest = manifests.create(folder, value["playlist"])
            except OSError as error:
                self._logger.warning("the album in '%s' cannot be read: %s", value["album-folder"], error)
                return None
        return {
            "identity": manifests.identity(manifest),
            "checksum": manifests.checksum(manifest),
            "files": len(manifest["files"]),
            "size": sum(entry["size"] for entry in manifest["files"]),
            "lacks": manifests.missing(folder, manifest),
            "manifest": manifest,
        }

    def album_changed(self, key: str, who: str) -> None:
        """The files of an album changed in the library, e.g. its playlist was written anew."""
        with self.lock:
            entry = self._entries("target").get(key)
        if entry is None or not _is_album(entry["value"]):
            raise Unknown(f"album {key}")
        value: dict[str, Any] = entry["value"]
        album = self._describe_album(value)
        with self.lock:
            if album is None or entry.get("album", {}).get("checksum") == album["checksum"]:
                return
            _changed(entry, LIBRARY, entry["rev"], who)
            self._put("target", key, entry, value, album)
            self._save_catalog("target")

    def fill(self, kind: str, entries: dict[str, Any], who: str) -> list[str]:
        """The first filling (section 3.5): take over the entries the library does not have yet, under their ids.
        Returns the keys that were taken over; what is there already is skipped."""
        taken = []
        with self._section():
            for key, value in entries.items():
                if key in self._entries(kind):
                    continue
                self._validate(kind, value)
                self.set(kind, key, value, who, imported=True)
                taken.append(key)
            if taken:
                self._dirty = True
        return taken

    def describe_album(self, key: str, manifest: dict[str, Any], who: str) -> list[str]:
        """The manifest of an album whose files are sent to the library from outside (first filling). Returns the
        files that the library lacks."""
        if not manifests.valid(manifest):
            raise Invalid("the manifest is not valid")
        with self.lock:
            entry = self._album(key)
            album = self._describe_album(entry["value"], manifest)
            if album is not None and entry.get("album", {}).get("checksum") != album["checksum"]:
                _changed(entry, LIBRARY, entry["rev"], who)
                self._put("target", key, entry, entry["value"], album)
                self._save_catalog("target")
            self.verify_albums()
            return list(entry.get("album", {}).get("lacks", []))

    def set_position(self, box_id: str, uid: str, value: Any, who: str) -> None:
        """A deliberate change of a position on the page of the library: the library wins against the box."""
        self._set_own(box_id, "positions", uid, value, who)

    def set_setting(self, box_id: str, key: str, value: Any, who: str) -> None:
        self._set_own(box_id, "settings", key, value, who)

    def _set_own(self, box_id: str, kind: str, key: str, value: Any, who: str) -> None:
        with self.lock:
            box = self.box(box_id)
            entry = box[kind].setdefault(key, _new_entry(LIBRARY))
            _changed(entry, LIBRARY, entry["rev"], who)
            entry["value"] = value
            self.save_box(box)
        self._notify("catalog")

    # --- boxes -------------------------------------------------------------------------------------------------------

    def save_box(self, box: dict[str, Any]) -> None:
        write_json(self._path("boxes", f"{box['id']}.json"), box)

    def box(self, box_id: str) -> dict[str, Any]:
        box = self._boxes.get(box_id)
        if box is None:
            raise Unknown(f"box {box_id}")
        return box

    def boxes(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(box) for box in self._boxes.values()]

    def box_name(self, box: dict[str, Any]) -> str:
        return str(box["settings"].get("name", {}).get("value") or box["id"])

    def register(
        self, box_id: str, name: str, secret: str, kind: str = BOX, settings: dict[str, Any] | None = None
    ) -> None:
        """Store the key of a box after its handshake. A box that is known already gets a new key. ``kind``: what the
        password of the handshake said - the bootstrap password registers a box, a one-time password the app on a
        phone. ``settings`` are given to a device that is new."""
        with self.lock:
            box = self._boxes.get(box_id)
            if box is None:
                box = self._boxes[box_id] = {
                    "id": box_id,
                    "kind": kind,
                    "registered": timestamp(),
                    "positions": {},
                    "settings": {"name": {"value": name, "rev": 1, "by": LIBRARY, "since": 0}},
                    "results": {},
                    "waiting": {},
                    "staging": {},
                    "provisional": {},
                    "journal": 0,
                    "history": 0,
                    "defective": [],
                    "status": {},
                }
                for key, value in (settings or {}).items():
                    box["settings"][key] = {"value": value, "rev": 1, "by": LIBRARY, "since": 0}
                self._logger.info("%s %s (%s) registered", "phone" if kind == APP else "box", box_id, name)
            box["kind"] = kind
            box["key"] = hash_secret(secret)
            box["removed"] = False
            self.save_box(box)

    def authenticate(self, box_id: str, secret: str) -> dict[str, Any] | None:
        with self.lock:
            box = self._boxes.get(box_id)
            if box is None or box.get("removed") or not box.get("key") or not same(box["key"], hash_secret(secret)):
                return None
            return box

    def remove_box(self, box_id: str) -> None:
        """The key of the box is invalid from now on; its data stays."""
        with self.lock:
            box = self.box(box_id)
            box["removed"] = True
            box["key"] = ""
            self.save_box(box)
            self._logger.info("box %s removed", box_id)

    def take_over(self, box_id: str, old_id: str, who: str) -> None:
        """A box that was set up again takes over the positions and settings of an old one (section 4.7)."""
        with self.lock:
            box, old = self.box(box_id), self.box(old_id)
            for kind in ("positions", "settings"):
                for key, entry in old[kind].items():
                    mine = box[kind].get(key, {"rev": 0})
                    box[kind][key] = {
                        **entry,
                        "rev": max(entry["rev"], mine["rev"]) + 1,
                        "by": LIBRARY,
                        "since": mine["rev"],
                        "who": who,
                        "when": timestamp(),
                    }
            old["removed"] = True
            old["key"] = ""
            self.save_box(box)
            self.save_box(old)
            self._logger.info("box %s took over box %s", box_id, old_id)
        self._notify("catalog")

    def box_state(self, box_id: str) -> dict[str, Any]:
        """Positions and settings of the box with their revisions, and the numbers the library knows of it."""
        with self.lock:
            box = self.box(box_id)

            def plain(entries: dict[str, Any]) -> dict[str, Any]:
                return {key: {"value": entry["value"], "rev": entry["rev"]} for key, entry in entries.items()}

            return {
                "format": FORMAT,
                "positions": plain(box["positions"]),
                "settings": plain(box["settings"]),
                "provisional": box["provisional"],
                "journal": box["journal"],
            }

    def contact(self, box: dict[str, Any], software: str, interface: int) -> None:
        with self.lock:
            box["contact"] = timestamp()
            box["software"] = software
            box["interface"] = interface

    # --- changes of a box (sections 4.4 to 4.6) ----------------------------------------------------------------------

    @staticmethod
    def _provisional(box_id: str, value: Any) -> int | None:
        """The running number of a provisional id of this box (``<box>-<number>``), else None."""
        prefix = f"{box_id}-"
        if isinstance(value, str) and value.startswith(prefix) and value[len(prefix) :].isdigit():
            return int(value[len(prefix) :])
        return None

    def _reference(self, box: dict[str, Any], value: Any) -> str:
        """The final id behind the id of a target that a box sent; a provisional id is resolved by the id table."""
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not value:
            raise Invalid(f"'{value}' is not an id")
        number = self._provisional(box["id"], value)
        if number is None:
            return value
        final = self._ids.get(f"{box['id']}/target/{number}")
        if final is not None:
            return final
        if str(number) in box["staging"] or any(
            entry["op"] == "create" and entry.get("key") == value for entry in box["waiting"].values()
        ):
            raise _Waiting
        raise Invalid(f"the id {value} cannot be resolved")

    def _target(self, box: dict[str, Any], value: Any) -> str:
        """The id of a target that a tag or a position of a box refers to. It must exist."""
        final = self._reference(box, value)
        entry = self._entries("target").get(final)
        if entry is None:
            raise Invalid(f"the target {final} does not exist")
        if entry["value"] is None:
            raise _Gone(f"the target {final} was deleted")
        return final

    def apply(self, box_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge entries of the outbox of a box, in order. An entry that is sent again gets the same answer."""
        with self._section():
            box = self.box(box_id)
            numbers = [entry["n"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("n"), int)]
            if len(numbers) != len(entries):
                raise Invalid("every entry needs a running number")
            for old in [key for key in box["results"] if numbers and int(key) < min(numbers)]:
                del box["results"][old]  # the box has dropped these entries, so it has seen their answers
            results = [{"n": entry["n"], **self._answer(box, entry)} for entry in entries]
            self.save_box(box)
            return results

    def _answer(self, box: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        number = str(entry["n"])
        if number in box["results"]:
            return box["results"][number]  # type: ignore[no-any-return]
        try:
            for field in ("op", "kind", "key", "by", "version"):
                if not isinstance(entry.get(field), str) or not entry[field]:
                    raise Invalid(f"'{field}' is missing")
            if box["kind"] == APP and not self._app_may(entry):
                raise Invalid(f"a phone may not change this: {entry['op']} {entry['kind']}")
            if entry["version"] in box["defective"]:
                result = self._discard_entry(box, entry)
            else:
                result = self._apply_entry(box, entry)
        except _Waiting as waiting:
            box["waiting"][number] = entry
            return {"result": "waiting", **({"missing": waiting.missing} if waiting.missing is not None else {})}
        except (Invalid, KeyError, TypeError, ValueError) as error:
            reason = str(error) if isinstance(error, Invalid) else f"malformed: {error!r}"
            self._logger.error("box %s: change %s rejected (%s): %s", box["id"], entry["n"], reason, entry)
            self.journal.record("rejected", box["id"], {"entry": entry, "reason": reason})
            result = {"result": "rejected", "reason": reason}
        box["waiting"].pop(number, None)
        box["results"][number] = result
        return result

    @staticmethod
    def _app_may(entry: dict[str, Any]) -> bool:
        """What the app on a phone may change (section 5.2): a position, and a tag that the software created as not
        linked. Its key is lost more easily than a box, and a child needs nothing else."""
        if entry["op"] != "set":
            return False
        if entry["kind"] == "position":
            return True
        value = entry.get("value")
        return (
            entry["kind"] == "tag"
            and entry["by"] == SOFTWARE
            and isinstance(value, dict)
            and isinstance(value.get("target"), dict)
            and is_unmapped(value["target"].get("id"))
        )

    def _conflict(self, box: dict[str, Any], entry: dict[str, Any], what: str, has: Any) -> dict[str, Any]:
        """Both sides changed the same entry: the value of the library stays, the change of the box is dropped."""
        clock = entry.get("clock", {})
        wanted = entry.get("value")
        self._logger.warning(
            "conflict: box %s changed %s, the library wins. The box wanted %s, the library has %s. Changed by %s at %s "
            "(clock of the box %s)",
            box["id"], what, wanted, has, entry["by"], clock.get("time"),
            "synchronized" if clock.get("synchronized") else "not synchronized",
        )  # fmt: skip
        self.journal.record("conflict", box["id"], {"what": what, "wanted": wanted, "library": has, "entry": entry})
        return {"result": "conflict", "what": what, "wanted": wanted, "library": has}

    def _discard_entry(self, box: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        """An entry written with a version that this box reported as defective (section 4.11)."""
        if entry["by"] != SOFTWARE:
            self._logger.warning(
                "box %s: change %s was entered by %s with the defective version %s and is not applied: %s",
                box["id"], entry["n"], entry["by"], entry["version"], entry,
            )  # fmt: skip
            self.journal.record("discarded", box["id"], {"entry": entry})
        return {"result": "discarded"}

    @staticmethod
    def _validate(kind: str, value: Any) -> None:
        if kind in _SCHEMAS:
            try:
                jsonschema.validate(value, _SCHEMAS[kind])
            except jsonschema.ValidationError as error:
                raise Invalid(f"the {kind} does not fit its schema: {error.message}") from None

    def _apply_entry(self, box: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        op, kind = entry["op"], entry["kind"]
        if op == "create" and kind == "target":
            return self._create_target(box, entry)
        if op != "set" or kind not in KINDS:
            raise Invalid(f"unknown change '{op} {kind}'")
        base = entry["base"]
        if not isinstance(base, int) or isinstance(base, bool):
            raise Invalid("'base' is not a revision")
        own = kind in ("position", "setting")
        entries = box[f"{kind}s"] if own else self._entries(kind)
        try:
            key, value = self._resolved(box, kind, entry["key"], entry.get("value"), entry["version"])
        except _Gone as gone:
            what = f"{kind} {entry['key']}"
            if own:  # a position in an album that the library deleted: nothing can resume it
                return {"result": "conflict", "what": what, "wanted": entry.get("value"), "library": None}
            return self._conflict(box, entry, what, str(gone))
        current = entries.get(key)
        if current is None:
            if kind == "target":
                raise Invalid(f"the target {key} does not exist")
            if value is None:
                return {"result": "applied", "rev": 0}
            current = entries[key] = _new_entry(box["id"])
        elif (verdict := self._judge(box, entry, kind, key, value, current)) is not None:
            return verdict
        if kind == "position" and current["rev"] > 0 and current.get("version") != entry["version"]:
            # the state before a version wrote: restored if this version turns out to be defective
            current.setdefault("history", []).append({"value": current["value"], "version": current.get("version")})
        _changed(current, box["id"], base, entry["by"])
        if own:
            current["value"] = value
            current["version"] = entry["version"]
        else:
            self._put(kind, key, current, value, self._album_of(key, entry) if _is_album(value) else None)
            self._save_catalog(kind)
            self._dirty = True
        return {"result": "applied", "rev": current["rev"]}

    def _resolved(self, box: dict[str, Any], kind: str, key: str, value: Any, version: str) -> tuple[str, Any]:
        """Key and value of a change with the final ids in place of provisional ones, the value checked. ``version``
        is the box software that made the change."""
        if kind == "target":
            key = self._reference(box, key)
            if value is not None:
                value = {**value, "target-id": key}
                has: dict[str, Any] = (self._entries("target").get(key) or {}).get("value") or {}
                if _is_album(value) and _is_album(has) and has.get("cover") and not knows_album_covers(version):
                    value["cover"] = has["cover"]  # a box before 0.32 changed the album: it does not know the cover
        elif kind == "tag" and value is not None and not is_unmapped(value["target"].get("id")):
            value = {**value, "target": {**value["target"], "id": self._target(box, value["target"]["id"])}}
        elif kind == "position" and value is not None:
            value = {**value, "target-id": self._target(box, value["target-id"])}
        if value is not None:
            self._validate(kind, value)
            owner = {"tag": lambda: value["tag"]["uid"], "position": lambda: value["tag-uid"]}.get(kind)
            if owner is not None and owner() != key:
                raise Invalid(f"the {kind} does not belong to the key {key}")
        return key, value

    def _judge(
        self, box: dict[str, Any], entry: dict[str, Any], kind: str, key: str, value: Any, current: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The answer to a change of an entry that exists, if it is not applied (section 4.5); None if it is."""
        by_itself = kind == "tag" and entry["by"] == SOFTWARE
        # the files of an album changed on the box: the entry may be what it was, the change is in its manifest
        manifest = entry.get("manifest")
        other_files = manifest is not None and (
            not manifests.valid(manifest) or manifests.checksum(manifest) != current.get("album", {}).get("checksum")
        )
        if current["value"] == value and not other_files and (current["rev"] > 0 or value is not None):
            return {"result": "applied", "rev": current["rev"]}  # both did the same
        if by_itself and current["value"] is not None:
            # the box created the tag by itself as "not linked" and the library knows it already: no deliberate change
            return {"result": "applied", "rev": current["rev"]}
        if _may_change(current, box["id"], entry["base"]) or by_itself:
            return None
        what = f"{kind} {key}"
        if kind in ("position", "setting"):
            # no conflict of two people: the library changed the entry on purpose and wins (section 4.4)
            self._logger.info("box %s: %s was changed in the library, its value stays", box["id"], what)
            return {"result": "conflict", "what": what, "wanted": value, "library": current["value"]}
        return self._conflict(box, entry, what, current["value"] if current["value"] is not None else "deleted")

    def _album_of(self, key: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        """What the catalog says about the files of an album that a box changed. With a new manifest the files that
        differ are removed here; the box sends them, because the library lacks them then."""
        manifest = entry.get("manifest")
        if manifest is None:
            return None
        if not manifests.valid(manifest):
            raise Invalid("the manifest is not valid")
        folder = os.path.join(self.albums_folder, entry["value"]["album-folder"])
        old = read_json(self._path("manifests", f"{key}.json"), {"files": []})
        checksums = {item["name"]: item["sha256"] for item in manifest["files"]}
        for item in old["files"]:
            if checksums.get(item["name"]) != item["sha256"]:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(manifests.path(folder, item["name"]))
        return self._describe_album(entry["value"], manifest)

    def resolve(self, box_id: str, kind: str, value: str) -> dict[str, Any]:
        """The final id of a provisional id - or, for an album that is not complete, the files that are missing."""
        with self.lock:
            box = self.box(box_id)
            number = self._provisional(box_id, value)
            if number is None or kind != "target":
                raise Unknown(value)
            final = self._ids.get(f"{box_id}/target/{number}")
            if final is not None:
                return {"id": final, "value": self._entries("target")[final]["value"]}
            if str(number) in box["staging"]:
                return {"missing": self._staging_missing(box, str(number))}
            try:
                self._reference(box, value)
            except _Waiting:
                return {"waiting": True}
            except Invalid:
                pass
            raise Unknown(value)

    # --- targets that a box created (sections 4.6, 4.8) --------------------------------------------------------------

    def _staging_folder(self, box: dict[str, Any], number: str) -> str:
        return self._path_staging(f"{box['id']}-{number}")

    def _staging_missing(self, box: dict[str, Any], number: str) -> list[str]:
        return manifests.missing(self._staging_folder(box, number), box["staging"][number]["manifest"])

    def _create_target(self, box: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        box_id, value = box["id"], entry["value"]
        number = self._provisional(box_id, entry["key"])
        if number is None:
            raise Invalid("a new target needs a provisional id of its box")
        self._validate("target", value)
        # an album that its box cannot read comes without a manifest: it is an entry of the catalog without files
        album = _is_album(value) and entry.get("manifest") is not None
        if album and not manifests.valid(entry["manifest"]):
            raise Invalid("the manifest is not valid")
        key = f"{box_id}/target/{number}"
        targets = self._entries("target")
        # the entry carries a mark of the provisional id it was created under: entry and resolution are stored in
        # one step. A checksum, because a provisional id that is visible on a box means "not settled yet"
        origin = sha256_bytes(key.encode())[:16]
        final = self._ids.get(key) or next((i for i, target in targets.items() if target.get("origin") == origin), None)
        if final is None and album:
            # an album with identical files exists: nothing is uploaded (two rips of one record are not recognized)
            wanted = manifests.identity(entry["manifest"])
            final = next(
                (i for i, target in targets.items() if target.get("album", {}).get("identity") == wanted), None
            )
            if final is not None:
                self._logger.info("box %s: album %s has the files of album %s", box_id, entry["key"], final)
        elif final is None and not _is_album(value):
            same_station = (value["name"], value["url"])
            final = next(
                (
                    i
                    for i, target in targets.items()
                    if target["value"] is not None
                    and (target["value"].get("name"), target["value"].get("url")) == same_station
                ),
                None,
            )
        if final is None and album:
            staged = box["staging"].setdefault(str(number), {"value": value, "manifest": entry["manifest"]})
            box["provisional"]["target"] = max(box["provisional"].get("target", 0), number)
            still = manifests.missing(self._staging_folder(box, str(number)), staged["manifest"])
            if still:
                raise _Waiting(still)
            final = self._finish_album(box, str(number), entry["by"])
        elif final is None:
            final = self.new_target_id()
            targets[final] = {
                "value": {**value, "target-id": final},
                "rev": 1,
                "by": box_id,
                "since": 0,
                "who": entry["by"],
                "when": timestamp(),
                "origin": origin,
            }
            self._save_catalog("target")
            self._dirty = True
        self._ids[key] = final
        write_json(self._path("ids.json"), self._ids)
        box["provisional"]["target"] = max(box["provisional"].get("target", 0), number)
        return {"result": "applied", "id": final, "value": targets[final]["value"]}

    def _free_folder(self, wanted: str) -> str:
        """A folder name for a new album: two boxes may have uploaded different albums under the same name."""
        known = {
            entry["value"]["album-folder"] for entry in self._entries("target").values() if _is_album(entry["value"])
        }
        name, count = wanted, 1
        while name in known or os.path.exists(os.path.join(self.albums_folder, name)):
            count += 1
            name = f"{wanted} ({count})"
        return name

    def _finish_album(self, box: dict[str, Any], number: str, who: str) -> str:
        """Every file is there and verified: the album moves into the library and gets its final id."""
        staged = box["staging"][number]
        final = self.new_target_id()
        folder = self._free_folder(os.path.normpath(staged["value"]["album-folder"]))
        target = os.path.join(self.albums_folder, folder)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(self._staging_folder(box, number), target)
        value = {**staged["value"], "target-id": final, "album-folder": folder}
        entry = {
            "value": None,
            "rev": 1,
            "by": box["id"],
            "since": 0,
            "who": who,
            "when": timestamp(),
            "origin": sha256_bytes(f"{box['id']}/target/{number}".encode())[:16],
        }
        self._put("target", final, entry, value, self._describe_album(value, staged["manifest"]))
        self._entries("target")[final] = entry
        self._save_catalog("target")
        del box["staging"][number]
        self._dirty = True
        self._logger.info("album %s of box %s is complete: album %s in '%s'", number, box["id"], final, folder)
        return final

    def _apply_waiting(self, box: dict[str, Any]) -> None:
        """Changes that waited for an album are applied now, in their order."""
        for number in sorted(box["waiting"], key=int):
            self._answer(box, box["waiting"][number])

    def _apply_all_waiting(self) -> None:
        for box in self._boxes.values():
            if box["waiting"]:
                self._apply_waiting(box)
                self.save_box(box)

    # --- albums: files with a manifest (section 4.8) -----------------------------------------------------------------

    def _album(self, album_id: str) -> dict[str, Any]:
        entry = self._entries("target").get(album_id)
        if entry is None or not _is_album(entry["value"]):
            raise Unknown(f"album {album_id}")
        return entry  # type: ignore[no-any-return]

    def manifest(self, album_id: str) -> dict[str, Any]:
        with self.lock:
            entry = self._album(album_id)
            manifest = read_json(self._path("manifests", f"{album_id}.json"))
            if manifest is None:
                raise Unknown(f"manifest of album {album_id}")
            return {"id": album_id, **manifest, "lacks": entry["album"]["lacks"]}

    def album_file(self, album_id: str, name: str) -> str:
        manifest = self.manifest(album_id)
        with self.lock:
            folder = os.path.join(self.albums_folder, self._album(album_id)["value"]["album-folder"])
        filename = manifests.path(folder, name)
        if name not in [entry["name"] for entry in manifest["files"]] or not os.path.isfile(filename):
            raise Unknown(f"file {name} of album {album_id}")
        return filename

    def receive_file(self, box_id: str, album: str, name: str, save: Callable[[str], str]) -> dict[str, Any]:
        """A file that a box sends: for an album it is uploading, or one that the library lacks.

        ``save`` stores the file under the name it is given and returns its checksum. The file gets its real name only
        after the checksum was verified.
        """
        with self.lock:
            box = self._boxes.get(box_id, {"staging": {}})  # not a box: the first filling
            number = self._provisional(box_id, album)
            staged = number is not None and str(number) in box["staging"]
            if staged:
                folder, manifest = self._staging_folder(box, str(number)), box["staging"][str(number)]["manifest"]
            elif album in self._entries("target") and _is_album(self._entries("target")[album]["value"]):
                folder = os.path.join(self.albums_folder, self._album(album)["value"]["album-folder"])
                manifest = self.manifest(album)
            else:
                raise Unknown(f"album {album}")
            entry = next((entry for entry in manifest["files"] if entry["name"] == name), None)
            if entry is None:
                raise Unknown(f"file {name}")
        filename = manifests.path(folder, name)
        temporary = os.path.join(os.path.dirname(filename), f".{os.path.basename(filename)}.part")
        checksum = save(temporary)
        with self._section():
            if checksum != entry["sha256"]:
                os.remove(temporary)
                self._logger.warning("%s: file %s of album %s has a wrong checksum, discarded", box_id, name, album)
                raise Invalid("wrong checksum")
            os.replace(temporary, filename)
            self._logger.info("%s: file %s of album %s received", box_id, name, album)
            if staged:
                still = self._staging_missing(box, str(number))
                if not still:
                    final = self._finish_album(box, str(number), box_id)
                    self._ids[f"{box_id}/target/{number}"] = final
                    write_json(self._path("ids.json"), self._ids)
                    self._apply_waiting(box)
                    self.save_box(box)
                return {"missing": still}
            self.verify_albums()
            return {"missing": self._album(album)["album"]["lacks"]}

    def verify_albums(self) -> None:
        """Note in the catalog which files the library itself lacks; a box that has them sends them."""
        with self.lock:
            changed = False
            for album_id, entry in self._entries("target").items():
                if not _is_album(entry["value"]) or "album" not in entry:
                    continue
                manifest = read_json(self._path("manifests", f"{album_id}.json"))
                if manifest is None:
                    continue
                lacks = manifests.missing(os.path.join(self.albums_folder, entry["value"]["album-folder"]), manifest)
                if lacks != entry["album"]["lacks"]:
                    entry["album"]["lacks"] = lacks
                    entry["rev"] += 1
                    changed = True
                    if lacks:
                        self._logger.warning("album %s lacks %s", album_id, lacks)
                changed = self._check(album_id, entry, manifest) or changed
            if changed:
                self._save_catalog("target")

    # --- broken files ------------------------------------------------------------------------------------------------

    def _check(self, key: str, entry: dict[str, Any], manifest: dict[str, Any]) -> bool:
        """Read the tracks of an album - once for every state of its files, as soon as the library has all of them.
        The catalog gets the number of the tracks and their playing time, so a box knows them of an album that it
        has not fetched. Tracks that are no valid mp3 files are noted; what was noted about a file that was replaced
        since is dropped. Returns whether the catalog changed."""
        album = entry["album"]
        state = self._broken.get(key, {"checksum": None, "files": {}})
        if album["lacks"] or (state["checksum"] == album["checksum"] and "length" in album):
            return False
        folder = os.path.join(self.albums_folder, entry["value"]["album-folder"])
        checksums = {item["name"]: item["sha256"] for item in manifest["files"]}
        files = {name: found for name, found in state["files"].items() if found["sha256"] == checksums.get(name)}
        length = 0.0
        for name in manifest["order"]:
            try:
                length += mp3_info(manifests.path(folder, name))[0]
            except mutagen.MutagenError:
                found = files.setdefault(name, {"sha256": checksums[name], "boxes": {}})
                if "library" not in found:
                    self._logger.warning("album %s: file %s is %s", key, name, NO_MP3)
                found["library"] = NO_MP3
        album.update({"tracks": len(manifest["order"]), "length": round(length, 2)})
        self._broken[key] = {"checksum": album["checksum"], "files": files}
        write_json(self._path("broken.json"), self._broken)
        return True

    def reported(self, box_id: str, line: dict[str, Any]) -> None:
        """The decoder of a box found a file damaged (line ``broken`` of its journal). Noted for the file as the
        library has it: the files of a box are the ones of the library, compared by their checksums. The time is the
        one of the box if its clock was synchronized, otherwise the time the line arrived."""
        with self.lock:
            name, error, clock = line.get("file"), str(line.get("error") or ""), line.get("clock") or {}
            try:
                key = self._reference(self.box(box_id), line.get("album"))
                manifest = self.manifest(key)
            except (Unknown, Invalid, _Waiting):
                key, manifest = str(line.get("album")), {"files": []}
            item = next((item for item in manifest["files"] if item["name"] == name), None)
            self._logger.warning("box %s found file %s of album %s damaged: %s", box_id, name, key, error)
            if item is None:
                return  # an album or a file that the library does not have (yet)
            state = self._broken.setdefault(key, {"checksum": None, "files": {}})
            found = state["files"].setdefault(name, {"sha256": item["sha256"], "boxes": {}})
            when = clock.get("time") if clock.get("synchronized") else None
            found["boxes"][box_id] = {"when": when or timestamp(), "error": error}
            write_json(self._path("broken.json"), self._broken)

    def broken(self, key: str | None = None) -> dict[str, dict[str, Any]]:
        """The broken files of every album that has some - or of the album ``key`` only - by album and name:
        ``library`` is what the library found itself (missing if nothing), ``boxes`` what boxes reported."""
        with self.lock:
            result = {}
            for album_id, state in self._broken.items():
                if state["files"] and key in (None, album_id):
                    result[album_id] = {
                        name: {field: value for field, value in found.items() if field != "sha256"}
                        for name, found in state["files"].items()
                    }
            return result

    def forget_reports(self, key: str) -> None:
        """Drop what boxes reported about the files of an album, e.g. after a single failure of a box that says
        nothing about the file. What the library found itself stays."""
        with self.lock:
            self._album(key)
            state = self._broken.get(key)
            if state is None:
                return
            for name in list(state["files"]):
                state["files"][name]["boxes"] = {}
                if "library" not in state["files"][name]:
                    del state["files"][name]
            write_json(self._path("broken.json"), self._broken)

    def uploads(self) -> list[dict[str, Any]]:
        """Albums that are on their way from a box, e.g. "only on box 1, 7 of 12 files transferred"."""
        with self.lock:
            result = []
            for box in self._boxes.values():
                for number, staged in box["staging"].items():
                    total = len(staged["manifest"]["files"])
                    result.append({
                        "box": box["id"], "name": self.box_name(box), "id": f"{box['id']}-{number}",
                        "folder": staged["value"]["album-folder"], "files": total,
                        "transferred": total - len(self._staging_missing(box, number)),
                    })  # fmt: skip
            return result

    # --- covers of albums and stations (section 4.8) ------------------------------------------------------------------

    def _take_over_covers(self) -> None:
        """Until 0.31 the cover of an album was a file of the album, ``cover.jpg`` or ``cover.png`` in its folder,
        which only a backup with the albums holds. Move the stored cover of every album that names no cover into
        the images folder. The file leaves the manifest of the album in the same step: the album has a new checksum,
        and a box that takes it over removes its own stored cover and fetches nothing else. No track is read for
        it."""
        moved = []
        for key, entry in self._entries("target").items():
            value = entry["value"]
            if not _is_album(value) or value.get("cover"):
                continue
            folder = os.path.join(self.albums_folder, value["album-folder"])
            stored = stored_cover(folder)
            if stored is None:
                continue
            try:
                with open(stored, "rb") as infile:
                    data = infile.read()
                extension = image_extension(data)
                if extension is None:
                    raise ValueError("neither a jpeg nor a png image")
                name = f"{ALBUM_PREFIX}{secrets.token_hex(4)}{extension}"
                write_atomic(os.path.join(self.images_folder, name), data)
            except (OSError, ValueError) as error:
                self._logger.warning("the cover '%s' stays where it is: %s", stored, error)
                continue
            value = {**value, "cover": name}
            manifest = read_json(self._path("manifests", f"{key}.json"))
            album = None
            if manifest is not None and "album" in entry:
                before = entry["album"]
                gone = os.path.relpath(stored, folder).replace(os.sep, "/")
                manifest = {**manifest, "files": [item for item in manifest["files"] if item["name"] != gone]}
                album = self._describe_album(value, manifest)
                state = self._broken.get(key)
                if album is not None and "length" in before and state and state["checksum"] == before["checksum"]:
                    # the tracks are what they were: what was read from them stays
                    album.update({"tracks": before["tracks"], "length": before["length"]})
                    state["checksum"] = album["checksum"]
            _changed(entry, LIBRARY, entry["rev"], SOFTWARE)
            self._put("target", key, entry, value, album)
            moved.append(stored)
        if moved:
            write_json(self._path("broken.json"), self._broken)
            self._save_catalog("target", quiet=True)
            for stored in moved:  # only now: the catalog names the images
                os.remove(stored)
            self._logger.info(
                "%d covers of albums moved from their album folders to '%s'", len(moved), self.images_folder
            )

    def _take_over_images(self) -> dict[str, Any]:
        """Until 0.28 the list of the images was part of ``cards.json``, the catalog file of the card images of tags.
        Its covers of stations become the list; the file goes."""
        old = self._path("catalog", "cards.json")
        images = {name: image for name, image in read_json(old, {}).get("images", {}).items() if is_cover(name)}
        with contextlib.suppress(FileNotFoundError):
            os.remove(old)
        return {"format": FORMAT, "images": images}

    def _save_images(self) -> None:
        write_json(self._path("catalog", IMAGES), self._images)
        self._notify("catalog")

    def scan_images(self) -> None:
        """Bring the list of the covers in line with the folder. An image that was removed stays as a mark, so that a
        box removes it too instead of sending it again."""
        with self.lock:
            images = self._images["images"]
            found = {}
            for name in sorted(os.listdir(self.images_folder)):
                if is_cover(name):
                    filename = os.path.join(self.images_folder, name)
                    known = images.get(name, {})
                    size = os.path.getsize(filename)
                    checksum = known["sha256"] if known.get("size") == size else sha256_file(filename)
                    found[name] = {"sha256": checksum, "size": size}
            for name in images:
                if name not in found:
                    found[name] = {"deleted": True}
            if found != images:
                self._images["images"] = found
                self._save_images()

    def image_file(self, name: str) -> str:
        with self.lock:
            if "sha256" not in self._images["images"].get(name, {}):
                raise Unknown(f"image {name}")
            return os.path.join(self.images_folder, name)

    def receive_image(self, box_id: str, name: str, save: Callable[[str], str]) -> None:
        """The cover of an album or a station that was set on a box."""
        if name != os.path.basename(name) or not is_cover(name):
            raise Invalid(f"'{name}' is not the name of a cover")
        with self.lock:
            if name in self._images["images"]:
                return  # the library has it, or removed it on purpose
        save(os.path.join(self.images_folder, f".{name}.part"))
        with self._section():
            os.replace(os.path.join(self.images_folder, f".{name}.part"), os.path.join(self.images_folder, name))
            self._logger.info("%s: cover %s received", box_id, name)
            self.scan_images()
            self._dirty = True

    # --- versions of the box software and their states (section 6.2) -------------------------------------------------

    def versions(self) -> dict[str, Any]:
        with self.lock:
            return {version: dict(entry) for version, entry in self._versions.items()}

    def add_version(self, description: dict[str, Any], files: dict[str, str], by: str) -> bool:
        """Put a slot into the archive; files that lie in the folder of the archive already stay where they are.
        A new version starts in the state ``use``."""
        with self.lock:
            version = description["version"]
            new = version not in self._versions
            for kind, source in files.items():
                target = self._path("archive", os.path.basename(source))
                if os.path.abspath(source) != target:
                    # never written in place: a file of the archive is replaced in one step
                    shutil.copyfile(source, f"{target}.tmp")
                    os.replace(f"{target}.tmp", target)
                files[kind] = os.path.basename(source)
            entry = self._versions.setdefault(
                version, {"state": "use", "rev": 1, "by": by, "when": timestamp(), "why": "new release"}
            )
            entry.update({"description": description, "files": files})
            write_json(self._path("versions.json"), self._versions)
            if new:
                self._logger.info("version %s is in the archive, state %s", version, entry["state"])
                self.journal.record(
                    "version", None, {"version": version, "state": entry["state"], "by": by, "why": "new release"}
                )
        if new:
            self._notify("versions")
        return new

    def version_file(self, version: str, kind: str) -> str:
        with self.lock:
            entry = self._versions.get(version)
            if entry is None or kind not in entry.get("files", {}):
                raise Unknown(f"version {version}")
            return self._path("archive", entry["files"][kind])

    def drop_version_files(self, version: str) -> None:
        """Remove a version from the archive; its entry and its state stay, the files can be fetched again."""
        with self.lock:
            entry = self._versions[version]
            for name in entry["files"].values():
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self._path("archive", name))
            entry["files"] = {}
            write_json(self._path("versions.json"), self._versions)

    def set_version(self, version: str, state: str, by: str, why: str) -> None:
        """Set the state of a version by hand. Who set it, when and why is recorded."""
        with self.lock:
            entry = self._versions.get(version)
            if entry is None or state not in STATES:
                raise Unknown(f"version {version} / state {state}")
            if entry["state"] == state:
                return
            entry.update({"state": state, "rev": entry["rev"] + 1, "by": by, "when": timestamp(), "why": why})
            write_json(self._path("versions.json"), self._versions)
            self._logger.info("version %s is %s now (%s: %s)", version, state, by, why)
            self.journal.record("version", None, {"version": version, "state": state, "by": by, "why": why})
        self._notify("versions")

    def box_left_version(self, box: dict[str, Any], version: str, base: int, why: str) -> None:
        """A box went back from a version: the version is ``manual`` for all boxes (section 6.2).

        If the library set the version differently in the meantime, the library wins. A box that has never seen
        the list of the versions (``base`` 0) cannot know of that; it went back all the same.
        """
        with self.lock:
            entry = self._versions.get(version)
            if entry is None:
                return
            if entry["state"] == "use" and base in (entry["rev"], 0):
                self.set_version(version, "manual", f"box {self.box_name(box)} ({box['id']})", why)
            else:
                self._logger.info(
                    "box %s went back from version %s; the library has it as %s already",
                    box["id"],
                    version,
                    entry["state"],
                )

    # --- status of a box, data of a defective version ----------------------------------------------------------------

    def report(self, box_id: str, status: dict[str, Any]) -> dict[str, Any]:
        """What a box reports with every contact: what runs, its switches of version, versions it went back from."""
        with self.lock:
            box = self.box(box_id)
            lines = status.pop("history", [])
            if lines and lines[0]["n"] == 1 and box["history"] >= lines[0]["n"]:
                # the switches of the box begin anew (a card set up again): its numbers start at 1 again
                self._logger.info("box %s: its list of switches begins anew", box_id)
                box["history"] = 0
            for line in lines:
                if line["n"] > box["history"]:
                    box["history"] = line["n"]
                    self.journal.record("switch", box_id, line)
                    self._logger.info("box %s switched: %s", box_id, line)
            left = status.pop("left", [])
            for item in left:
                self.box_left_version(box, item["version"], item["base"], item.get("why", ""))
            defective = status.pop("defective", [])
            for version in defective:
                self.discard(box, version)
            box["status"] = status
            self.save_box(box)
            return {"history": box["history"], "left": [item["version"] for item in left], "defective": defective}

    def discard(self, box: dict[str, Any], version: str) -> None:
        """Do not trust what a defective version wrote by itself: the positions go back to the state before."""
        if version in box["defective"]:
            return
        box["defective"].append(version)
        restored = []
        for key, position in box["positions"].items():
            if position.get("version") != version:
                continue
            history = position.get("history", [])
            before = history.pop() if history else {"value": None, "version": None}
            # a deliberate change: the library wins against the box
            _changed(position, LIBRARY, position["rev"], f"defective version {version}")
            position.update({"value": before["value"], "version": before["version"]})
            restored.append(key)
        discarded = self.journal.discard(box["id"], version)
        self._logger.warning(
            "box %s reported version %s as defective: %d positions set back, %d journal entries flagged",
            box["id"], version, len(restored), discarded,
        )  # fmt: skip
        self.journal.record("defective", box["id"], {"version": version, "positions": restored})
        self._notify("catalog")
