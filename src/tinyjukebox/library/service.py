"""The library: catalog, albums, covers of the stations, id table, journal and the archive of box versions; a web
page for managing them, and the interface for the boxes (docs/developer/library.md, section 3).

    tinyjukebox-library -c library.yaml

A server and a box are different things, so this is a program of its own. It uses what both need: the data of
:mod:`tinyjukebox.data`, the operations of :class:`~tinyjukebox.control.CatalogControl`, the web server with its page
and its users. Drivers, state machines and player are left out.
"""

import argparse
import logging
import os
import queue
import re
import secrets
import signal
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from tinyjukebox import __version__
from tinyjukebox.backup import Backup, Folder
from tinyjukebox.control import CatalogControl
from tinyjukebox.core.config import read_config, validate_config
from tinyjukebox.core.logging import add_stdout_handler, create_logger
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.targets import Targets
from tinyjukebox.errors import ApiError, BadRequest, Forbidden, Gone, NotFound, Unauthorized, Unprocessable
from tinyjukebox.images import Images
from tinyjukebox.library.backups import Backups
from tinyjukebox.library.releases import Releases
from tinyjukebox.library.schema import get_schema
from tinyjukebox.library.statistics import statistics
from tinyjukebox.library.store import CATALOG, Invalid, Store, Unknown
from tinyjukebox.sync import INTERFACE, actor, handshake
from tinyjukebox.sync.client import KEEP_ALIVE, Unreachable
from tinyjukebox.sync.util import read_json, version_key, write_json
from tinyjukebox.web.auth import has_role
from tinyjukebox.web.logbuffer import LogBuffer
from tinyjukebox.web.server import GUARDED, NO_CONTENT, Request, Response, RestApi, Route, Writable

__all__ = ["BOX_ID", "Hub", "Library", "LibraryControl", "main"]

BOX_ID = re.compile(r"[a-z][a-z0-9]{1,15}")
READER_UID = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){3}((:[0-9A-F]{2}){3}){0,2}")
"""uid of a tag as ``rfid-reader`` reports it: 4, 7 or 10 bytes."""
HANDSHAKE_SECONDS = 30
MAX_CHANGES_BODY = 4 * 2**20
FILLING = "first filling"  # who sends the files of the first filling, for the log
# what never changes, whatever the version of the interface (section 4.10)
UNVERSIONED = re.compile(r"/api/sync/(boxes|interface|versions(/[^/]+(/signature)?)?)")
V = f"/api/sync/v{INTERFACE}"


class Hub:
    """Passes events on to the boxes that keep a connection open."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[queue.Queue[str]] = []

    def publish(self, event: str) -> None:
        with self._lock:
            for subscriber in self._queues:
                subscriber.put(event)

    def subscribe(self) -> Iterator[str | None]:
        """The events for one box; None when nothing happened for a while."""
        subscriber: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._queues.append(subscriber)
        try:
            # the first event tells a box that it will hear of every change from now on; what changed before, it asks
            yield "connected"
            while True:
                try:
                    yield subscriber.get(timeout=KEEP_ALIVE)
                except queue.Empty:
                    yield None
        finally:
            with self._lock:
                self._queues.remove(subscriber)


class LibraryControl(CatalogControl):
    """What the page of the library does with the catalog. There is no tag on a library and nothing plays."""

    def __init__(
        self,
        config: Mapping[str, Any],
        tags: Tags,
        targets: Targets,
        activities: Activities,
        logger: logging.Logger,
        images: Images,
        store: Store,
    ) -> None:
        super().__init__(config, tags, targets, activities, logger, images)
        self._store = store

    def _album_changed(self, album_id: str) -> None:
        self._store.album_changed(album_id, actor())

    def backup(self, albums: bool = False) -> Backup:
        """The data folder (:meth:`Store.backup_data`), the covers of the stations and, with ``albums``, the albums."""
        folders = [Folder("images", self._store.images_folder)]
        if albums:
            folders.append(Folder("albums", self._store.albums_folder))
        return Backup("library", albums, self._store.backup_data(), folders)

    def _described(self, album_id: str) -> dict[str, Any]:
        album: dict[str, Any] = (self._store.entry("target", album_id) or {}).get("album", {})
        return album

    def list_albums(self, unmapped_only: bool = False) -> list[dict[str, Any]]:
        """With ``broken``: the number of files of the album that the library or a box found broken."""
        broken = self._store.broken()
        return [dict(album, broken=len(broken.get(album["id"], {}))) for album in super().list_albums(unmapped_only)]

    def get_album(self, album_id: object) -> dict[str, Any]:
        """With ``broken``: the files of the album that the library or a box found broken, in the order of their
        names - ``library`` is what the library found itself (None if nothing), ``boxes`` what boxes reported."""
        result = super().get_album(album_id)
        names = {box["id"]: self._store.box_name(box) for box in self._store.boxes()}
        result["broken"] = [
            {
                "file": name,
                "library": found.get("library"),
                "boxes": [
                    {"box": box, "name": names.get(box, box), **report}
                    for box, report in sorted(found["boxes"].items())
                ],
            }
            for name, found in sorted(self._store.broken(result["id"]).get(result["id"], {}).items())
        ]
        return result

    def forget_broken_reports(self, album_id: str) -> dict[str, Any]:
        """Drop what boxes reported about the files of the album; returns the album."""
        self._album(album_id)
        self._store.forget_reports(album_id)
        return self.get_album(album_id)

    def scan_tag(self, uid: str) -> tuple[dict[str, Any], bool]:
        """A phone read this tag (docs/developer/library.md, section 5.1): an unknown one is added, not linked - as a
        box adds the tag of an unknown tag. Returns the tag and whether it was added. Nothing is kept about the
        scan itself."""
        if not READER_UID.fullmatch(uid):
            raise BadRequest(
                "invalid uid - 4, 7 or 10 bytes as the reader reports them, e.g. 04:1A:2B:3C:4D:5E:6F.",
                code="scan.invalid_uid",
            )
        with self._lock:
            added = uid not in self._tags.tags
            if added:
                self._tags.add_tag(uid)
                self._logger.info("new tag '%s' read by a phone - adding it to tags as unmapped tag.", uid)
            return self._tag_dict(self._tags.get_tag(uid)), added

    def status(self) -> dict[str, Any]:
        boxes = [box for box in self._store.boxes() if not box.get("removed")]
        return {
            "state": "library",
            "tag": None,
            "album": None,
            "radio": None,
            "library": {"version": __version__, "interface": INTERFACE, "boxes": len(boxes)},
        }


class Library:
    def __init__(self, config: Mapping[str, Any], logger: logging.Logger) -> None:
        """``config``: the section ``library`` of the config file."""
        self.logger = logger
        self._config = config
        self.data = os.path.expanduser(config["data-folder"])
        albums = os.path.expanduser(config["albums-folder"])
        images_folder = os.path.expanduser(config["images-folder"])
        json_folder = os.path.expanduser(config.get("json-folder") or os.path.join(self.data, "json"))
        os.makedirs(json_folder, exist_ok=True)
        self.hub = Hub()
        self.store = Store(self.data, albums, images_folder, logger, self.hub.publish)
        self.settings: dict[str, Any] = read_json(os.path.join(self.data, "settings.json"), {})
        if config.get("bootstrap-password") and not self.settings.get("bootstrap"):
            self.settings["bootstrap"] = config["bootstrap-password"]
            self._save_settings()

        # what the library works with is generated from the catalog, as on a box (section 4.2)
        files = {kind: os.path.join(json_folder, name) for kind, name in CATALOG.items()}
        for kind in ("tag", "target"):
            write_json(files[kind], self.store.working(kind))
        self.tags = Tags(files["tag"], logger)
        self.targets = Targets(files["target"], albums, logger)
        write_json(os.path.join(json_folder, "activity.json"), {})  # positions belong to the boxes
        self.activities = Activities(os.path.join(json_folder, "activity.json"), self.tags, logger)
        self.images = Images(images_folder)
        self.targets.id_source = self.store.new_target_id
        self.tags.listen(lambda key, value: self.store.set("tag", key, value, actor()))
        self.targets.listen(lambda key, value: self.store.set("target", key, value, actor()))
        self.images.listen(self.store.scan_images)
        # a change that a box made is put into the working data by a thread of its own: the one that tells of it
        # may hold a lock of the working data
        self._changed = threading.Event()
        self._stopping = threading.Event()
        self._worker: threading.Thread | None = None
        self.store.changed = self._changed.set

        self.control = LibraryControl(
            {"playlist-filename": config.get("playlist-filename", "playlist.m3u")},
            self.tags,
            self.targets,
            self.activities,
            logger,
            self.images,
            self.store,
        )
        self.releases = Releases(
            self.store, self._releases_config(), str(config.get("platform", "")), self.data, logger
        )
        # beside the albums by default: on the NAS that is the shared folder, where the backup of the NAS finds them
        backups = config.get("backups-folder") or os.path.join(os.path.dirname(os.path.abspath(albums)), "backups")
        self.backups = Backups(
            os.path.expanduser(backups), os.path.join(self.data, "backups.json"), self.control.backup, logger
        )
        self._handshakes: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()
        self._log_buffer = LogBuffer()
        logger.addHandler(self._log_buffer)
        self.api = RestApi(
            config.get("rest-api", {}),
            self.control,
            self.targets,
            logger,
            self._log_buffer,
            routes=[*self._sync_routes(), *self._page_routes()],
            guards={"/api/sync/": self._guard},
            mode="library",
        )

    def _save_settings(self) -> None:
        filename = os.path.join(self.data, "settings.json")
        write_json(filename, self.settings)
        os.chmod(filename, 0o600)

    def _releases_config(self) -> dict[str, Any]:
        """What is set on the page comes before the config: the token before the one of the token file, and the
        number of versions the archive keeps."""
        config = dict(self._config.get("releases", {}))
        if self.settings.get("keep-versions"):
            config["keep-versions"] = self.settings["keep-versions"]
        token_file = config.pop("token-file", None)
        self._access = "none"  # where the token comes from: "page", "file", "missing" or "none" (asks without)
        if self.settings.get("releases-token"):
            config["token"] = self.settings["releases-token"]
            self._access = "page"
        elif token_file:
            try:
                with open(os.path.expanduser(token_file), encoding="utf-8") as infile:
                    config["token"] = infile.read().strip()
                self._access = "file"
            except FileNotFoundError:
                # without its token the library does not ask the git server
                self.logger.warning(
                    "the token for the releases is missing (not set on the page, no file %s): "
                    "no new versions are fetched",
                    token_file,
                )
                config["token"] = None
                self._access = "missing"
        return config

    # --- start and stop ----------------------------------------------------------------------------------------------

    def _own_slot(self) -> None:
        """The package brings the slot of its own version (section 6.13)."""
        folder = os.path.expanduser(self._config.get("slots-folder") or "")
        if not folder or not os.path.isdir(folder):
            return
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            description = read_json(os.path.join(folder, name))
            # also a version whose files are gone, e.g. after the data was restored from a backup
            if not self.store.versions().get(description["version"], {}).get("files"):
                base = os.path.join(folder, name.removesuffix(".json"))
                files = {"description": f"{base}.json", "archive": f"{base}.tar.gz", "signature": f"{base}.tar.gz.sig"}
                self.store.add_version(description, files, "package")

    def start(self) -> None:
        self._stopping.clear()
        self._own_slot()
        self._worker = threading.Thread(target=self._apply_changes, name="Library.working", daemon=True)
        self._worker.start()
        self.api.start()
        self.releases.prune()  # the limit may be new: set in the config, or the default of this version
        self.releases.start()
        self.backups.start()
        self.logger.info("library %s serves the interface version %d", __version__, INTERFACE)

    def stop(self) -> None:
        self._stopping.set()
        self._changed.set()
        self.backups.stop()
        self.api.stop()
        if self._worker is not None:
            self._worker.join()
            self._worker = None
        self.logger.removeHandler(self._log_buffer)
        self.store.close()

    def run(self) -> None:
        self.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            self.logger.info("stopping")
        finally:
            self.stop()

    def _apply_changes(self) -> None:
        """Put what boxes changed in the catalog into the working data of the library."""
        while True:
            self._changed.wait()
            if self._stopping.is_set():
                return
            self._changed.clear()
            try:
                self.targets.sync(lambda: self.store.working("target"))
                self.tags.sync(lambda: self.store.working("tag"))
            except Exception:
                self.logger.exception("the working data of the library cannot be brought up to date")

    def settle(self, timeout: float = 5.0) -> None:
        """Wait until the working data has what the boxes changed (tests)."""
        deadline = time.monotonic() + timeout
        while self._changed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)

    # --- who may ask (section 4.14) ----------------------------------------------------------------------------------

    def _guard(self, request: Request) -> None:
        """No part of the interface for the boxes answers without the key of a box."""
        kind, _, credentials = request.headers.get("Authorization", "").partition(" ")
        box_id, _, secret = credentials.partition(":")
        box = self.store.authenticate(box_id, secret) if kind == "Box" else None
        if box is None:
            raise Unauthorized("")
        try:
            speaks = int(request.headers.get("X-Box-Interface", "0") or 0)
        except ValueError:
            speaks = 0
        self.store.contact(box, request.headers.get("X-Box-Version", ""), speaks)
        request.context["box"] = box
        if request.params.get("box", box_id) != box_id:
            raise Forbidden("the key of a box is valid for its own data only.")
        if not UNVERSIONED.fullmatch(request.path) and not request.path.startswith(f"{V}/"):
            # a box that speaks another version gets nothing but what updates it (section 4.15)
            raise Gone("this version of the interface is not served.", serves=[INTERFACE])

    def _handshake(self, request: Request) -> Response:
        """Two steps. Nobody without the bootstrap password gets more than a random number."""
        body = request.json()
        if not BOX_ID.fullmatch(str(body.get("box", ""))):
            raise Unauthorized("")
        box_id, now = str(body["box"]), time.monotonic()
        with self._lock:
            if "proof" not in body:
                if not isinstance(body.get("nonce"), str) or len(body["nonce"]) < 16:
                    raise Unauthorized("")
                nonce = handshake.new_nonce()
                self._handshakes[box_id] = (body["nonce"], nonce, now + HANDSHAKE_SECONDS)
                return Response(data={"nonce": nonce})
            nonce_box, nonce_library, expires = self._handshakes.pop(box_id, ("", "", 0.0))
        with open(self.api.certificate_file, encoding="utf-8") as infile:
            certificate = infile.read()
        password = str(self.settings.get("bootstrap") or "")
        expected = handshake.proof(password, "box", box_id, nonce_box, nonce_library, certificate)
        if not password or now > expires or not handshake.same(str(body["proof"]), expected):
            self.logger.warning("handshake of box %s rejected", box_id)
            raise Unauthorized("")
        secret = handshake.new_secret()
        self.store.register(box_id, str(body.get("name") or box_id), secret)
        proof = handshake.proof(password, "library", box_id, nonce_box, nonce_library, certificate)
        return Response(data={"proof": proof, "key": secret})

    # --- the interface for the boxes (section 4.13) ------------------------------------------------------------------

    def _sync_routes(self) -> list[Route]:
        store = self.store

        def answer(function: Any) -> Any:
            """A route of the interface: what the store does not have or rejects becomes an error for the box."""

            def wrapped(request: Request) -> Response:
                try:
                    result = function(request)
                except Unknown as error:
                    raise NotFound(f"unknown: {error}") from None
                except Invalid as error:
                    raise Unprocessable(str(error)) from None
                if isinstance(result, Response):
                    return result
                return NO_CONTENT if result is None else Response(data=result)

            return wrapped

        def journal(request: Request) -> Any:
            with store.lock:
                box = store.box(request.params["box"])
                lines, before = request.json(MAX_CHANGES_BODY)["lines"], box["journal"]
                box["journal"] = store.journal.add(box["id"], before, lines)
                store.save_box(box)
                for line in lines:  # the lines that were taken now: a file that the box found damaged is collected
                    if line.get("event") == "broken" and before < line["n"] <= box["journal"]:
                        store.reported(box["id"], line)
                return {"confirmed": box["journal"]}

        def own_statistics(request: Request) -> Any:
            """What the journal of this box says, for the page of the box. The other boxes are not its business."""
            result = self.statistics(request.params["box"])
            return {**result, "boxes": [entry for entry in result["boxes"] if entry["id"] == result["box"]]}

        def changes(request: Request) -> Any:
            entries = request.json(MAX_CHANGES_BODY).get("entries")
            if not isinstance(entries, list):
                raise Invalid("'entries' is missing")
            return {"results": store.apply(request.params["box"], entries)}

        def receive(request: Request) -> Any:
            return store.receive_file(
                request.context["box"]["id"], request.params["id"], request.params["file"], request.save
            )

        def receive_image(request: Request) -> Any:
            store.receive_image(request.context["box"]["id"], request.params["name"], request.save)

        routes = [
            # what never changes, next to the handshake: interface versions, versions of the box software
            ("GET", "/api/sync/interface", lambda r: {"serves": [INTERFACE], "library": __version__}),
            ("GET", "/api/sync/versions", lambda r: {"versions": [v for v in self.versions() if v["offered"]]}),
            ("GET", "/api/sync/versions/{version}", lambda r: self._version_file(r.params["version"], "archive")),
            (
                "GET",
                "/api/sync/versions/{version}/signature",
                lambda r: self._version_file(r.params["version"], "signature"),
            ),
            # the interface of this version
            ("GET", f"{V}/catalog", lambda r: {"files": store.catalog_index()}),
            ("GET", f"{V}/catalog/{{file}}", lambda r: Response(file=store.catalog_path(r.params["file"]))),
            ("GET", f"{V}/boxes/{{box}}/state", lambda r: store.box_state(r.params["box"])),
            ("POST", f"{V}/boxes/{{box}}/changes", changes),
            (
                "GET",
                f"{V}/boxes/{{box}}/ids/{{type}}/{{id}}",
                lambda r: store.resolve(r.params["box"], r.params["type"], r.params["id"]),
            ),
            ("POST", f"{V}/boxes/{{box}}/journal", journal),
            ("GET", f"{V}/boxes/{{box}}/statistics", own_statistics),
            ("PUT", f"{V}/boxes/{{box}}/status", lambda r: store.report(r.params["box"], r.json(MAX_CHANGES_BODY))),
            ("GET", f"{V}/albums/{{id}}/manifest", lambda r: store.manifest(r.params["id"])),
            (
                "GET",
                f"{V}/albums/{{id}}/files/{{file}}",
                lambda r: Response(file=store.album_file(r.params["id"], r.params["file"])),
            ),
            ("PUT", f"{V}/albums/{{id}}/files/{{file}}", receive),
            ("GET", f"{V}/images/{{name}}", lambda r: Response(file=store.image_file(r.params["name"]))),
            ("PUT", f"{V}/images/{{name}}", receive_image),
            ("GET", f"{V}/events", lambda r: Response(content_type="text/event-stream", stream=self._events)),
        ]
        return [
            # the handshake proves the bootstrap password itself
            Route("POST", "/api/sync/boxes", self._handshake, None),
            *(Route(method, pattern, answer(handler), GUARDED) for method, pattern, handler in routes),
        ]

    def _events(self, out: Writable) -> None:
        """Server-sent events for one box until it goes away or the library stops (section 4.9). An event carries
        no content: it only says that something changed."""
        source = self.hub.subscribe()
        try:
            for event in source:
                if self._stopping.is_set():
                    break
                out.write(b": keep-alive\n\n" if event is None else f"event: {event}\ndata: {{}}\n\n".encode())
                out.flush()
        except OSError:  # the box went away
            pass
        finally:
            source.close()  # type: ignore[attr-defined]

    def _version_file(self, version: str, kind: str) -> Response:
        entry = self.store.versions().get(version)
        if entry is not None and not entry.get("files"):
            self.releases.restore(version)  # removed from the archive: fetched again when needed
        return Response(file=self.store.version_file(version, kind))

    # --- versions and boxes as the page shows them (section 6.13) ----------------------------------------------------

    def versions(self) -> list[dict[str, Any]]:
        """The versions in the archive with their states. A version that speaks an interface this library does not
        serve needs a newer library and is not offered to the boxes (section 8)."""
        boxes = self.store.boxes()
        result = []
        for version, entry in sorted(self.store.versions().items(), key=lambda item: version_key(item[0])):
            description = entry["description"]
            fits = description["interface"] == INTERFACE
            result.append({
                "version": version, "state": entry["state"], "rev": entry["rev"], "by": entry["by"],
                "when": entry["when"], "why": entry["why"], "description": description, "offered": fits,
                "note": "" if fits else "needs a newer library", "archived": bool(entry.get("files")),
                "boxes": [box["id"] for box in boxes if box["status"].get("version") == version],
                # fetched by the box and tried on its next start (section 6.3)
                "prepared": [
                    box["id"] for box in boxes
                    if box["status"].get("slots", {}).get("next", {}).get("version") == version
                ],
            })  # fmt: skip
        return result

    def statistics(self, box: str | None = None) -> dict[str, Any]:
        """What was played (section 7): on all boxes, or on ``box`` only. ``boxes`` always names every box, so that
        the page can offer them."""
        store = self.store
        names = {entry["id"]: store.box_name(entry) for entry in store.boxes()}
        gone = frozenset(entry["id"] for entry in store.boxes() if entry.get("removed"))

        def counted(only: str | None) -> dict[str, Any]:
            return statistics(
                store.journal.lines(only),
                store.journal.records(box=only),
                store.working("tag"),
                store.working("target"),
                names,
                gone,
            )

        result = counted(None)
        if box is None:
            return {**result, "box": None}
        return {**counted(store.box(box)["id"]), "boxes": result["boxes"], "box": box}

    def overview(self) -> dict[str, Any]:
        boxes = []
        for box in self.store.boxes():
            speaks, name = box.get("interface", 0), self.store.box_name(box)
            fits = ""
            if speaks and speaks != INTERFACE:
                fits = f"box {name} speaks version {speaks}, this library serves {INTERFACE}"
                if speaks < INTERFACE and box["status"].get("mode") in ("manual", "off"):
                    fits += "; its update mode is manual, it must be updated by hand"
            boxes.append({
                "id": box["id"], "name": name, "removed": box.get("removed", False), "contact": box.get("contact"),
                "registered": box.get("registered"), "software": box.get("software"), "interface": speaks,
                "fits": fits, "status": box["status"], "positions": len(box["positions"]),
                "settings": {key: entry["value"] for key, entry in box["settings"].items()},
                "waiting": len(box["waiting"]),
            })  # fmt: skip
        return {
            "library": __version__, "interface": INTERFACE, "boxes": boxes, "versions": self.versions(),
            "uploads": self.store.uploads(),
            "releases": {**self.releases.state(), "token": self._access},
            "bootstrap": bool(self.settings.get("bootstrap")),
        }  # fmt: skip

    # --- what the page of the library does beyond the catalog --------------------------------------------------------

    def _page_routes(self) -> list[Route]:
        store = self.store

        def done(function: Any) -> Any:
            def wrapped(request: Request) -> Response:
                try:
                    result = function(request)
                except Unknown as error:
                    raise NotFound(f"unknown: {error}") from None
                except (Invalid, KeyError, TypeError) as error:  # an Invalid may name the code of its text
                    raise BadRequest(str(error), **getattr(error, "details", {})) from None
                return NO_CONTENT if result is None else Response(data=result)

            return wrapped

        def set_version(request: Request) -> None:
            body = request.json()
            store.set_version(request.params["version"], body["state"], actor(), str(body.get("why") or "set by hand"))

        def set_box(request: Request) -> None:
            body = request.json()
            if "name" in body:
                if not isinstance(body["name"], str) or not body["name"].strip():
                    raise Invalid("name must not be empty.", code="name.empty")
                store.set_setting(request.params["box"], "name", body["name"].strip(), actor())
            if "takeover" in body:
                store.take_over(request.params["box"], str(body["takeover"]), actor())

        def set_position(request: Request) -> None:
            store.set_position(request.params["box"], request.params["uid"], request.json().get("value"), actor())

        def bootstrap(request: Request) -> None:
            password = request.json().get("password")
            if not isinstance(password, str) or len(password) < 16:
                raise Invalid("the bootstrap password must have at least 16 characters.", code="bootstrap.too_short")
            self.settings["bootstrap"] = password
            self._save_settings()
            self.logger.info("bootstrap password changed by %s", actor())

        def token(request: Request) -> dict[str, str]:
            new = secrets.token_urlsafe(32)
            self.settings.setdefault("script-tokens", []).append(handshake.hash_secret(new))
            self._save_settings()
            self.logger.info("token for scripts created by %s", actor())
            return {"token": new}

        def verify(request: Request) -> dict[str, Any]:
            store.verify_albums()
            store.scan_images()
            return self.overview()

        def journal(request: Request) -> dict[str, Any]:
            kind, box = request.query.get("kind", [None])[-1], request.query.get("box", [None])[-1]
            return {"records": store.journal.records(kind, box), "lines": store.journal.lines(box)}

        def scan(request: Request) -> Response:
            tag, added = self.control.scan_tag(request.params["uid"])
            return Response(201 if added else 200, tag)

        def ask_backup(request: Request) -> Response:
            self.backups.ask(bool(request.json().get("albums", False)))
            return Response(202, self.backups.status())

        def stored_backup(request: Request) -> Response:
            name = request.params["name"]
            headers = {"Content-Disposition": f'attachment; filename="{name}"'}
            return Response(200, headers=headers, file=self.backups.path(name))

        def play_statistics(request: Request) -> dict[str, Any]:
            return self.statistics(request.query.get("box", [None])[-1] or None)

        return [
            Route("GET", "/api/library/overview", done(lambda r: self.overview()), "user"),
            Route("PUT", "/api/library/versions/{version}", done(set_version), "admin"),
            Route("PATCH", "/api/library/boxes/{box}", done(set_box), "admin"),
            Route("DELETE", "/api/library/boxes/{box}", done(lambda r: store.remove_box(r.params["box"])), "admin"),
            Route("PUT", "/api/library/boxes/{box}/positions/{uid}", done(set_position), "admin"),
            Route("GET", "/api/library/boxes/{box}", done(lambda r: store.box_state(r.params["box"])), "admin"),
            # by an admin of the page, or by a script with its token (check_release.sh)
            Route("POST", "/api/library/releases/check", self._check_releases, None),
            Route("PUT", "/api/library/releases/token", done(self._set_releases_token), "admin"),
            Route("PUT", "/api/library/releases/keep", done(self._set_releases_keep), "admin"),
            Route("PUT", "/api/library/bootstrap", done(bootstrap), "admin"),
            Route("POST", "/api/library/tokens", done(token), "admin"),
            Route("POST", "/api/library/verify", done(verify), "admin"),
            Route(
                "DELETE",
                "/api/library/albums/{id}/broken",
                done(lambda r: self.control.forget_broken_reports(r.params["id"])),
                "admin",
            ),
            Route("GET", "/api/library/journal", done(journal), "admin"),
            # the backups the library makes by itself (tinyjukebox.library.backups); GET /api/backup sends one at once
            Route("GET", "/api/library/backups", done(lambda r: self.backups.status()), "admin"),
            Route("PUT", "/api/library/backups", done(lambda r: self.backups.set(r.json())), "admin"),
            Route("POST", "/api/library/backups", ask_backup, "admin"),
            Route("GET", "/api/library/backups/{name}", stored_backup, "admin"),
            Route(
                "DELETE", "/api/library/backups/{name}", done(lambda r: self.backups.remove(r.params["name"])), "admin"
            ),
            # what was played is for everybody with a login on the page; it never leaves the library
            Route("GET", "/api/library/statistics", done(play_statistics), "user"),
            # the phone as reader: every user may make a tag known, as everybody may place a tag on a box
            Route("PUT", "/api/tags/{uid}", scan, "user"),
            *self._fill_routes(done),
        ]

    def _fill_routes(self, done: Any) -> list[Route]:
        """The first filling of the library from the PC (section 3.5, ``tinyjukebox-fill-library``)."""
        store = self.store

        def fill(request: Request) -> dict[str, Any]:
            """The first filling (section 3.5): tags and targets under their ids."""
            body = request.json(MAX_CHANGES_BODY)
            kinds = {"target": "targets", "tag": "tags"}
            return {name: store.fill(kind, body.get(name) or {}, actor()) for kind, name in kinds.items()}

        def describe(request: Request) -> dict[str, Any]:
            manifest = request.json(MAX_CHANGES_BODY).get("manifest") or {}
            return {"lacks": store.describe_album(request.params["id"], manifest, actor())}

        def album_file(request: Request) -> dict[str, Any]:
            return store.receive_file(FILLING, request.params["id"], request.params["file"], request.save)

        def image(request: Request) -> None:
            store.receive_image(FILLING, request.params["name"], request.save)

        return [
            Route("POST", "/api/library/fill", done(fill), "admin"),
            Route("POST", "/api/library/fill/albums/{id}", done(describe), "admin"),
            Route("PUT", "/api/library/fill/albums/{id}/files/{file}", done(album_file), "admin"),
            Route("PUT", "/api/library/fill/images/{name}", done(image), "admin"),
        ]

    def _set_releases_keep(self, request: Request) -> dict[str, Any]:
        """How many versions the archive keeps at most; what is too much goes at once."""
        new = request.json().get("versions")
        if not isinstance(new, int) or isinstance(new, bool) or new < 1:
            raise Invalid("the number of versions must be 1 or more.", code="releases.keep_invalid")
        self.releases.set_keep(new)
        self.settings["keep-versions"] = new
        self._save_settings()
        self.logger.info("the archive keeps %d versions at most, set by %s", new, actor())
        return {"keep": new}

    def _set_releases_token(self, request: Request) -> dict[str, Any]:
        """Take over the token with which the library reads the releases, and look for a release with it."""
        new = request.json().get("token")
        if not isinstance(new, str) or not new.strip():
            raise Invalid("the token must not be empty.", code="releases.token_empty")
        try:
            self.releases.set_token(new.strip())
        except Unreachable as error:
            raise Invalid(
                f"the token is not taken over: {error}", code="releases.token_refused", reason=str(error)
            ) from None
        self.settings["releases-token"] = new.strip()
        self._save_settings()
        self._access = "page"
        self.logger.info("token for the releases set by %s", actor())
        return self.releases.check(by_hand=True)

    def _check_releases(self, request: Request) -> Response:
        """Look for a release now. Also shortens the interval of the checks that follow (section 8)."""
        role = self.api.users.get_role(request.session.username) if request.session is not None else None
        if role is not None and has_role(role, "admin"):
            by = f"web:{request.session.username}" if request.session is not None else "web"
        elif request.token and handshake.hash_secret(request.token) in self.settings.get("script-tokens", []):
            by = "script"
        else:
            raise Unauthorized("login required.")
        self.logger.info("check for a release triggered by %s", by)
        return Response(data=self.releases.check(by_hand=True))


def main(argv: Sequence[str] | None = None) -> None:
    """Console script ``tinyjukebox-library``."""
    parser = argparse.ArgumentParser(description="The library of Tiny Jukebox: catalog, albums and box versions.")
    parser.add_argument("-c", "--config", required=True, help="config file (yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log to the console as well")
    parser.add_argument("--version", action="version", version=__version__)
    arguments = parser.parse_args(argv)
    config = read_config(arguments.config)
    error = validate_config(config, get_schema())
    if error is not None:
        raise SystemExit(f"{arguments.config}: {error}")
    log_file = os.path.expanduser(config["logger"]["log-file"])
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    logger = create_logger(config["logger"], "Library")
    add_stdout_handler("INFO" if arguments.verbose else "WARNING")

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(signal.Signals(signum).name)

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    try:
        Library(config["library"], logger).run()
    except ApiError as failure:
        raise SystemExit(failure.message) from None
    except Exception:
        logger.exception("the library ends with an exception")
        raise


if __name__ == "__main__":  # the start script of the package on the NAS runs the module
    main()
