"""A box that works with a library (docs/developer/library.md): what it stores, the connection, the files.

``BoxSync`` connects the synchronization with the data of the box (``tinyjukebox.data``):

- every change of tags, targets, activities and volume limits - whoever made it - is recorded in
  the outbox,
- what the library delivers is put into the same collections, and the tag on the box is evaluated again if it is
  concerned.

A box works with a library as soon as the address of one was entered (file ``library`` in the folder ``identity``).
From then on the json files are generated from what the library delivered and the changes of the box. A box without
a library is not touched: its json files stay what they are. A box that is unlinked from its library
(:meth:`BoxSync.unlink`) is such a box again, with everything it has on its card.
"""

import contextlib
import json
import logging
import os
import secrets
import socket
import threading
from collections.abc import Callable
from typing import Any, Protocol

from tinyjukebox.core import unexpected
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.targets import LAST_ID_SUFFIX, Targets
from tinyjukebox.data.volumelimits import VolumeLimits
from tinyjukebox.images import Images
from tinyjukebox.slots import Health, Slots
from tinyjukebox.sync import INTERFACE, acting_as
from tinyjukebox.sync.data import BoxData
from tinyjukebox.sync.files import AlbumFiles, ImageFiles
from tinyjukebox.sync.link import FITS, Link
from tinyjukebox.sync.util import clock_synchronized, read_json, write_atomic, write_json

__all__ = ["MIGRATION", "BoxSync", "Playback"]

MIGRATION = "migration"  # who made the changes that stand for what a box had before it met a library
VOLUME = "volume."  # settings of a box: the volume limits of a profile, e.g. "volume.speaker"
ALIVE_SECONDS = 600.0  # a journal line this often: switching the box off leaves none (section 7)


class Playback(Protocol):
    """What the synchronization needs of :class:`~tinyjukebox.stm.stmoperation.STMOperation`."""

    def get_current_uid(self) -> str | None: ...

    def get_album(self) -> Any: ...

    def recheck_tag(self) -> None: ...


def _why_left(history: list[dict[str, Any]], version: str) -> str:
    """Why the box went back from ``version``: the last switch away from it, with its details - e.g. "health check
    failed: stage 1 of the health check not passed within 120 s". Empty if the list of switches does not say."""
    for line in reversed(history):
        if str(line.get("from", "")).endswith(f":{version}") and not str(line.get("to", "")).endswith(f":{version}"):
            return ": ".join(part for part in (line.get("why"), line.get("details")) if part)
    return ""


class BoxSync:
    def __init__(
        self,
        folder: str,
        identity: str,
        version: str,
        logger: logging.Logger,
        albums_folder: str,
        images_folder: str,
        retry_seconds: float = 30.0,
        keep_free: int = 0,
    ) -> None:
        """``folder``: copy, outbox and journal; ``identity``: what identifies this box - its id, its key, address
        and certificate of the library. No version of the box software overwrites ``identity``."""
        self.logger = logger
        self.identity = identity
        self.version = version
        self.retry_seconds = retry_seconds
        self.fit = FITS
        self._folder = folder
        self.data = BoxData(folder, self._box_id(), version, logger, clock_synchronized, self._working_changed)
        self.link = Link(self)
        self._stopped = threading.Event()
        self.files = AlbumFiles(self, albums_folder, os.path.join(folder, "manifests"), keep_free)
        self.images = ImageFiles(self, images_folder)
        self._tags: Tags | None = None
        self._targets: Targets | None = None
        self._activities: Activities | None = None
        self._limits: VolumeLimits | None = None
        self._playback: Playback | None = None
        self._positions: dict[str, Any] = {}
        self._applying = threading.Lock()
        self.slots: Slots | None = None  # which version runs (tinyjukebox.slots) - None on a box without slots
        self.health: Health | None = None
        self.on_fit: Callable[[], None] | None = None  # box and library fit together, or not any more
        self.fit = self._stored("fit") or FITS

    def _box_id(self) -> str:
        """The internal id of this box: generated on the first start, never changed, typed by nobody (section 4.7)."""
        filename = os.path.join(self.identity, "box-id")
        try:
            with open(filename, encoding="utf-8") as infile:
                return infile.read().strip()
        except FileNotFoundError:
            box_id = "b" + secrets.token_hex(4)
            os.makedirs(self.identity, mode=0o700, exist_ok=True)
            write_atomic(filename, box_id + "\n")
            return box_id

    # --- with or without a library -----------------------------------------------------------------------------------

    def _stored(self, name: str) -> str:
        try:
            with open(os.path.join(self._folder, name), encoding="utf-8") as infile:
                return infile.read().strip()
        except FileNotFoundError:
            return ""

    def _identity(self, name: str) -> str:
        try:
            with open(os.path.join(self.identity, name), encoding="utf-8") as infile:
                return infile.read().strip()
        except FileNotFoundError:
            return ""

    @property
    def library(self) -> str:
        """The address of the library, empty if none is configured."""
        return self._identity("library")

    @property
    def attached(self) -> bool:
        """Whether this box works with a library: its changes are recorded, its json files are generated."""
        return os.path.exists(os.path.join(self._folder, "attached"))

    def set_library(self, url: str, bootstrap: str | None = None) -> None:
        """Enter the address of the library, and the bootstrap password for the first contact. The box works with
        the library from its next start."""
        os.makedirs(self.identity, mode=0o700, exist_ok=True)
        write_atomic(os.path.join(self.identity, "library"), url.strip() + "\n")
        if bootstrap:
            filename = os.path.join(self.identity, "bootstrap")
            write_atomic(filename, bootstrap.strip() + "\n")
            os.chmod(filename, 0o600)
        self.link.wake()

    def unlink(self) -> None:
        """Leave the library (section 4.20). The box keeps what is on its card - its json files are the truth again -
        and forgets the library: address, key, certificate, the copy and the changes that wait. The library is not
        asked; it may be switched off, or not exist any more."""
        self.link.stop()
        with self._applying:
            if self._targets is not None:
                self._targets.id_source = None  # new targets get a number again
            # from here on nothing is recorded, and nothing of the copy is put into the data of the box
            self._tags = self._targets = self._activities = self._limits = None
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(self._folder, "attached"))
        for name in ("library", "key", "library.crt", "bootstrap"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(self.identity, name))
        self.data.forget_library()
        self.files.forget()
        self.data.journal("unlinked")
        self.data.journal_limit()
        self.set_fit(FITS)  # a library that was too old for the box does not stop it any more
        self.logger.info("unlinked from the library: this box works for itself")
        self.look_for_versions()  # from now on they come from the releases (section 6.16)
        if self._playback is not None:
            self._playback.recheck_tag()

    def absent(self) -> int:
        """Albums of which files are missing on this box. Only the library has them."""
        if not self.attached:
            return 0
        return sum(1 for target in self.data.working("target") if self.files.lacking(target) != [])

    def prepare(self, files: dict[str, str]) -> None:
        """Called before the data of the box is loaded. ``files``: the json files of tags (``tag``), targets
        (``target``), activities (``position``) and volume limits (``volume``).

        With a library: on the first start everything the box has becomes a change that the library has not seen
        yet; on every start the json files are generated anew from copy and outbox (section 4.2).
        """
        if not self.library and not self.attached:
            return
        if not self.attached:
            self._attach(files)
        limits = {
            key[len(VOLUME) :]: value for key, value in self.data.working("setting").items() if key.startswith(VOLUME)
        }
        for kind, data in (("tag", None), ("target", None), ("position", None), ("volume", limits)):
            write_json(files[kind], self.data.working(kind) if data is None else data)

    def _attach(self, files: dict[str, str]) -> None:
        """The first start with a library. What the box has may be unknown there, so every target gets a provisional
        id - the old id with the id of the box in front - and is settled like one that was created without a
        connection: the library recognizes an album or a station it has already."""
        self.logger.info("first start with a library: everything on this box is recorded as a change of its own")
        self.data.clear_outbox()
        targets = read_json(files["target"], {})
        used = [int(key) for key in targets if key.isascii() and key.isdigit()]
        last = read_json(os.path.splitext(files["target"])[0] + LAST_ID_SUFFIX, {}).get("last-id", 0)
        number = max([0, *used, last if isinstance(last, int) else 0])
        ids = {}
        for key in targets:
            if not (key.isascii() and key.isdigit()):
                number += 1
            ids[key] = f"{self.data.box_id}-{key if key.isascii() and key.isdigit() else number}"
        self.data.numbers["target"] = number

        def mapped(target_id: Any) -> Any:
            """The new id of a target; a link to a target that does not exist is no link."""
            return ids.get(str(target_id), -1)

        for key, value in targets.items():
            self.data.add("target", ids[key], {**value, "target-id": ids[key]}, by=MIGRATION)
        for key, tag in read_json(files["tag"], {}).items():
            target = tag.get("target", {})
            linked = {**tag, "target": {**target, "id": mapped(target["id"])}} if "id" in target else tag
            self.data.add("tag", key, linked, by=MIGRATION)
        for key, value in read_json(files["position"], {}).items():
            if str(value["target-id"]) in ids:
                self.data.add("position", key, {**value, "target-id": ids[str(value["target-id"])]}, by=MIGRATION)
        for key, value in read_json(files["volume"], {}).items():
            self.data.add("setting", VOLUME + key, value, by=MIGRATION)
        # only now the box counts as attached: a start that was interrupted above begins anew
        write_atomic(os.path.join(self._folder, "attached"), self.data.box_id + "\n")

    def bind(
        self,
        tags: Tags,
        targets: Targets,
        activities: Activities,
        images: Images,
        limits: VolumeLimits,
        playback: Playback | None,
    ) -> None:
        """Connect the data of the box: from now on its changes are recorded, and what the library delivers is put
        into it."""
        self._playback = playback
        if not self.attached:
            return
        self._tags, self._targets, self._activities = tags, targets, activities
        self._limits = limits
        self._positions = dict(self.data.working("position"))
        targets.id_source = self.data.new_target_id
        tags.listen(lambda key, value: self._local("tag", key, value))
        targets.listen(lambda key, value: self._local("target", key, value))
        activities.listen(lambda key, value: self._local("position", key, value))
        images.listen(self.link.wake)
        limits.listen(lambda key, value: self._local("setting", VOLUME + key, value))

    def start(self) -> None:
        if self.attached:
            self.link.start()
            self._stopped.clear()
            threading.Thread(target=self._alive, name="Box.alive", daemon=True).start()
        else:
            self.data.journal_limit()

    def stop(self) -> None:
        self._stopped.set()
        self.link.stop()

    def _alive(self) -> None:
        """The last line of a start tells the library how long the box ran: a line from time to time, so that the
        hours after the last tag count too."""
        while not self._stopped.wait(ALIVE_SECONDS):
            if self.attached:  # not unlinked since the start
                self.journal("alive")

    # --- changes made on this box ------------------------------------------------------------------------------------

    def _local(self, kind: str, key: str, value: Any) -> None:
        """A change of the data of the box: into the outbox. It took effect already, with or without a connection."""
        if not self.attached:
            return  # unlinked since the start
        tell = True
        if kind == "position":
            # the position moves on all the time; the library hears of it with a change of track, a pause or the end
            before, self._positions[key] = self._positions.get(key), value
            tell = (
                before is None
                or value is None
                or any(before.get(field) != value.get(field) for field in ("target-id", "track-id", "album-finished"))
            )
        self.data.add(kind, key, value)
        if tell:
            self.link.wake()

    def journal(self, event: str, **fields: Any) -> None:
        """Note what happened (section 7). Without a library the journal is kept short, like a log file."""
        self.data.journal(event, **fields)
        if not self.attached:
            self.data.journal_limit()
        elif event == "tag" and fields.get("kind") == "album":
            self.files.played(str(fields.get("target")))  # the album played last is the last to make room

    def tell(self) -> None:
        """Something worth telling the library happened, e.g. the playback paused."""
        if self.attached:
            self.link.wake()

    def name(self) -> str | None:
        """The name of this box for people - a box gets one when it works with a library."""
        return self.data.working("setting").get("name") if self.attached else None

    def set_name(self, name: str) -> None:
        self.data.add("setting", "name", name)
        self.link.wake()

    def album_changed(self, album: str) -> None:
        """The files of an album changed on this box, e.g. its playlist was written anew."""
        if self.attached:
            self.files.changed(album)
            self.link.wake()

    def lookup(self, timeout: float = 3.0) -> None:
        """An unknown tag: compare the catalog with the library before it is taken for a new one (section 5)."""
        if self.attached and self.library:
            self.link.wait_pass(timeout)

    # --- what the library delivered ----------------------------------------------------------------------------------

    def _signature(self) -> Any:
        """What the tag on the box plays: if this changes, the tag is evaluated again."""
        uid = self._playback.get_current_uid() if self._playback is not None else None
        if uid is None or self._tags is None or self._targets is None:
            return None
        tag = self._tags.tags.get(uid)
        if tag is None:
            return (uid,)
        target = self._targets.targets.get(str(tag.target_id))
        described = {k: v for k, v in target.to_dict().items() if k != "target-id"} if target is not None else None
        return (uid, tag.target_type, self.data.final(str(tag.target_id)), described)

    def _working_changed(self) -> None:
        """Copy or outbox changed: put the working data into the collections of the box."""
        if self._tags is None or self._targets is None or self._activities is None:
            return
        with self._applying, acting_as("library"):
            before = self._signature()
            self._targets.sync(lambda: self.data.working("target"), self.data.renamed())
            self._tags.sync(lambda: self.data.working("tag"))
            self._activities.sync(lambda: self.data.working("position"))
            self._positions = dict(self.data.working("position"))
            settings = self.data.working("setting")
            if self._limits is not None:
                self._limits.sync({k[len(VOLUME) :]: v for k, v in settings.items() if k.startswith(VOLUME)})
            if self._playback is not None and before != self._signature():
                # e.g. the tag on the box was linked on the page of the library: it plays at once
                self._playback.recheck_tag()

    # --- what the connection asks ------------------------------------------------------------------------------------

    def unexpected(self, error: BaseException) -> None:
        """The central place for an error nobody expected in the threads of the synchronization."""
        self.logger.error("unexpected exception in the synchronization: %r", error, exc_info=error)
        unexpected.report(error, "synchronization")

    def set_fit(self, fit: str) -> None:
        """Whether box and library fit together (section 4.15)."""
        if fit == self.fit:
            return
        self.fit = fit
        write_atomic(os.path.join(self._folder, "fit"), fit + "\n")  # the state survives a restart
        if self.slots is not None and self.slots.available:
            self.slots.run("_fit", fit)  # "tinyjukebox-slot status" shows it
        if self.on_fit is not None:
            self.on_fit()
        if self.blocked:
            self.logger.error("this box stops working: %s", fit)
        else:
            self.logger.info("box and library: %s", fit)
        self.data.journal("fit", state=fit)

    @property
    def blocked(self) -> bool:
        return self.fit.startswith("library-too-old")

    def playing(self, target_id: str) -> bool:
        album = self._playback.get_album() if self._playback is not None else None
        if album is None:
            return False
        playing = str(album.get_info().get("target_id"))
        return playing in (target_id, self.data.final(target_id))

    def learn(self, provisional: str, final: str, value: dict[str, Any] | None) -> bool:
        """Apply the final id of a target. Returns False if this has to wait: the album is playing and its folder
        has another name in the library."""
        created = [e["value"] for e in self.data.outbox() if e["op"] == "create" and e["key"] == provisional]
        moves = bool(created and value and created[0].get("album-folder") != value.get("album-folder"))
        if moves and self.playing(provisional):
            return False
        self.files.renamed(provisional, final, value)
        self.data.learn(provisional, final, value)
        self.logger.info("the provisional id %s is %s", provisional, final)
        return True

    def target_removed(self, value: dict[str, Any]) -> None:
        if not self.playing(str(value.get("target-id"))):
            self.files.removed(value)

    def look_for_versions(self) -> None:
        """The versions of the box software may have changed in the library."""
        if self.health is not None:
            self.health.look_for_versions()

    def _slot_status(self) -> dict[str, Any]:
        return self.slots.status() if self.slots is not None and self.slots.available else {}

    def discard_defective(self) -> None:
        """Do not trust the data of a version that the health check sent back (section 4.11): what it left in the
        outbox is dropped. What a person entered with it is logged with its full content, not applied."""
        for version in self._slot_status().get("discard", []):
            for entry in self.data.discard_version(version):
                self.logger.warning(
                    "entered by %s with the defective version %s, not applied: %s",
                    entry["by"], version, json.dumps(entry),
                )  # fmt: skip
                self.data.journal("discarded", entry=entry)

    def status_report(self) -> dict[str, Any]:
        """What the box tells the library with every contact: what runs here, and what the box did to its versions
        since the last report."""
        report: dict[str, Any] = {
            "version": self.version, "interface": INTERFACE, "host": socket.gethostname(), "fit": self.fit,
        }  # fmt: skip
        status = self._slot_status()
        if status:
            acknowledged = self.data.numbers["history"]
            if status["history"] and status["history"][-1]["n"] < acknowledged:
                # the switches begin anew - a card set up again with the folder of the synchronization of before:
                # a number from the old list would keep every new switch from the library
                acknowledged = 0
            report.update({
                "slot": status["active"], "slots": status["slots"], "mode": status["mode"],
                "newest": status["newest"], "problem": status["problem"],
                "history": [line for line in status["history"] if line["n"] > acknowledged],
                "left": [
                    {"version": entry["version"], "base": entry["rev"],
                     "why": entry.get("why") or _why_left(status["history"], entry["version"])}
                    for entry in status["versions"] if entry["origin"] == "box"
                ],
                "defective": status["discard"],
            })  # fmt: skip
        return report

    def reported(self, answer: dict[str, Any]) -> None:
        """The library has the report: it knows the switches, the versions this box went back from, and the versions
        that are defective here."""
        if self.slots is None or not self.slots.available:
            return
        if answer.get("left"):
            self.slots.run("_reported", *answer["left"])
        for version in answer.get("defective", []):
            self.slots.run("_discarded", version)
        self.data.history_reported(answer.get("history", self.data.numbers["history"]))

    def info(self) -> dict[str, Any]:
        """The state of the synchronization for the page of the box."""
        return {
            "box": self.data.box_id,
            "name": self.name(),
            "library": self.library,
            "attached": self.attached,
            "connected": self.link.connected,
            "alone": self.link.alone,
            "alone_code": self.link.alone_code,
            "fit": self.fit,
            "blocked": self.blocked,
            "outbox": len(self.data.outbox()),
            "absent": self.absent(),
            "unsettled": self.data.provisional_ids(),
            "fetching": self.files.fetching(),
        }
