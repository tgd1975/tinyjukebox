"""What a box stores for the synchronization (docs/developer/library.md, section 4.2).

- The copy: the catalog, and the positions and settings of this box, exactly as the library delivered them.
- The outbox: what was changed on this box and is not confirmed by the library yet, in order.
- What the box works with is the copy with the outbox applied on top (:meth:`BoxData.working`). The json files of
  ``tinyjukebox.data`` are generated from it and can be deleted.
- The journal: lines that are only appended, numbered, and deleted when the library has confirmed them (section 7).

The lock is never held while ``tinyjukebox.data`` is called; ``changed`` announces that the working data differs.
"""

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from tinyjukebox.data.common import is_unmapped
from tinyjukebox.sync import KINDS, SOFTWARE, actor
from tinyjukebox.sync.util import read_json, sha256_bytes, timestamp, write_atomic, write_json

__all__ = ["CATALOG_FILES", "COPY", "IMAGES", "JOURNAL_LIMIT", "BoxData", "Replaced"]

COPY = {"tag": "tags.json", "target": "targets.json"}
IMAGES = "images.json"  # the covers of the stations that the library has: no entries, a list of files
CATALOG_FILES = (*COPY.values(), IMAGES)
STATE = "box.json"  # positions and settings of this box
JOURNAL_LIMIT = 1000  # lines a box keeps that has no library, like a log file
STARTED = time.monotonic()


def _lines(filename: str) -> list[dict[str, Any]]:
    try:
        with open(filename, encoding="utf-8") as infile:
            return [json.loads(line) for line in infile if line.strip()]
    except FileNotFoundError:
        return []


def _write_lines(filename: str, lines: list[dict[str, Any]]) -> None:
    write_atomic(filename, "".join(json.dumps(line) + "\n" for line in lines))


def _append(filename: str, line: dict[str, Any]) -> None:
    with open(filename, "a", encoding="utf-8") as outfile:
        outfile.write(json.dumps(line) + "\n")
        outfile.flush()
        os.fsync(outfile.fileno())


class Replaced:
    """What a new file of the copy means for the box."""

    def __init__(self) -> None:
        self.lost: list[tuple[str, str, Any, int]] = []  # (kind, key, value, revision) the library lost
        self.removed: list[dict[str, Any]] = []  # targets that the library deleted, as the box had them


class BoxData:
    def __init__(
        self,
        folder: str,
        box_id: str,
        version: str,
        logger: logging.Logger,
        clock_synchronized: Callable[[], bool] = lambda: True,
        changed: Callable[[], None] | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.box_id = box_id
        self.version = version
        self.changed = changed
        self._logger = logger
        self._clock_synchronized = clock_synchronized
        self._folder = folder
        os.makedirs(os.path.join(folder, "copy"), exist_ok=True)
        self._copy: dict[str, Any] = {}
        self._raw: dict[str, bytes] = {}
        for name in (*CATALOG_FILES, STATE):
            self._load_copy(name)
        with contextlib.suppress(FileNotFoundError):  # until 0.28: the card images of tags and the list of the images
            os.remove(os.path.join(folder, "copy", "cards.json"))
        self._outbox = _lines(self._file("outbox"))
        self._journal = _lines(self._file("journal"))
        # the last numbers this box assigned: a number is never assigned twice
        self.numbers: dict[str, int] = {
            "outbox": 0, "target": 0, "journal": 0, "start": 0, "history": 0,
            **read_json(self._file("numbers.json"), {}),
        }  # fmt: skip
        self.fresh = not os.path.exists(self._file("numbers.json"))  # nothing was ever stored here
        self._ids: dict[str, str] = read_json(self._file("ids.json"), {})  # provisional id -> final id, while needed
        self.numbers["start"] += 1
        self._save_numbers()

    def _file(self, name: str) -> str:
        return os.path.join(self._folder, name)

    def _save_numbers(self) -> None:
        write_json(self._file("numbers.json"), self.numbers)

    def _tell(self) -> None:
        if self.changed is not None:
            self.changed()

    # --- copy --------------------------------------------------------------------------------------------------------

    def _load_copy(self, name: str) -> None:
        try:
            with open(os.path.join(self._folder, "copy", name), "rb") as infile:
                self._raw[name] = infile.read()
            self._copy[name] = json.loads(self._raw[name])
        except FileNotFoundError:
            self._raw[name] = b""
            self._copy[name] = {"positions": {}, "settings": {}} if name == STATE else {"entries": {}}

    def _copied(self, kind: str) -> dict[str, Any]:
        """The entries of ``kind`` in the copy, each with ``value`` and ``rev``."""
        if kind in COPY:
            return self._copy[COPY[kind]]["entries"]  # type: ignore[no-any-return]
        return self._copy[STATE][f"{kind}s"]  # type: ignore[no-any-return]

    def checksum(self, name: str) -> str:
        with self.lock:
            return sha256_bytes(self._raw[name])

    def replace_copy(self, name: str, data: bytes) -> Replaced:
        """Store a file as the library delivered it. The outbox stays on top, so no own change is lost.

        For the positions and settings of this box the result lists what the library has with a lower revision than
        the copy had: the library lost data, e.g. it was restored from a backup, and the box wins (section 4.12).
        """
        result = Replaced()
        with self.lock:
            old = self._copy[name]
            json.loads(data)  # a file that is not JSON never becomes the copy
            write_atomic(os.path.join(self._folder, "copy", name), data)
            self._load_copy(name)
            new = self._copy[name]
            if name == STATE:
                for kind in ("position", "setting"):
                    for key, entry in old[f"{kind}s"].items():
                        if new[f"{kind}s"].get(key, {"rev": 0})["rev"] < entry["rev"]:
                            result.lost.append((kind, key, entry["value"], entry["rev"]))
                # the highest provisional number the library knows of this box
                self.numbers["target"] = max(self.numbers["target"], new.get("provisional", {}).get("target", 0))
                self._save_numbers()
            elif name == COPY["target"]:
                for key, entry in old["entries"].items():
                    if entry["value"] is not None and new["entries"].get(key, {"value": None})["value"] is None:
                        result.removed.append(entry["value"])
        self._tell()
        return result

    def album(self, key: str) -> dict[str, Any] | None:
        """What the catalog says about the files of an album: identity, number and size, what the library lacks."""
        with self.lock:
            entry = self._copied("target").get(key)
            return dict(entry["album"]) if entry is not None and "album" in entry else None

    def images(self) -> dict[str, Any]:
        """The covers of the stations that the library has: checksum and size each, or the mark of a removed one."""
        with self.lock:
            return dict(self._copy[IMAGES].get("images", {}))

    # --- outbox ------------------------------------------------------------------------------------------------------

    def new_target_id(self) -> str:
        """A provisional id: the internal id of the box and a running number (section 4.6)."""
        with self.lock:
            self.numbers["target"] += 1
            self._save_numbers()
            return f"{self.box_id}-{self.numbers['target']}"

    def is_provisional(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(f"{self.box_id}-")

    def add(
        self, kind: str, key: str, value: Any, by: str | None = None, base: int | None = None, **fields: Any
    ) -> dict[str, Any]:
        """A change made on this box: the entry ``key`` of ``kind`` is ``value`` now (None: deleted).

        The entry carries a running number, the revision it is based on, who made it and with which version. An
        earlier change of the same entry that is still in the outbox is replaced. A target that the library has
        never seen is created there, with its provisional id.

        ``fields`` are stored with the entry in the same step, e.g. the manifest of an album whose files changed: a
        pass that runs at this moment never sends the entry without it. A manifest that an earlier change of the
        entry carries and that was not sent yet stays with the entry.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown kind '{kind}'")
        with self.lock:
            copied = self._copied(kind).get(self.final(key))
            created = [entry for entry in self._outbox if entry["op"] == "create" and entry["key"] == key]
            creates = kind == "target" and copied is None and not created
            if creates and value is None:
                return {}
            before = len(self._outbox)
            if created and value is None:
                # created and deleted before the library heard of it: nothing is left to tell
                self._outbox = [e for e in self._outbox if (e["kind"], e["key"]) != (kind, key)]
                _write_lines(self._file("outbox"), self._outbox)
                return {}
            replaced = [e for e in self._outbox if (e["op"], e["kind"], e["key"]) == ("set", kind, key)]
            if replaced and "manifest" in replaced[-1] and value is not None:
                fields.setdefault("manifest", replaced[-1]["manifest"])
            self._outbox = [e for e in self._outbox if (e["op"], e["kind"], e["key"]) != ("set", kind, key)]
            self.numbers["outbox"] += 1
            self._save_numbers()
            entry = {
                "n": self.numbers["outbox"], "op": "create" if creates else "set", "kind": kind, "key": key,
                "value": value, "by": by or actor(), "version": self.version,
                "clock": {"time": timestamp(), "synchronized": self._clock_synchronized()},
            }  # fmt: skip
            if not creates:
                entry["base"] = (copied["rev"] if copied else 0) if base is None else base
            entry.update(fields)
            self._outbox.append(entry)
            if len(self._outbox) == before + 1:
                _append(self._file("outbox"), entry)
            else:
                _write_lines(self._file("outbox"), self._outbox)
            return entry

    def clear_outbox(self) -> None:
        with self.lock:
            self._outbox = []
            _write_lines(self._file("outbox"), self._outbox)

    def forget_library(self) -> None:
        """The box leaves its library: the copy, the outbox and the ids that wait to be settled go. The numbers
        stay - a number is never assigned twice, also not for a library the box meets later."""
        with self.lock:
            for name in (*CATALOG_FILES, STATE):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(self._folder, "copy", name))
                self._load_copy(name)
            self._ids = {}
            write_json(self._file("ids.json"), self._ids)
            self.clear_outbox()

    def outbox(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(entry) for entry in self._outbox]

    def amend(self, number: int, **fields: Any) -> None:
        """Add something to an entry that is not sent yet, e.g. the manifest of a new album."""
        with self.lock:
            for entry in self._outbox:
                if entry["n"] == number:
                    entry.update(fields)
            _write_lines(self._file("outbox"), self._outbox)

    def confirm(self, numbers: set[int]) -> None:
        """The library answered these entries, and the copy has what it made of them: they leave the outbox."""
        with self.lock:
            if not numbers:
                return
            self._outbox = [entry for entry in self._outbox if entry["n"] not in numbers]
            _write_lines(self._file("outbox"), self._outbox)
            self._forget_ids()
        self._tell()

    def discard_version(self, version: str) -> list[dict[str, Any]]:
        """Drop what a defective version left in the outbox; return what a person had entered with it (4.11)."""
        with self.lock:
            dropped = [entry for entry in self._outbox if entry["version"] == version]
            self.confirm({entry["n"] for entry in dropped})
            return [entry for entry in dropped if entry["by"] != SOFTWARE]

    # --- provisional ids ---------------------------------------------------------------------------------------------

    def final(self, value: Any) -> Any:
        """The final id behind a provisional one, as far as the library told it."""
        return self._ids.get(value, value) if isinstance(value, str) else value

    def _with_final_ids(self, kind: str, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if kind == "target":
            return {**value, "target-id": self.final(value["target-id"])}
        if kind == "tag" and not is_unmapped(value["target"].get("id")):
            return {**value, "target": {**value["target"], "id": self.final(value["target"]["id"])}}
        if kind == "position":
            return {**value, "target-id": self.final(value["target-id"])}
        return value

    def learn(self, provisional: str, final: str, value: dict[str, Any] | None = None) -> None:
        """Apply the final id that the library assigned, and what it made of the target (the folder of an album may
        have another name there). The entry that created it keeps its provisional id on the way to the library."""
        with self.lock:
            self._ids[provisional] = final
            write_json(self._file("ids.json"), self._ids)
            if value is not None:
                for entry in self._outbox:
                    if entry["op"] == "create" and entry["key"] == provisional:
                        entry["value"] = {**value, "target-id": provisional}
                _write_lines(self._file("outbox"), self._outbox)
        self._tell()

    def _forget_ids(self) -> None:
        used = self.provisional_ids(settled=True)
        self._ids = {provisional: final for provisional, final in self._ids.items() if provisional in used}
        write_json(self._file("ids.json"), self._ids)

    def provisional_ids(self, settled: bool = False) -> list[str]:
        """Provisional ids that still occur in the outbox: not settled with the library yet (``settled``: also the
        ones whose final id is known)."""
        with self.lock:
            found: dict[str, None] = {}
            for entry in self._outbox:
                value = entry.get("value")
                candidates: list[Any] = [entry["key"] if entry["kind"] == "target" else None]
                if isinstance(value, dict) and entry["kind"] == "tag":
                    candidates.append(value["target"].get("id"))
                elif isinstance(value, dict) and entry["kind"] == "position":
                    candidates.append(value["target-id"])
                for candidate in candidates:
                    if self.is_provisional(candidate) and (settled or candidate not in self._ids):
                        found[str(candidate)] = None
            return list(found)

    # --- what the box works with -------------------------------------------------------------------------------------

    def working(self, kind: str) -> dict[str, Any]:
        """The entries of ``kind`` the box works with: the copy with the outbox applied on top."""
        with self.lock:
            result = {key: entry["value"] for key, entry in self._copied(kind).items() if entry["value"] is not None}
            for entry in self._outbox:
                if entry["kind"] != kind:
                    continue
                key = self.final(entry["key"]) if kind == "target" else entry["key"]
                if entry["value"] is None:
                    result.pop(key, None)
                else:
                    result[key] = self._with_final_ids(kind, entry["value"])
            return result

    def renamed(self) -> dict[str, str]:
        """Provisional ids of targets and the final ids that replace them in the working data."""
        with self.lock:
            return dict(self._ids)

    # --- journal -----------------------------------------------------------------------------------------------------

    def journal(self, event: str, **fields: Any) -> None:
        """Append a line. It carries the start of the box, a running number, the time since the start, and whether
        the clock was synchronized - order is never decided by the clock."""
        with self.lock:
            self.numbers["journal"] += 1
            self._save_numbers()
            line = {
                "n": self.numbers["journal"], "start": self.numbers["start"],
                "uptime": round(time.monotonic() - STARTED, 1),
                "clock": {"time": timestamp(), "synchronized": self._clock_synchronized()}, "version": self.version,
                "event": event, **fields,
            }  # fmt: skip
            self._journal.append(line)
            with open(self._file("journal"), "a", encoding="utf-8") as outfile:
                outfile.write(json.dumps(line) + "\n")

    def journal_lines(self, limit: int | None = 200) -> list[dict[str, Any]]:
        """The oldest lines the box still has; ``None``: all of them."""
        with self.lock:
            return self._journal[:limit]

    def journal_confirmed(self, number: int) -> None:
        """The library has the lines up to ``number``: they are deleted here."""
        with self.lock:
            self._journal = [line for line in self._journal if line["n"] > number]
            _write_lines(self._file("journal"), self._journal)

    def journal_limit(self) -> None:
        """Without a library the journal is kept short, like a log file."""
        with self.lock:
            if len(self._journal) > JOURNAL_LIMIT:
                self.journal_confirmed(self._journal[-JOURNAL_LIMIT]["n"] - 1)

    def history_reported(self, number: int) -> None:
        with self.lock, contextlib.suppress(ValueError):
            self.numbers["history"] = int(number)
            self._save_numbers()
