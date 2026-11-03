"""A library and boxes in one process, talking over https - what the test bed did with containers
(docs/developer/library.md, section 9), now with the real application."""

import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from tests.support import ApiClient, box_config, create_library, wait_for
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.core.pubsub.localqueue import LocalQueue
from tinyjukebox.library.service import Library
from tinyjukebox.messages import RFID_STATE, RfidState
from tinyjukebox.sync import acting_as
from tinyjukebox.tinyjukebox import TinyJukebox
from tinyjukebox.web.auth import UsersFile

BOOTSTRAP = "bootstrap-password-of-the-tests"
ADMIN = ("admin", "admin-password")
OFFLINE = "https://127.0.0.1:1"  # nothing listens there


class Box:
    """An TinyJukebox service without hardware in ``folder``, with the data of :func:`tests.support.create_library`
    or without any."""

    def __init__(self, folder: Path, logger: logging.Logger, empty: bool = False, identity: Path | None = None) -> None:
        self.folder = folder
        self._logger = logger
        folder.mkdir()
        if empty:
            (folder / "albums").mkdir()
            (folder / "json").mkdir()
            for name in ("tags.json", "targets.json"):
                (folder / "json" / name).write_text("{}")
            self.library = create_namespace(folder)
        else:
            self.library = create_library(folder, logger)
            del self.library.tags, self.library.targets, self.library.activities  # the service loads its own
        self.identity = identity or folder / "identity"
        self.tinyjukebox: TinyJukebox | None = None

    def connect(self, url: str, bootstrap: str | None = BOOTSTRAP) -> None:
        """What the playbook does: the address of the library and the bootstrap password."""
        self.identity.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.identity / "library").write_text(url + "\n")
        if bootstrap is not None:
            (self.identity / "bootstrap").write_text(bootstrap + "\n")
        if self.tinyjukebox is not None:
            self.sync.link.wake()

    def disconnect(self) -> None:
        """The box is at the grandparents: the library cannot be reached."""
        (self.identity / "library").write_text(OFFLINE + "\n")
        self.sync.link.wait_pass(5)

    def start(self) -> "Box":
        config = box_config(
            self.library,
            albumplayer={"pause-between-tracks": 0, "rewind-max-time": 5, "update-schedule": 0.1},
            sync={"retry-seconds": 0.1, "identity": str(self.identity)},
        )
        self.tinyjukebox = TinyJukebox(
            config, logger=self._logger.getChild(self.folder.name), no_gui=True, hardware=False, webserver=False,
            player_factory=DummyPlayer, poll_interval=0.05,
        )  # fmt: skip
        self.tinyjukebox.start()
        return self

    def stop(self) -> None:
        if self.tinyjukebox is not None:
            self.tinyjukebox.stop()
            self.tinyjukebox = None

    @property
    def service(self) -> TinyJukebox:
        assert self.tinyjukebox is not None
        return self.tinyjukebox

    @property
    def sync(self) -> Any:
        return self.service._sync

    @property
    def control(self) -> Any:
        return self.service.control

    @property
    def state(self) -> str:
        return str(self.service.stmoperation.get_active_state().value)

    def synced(self, timeout: float = 10.0) -> bool:
        """Wait until the box is connected and has nothing left to settle with the library."""

        def settled() -> bool:
            self.sync.link.wait_pass(2)
            return bool(self.sync.link.connected and not self.sync.data.outbox() and not self.sync.files.fetching())

        return bool(wait_for(settled, timeout=timeout, interval=0.05))

    def place(self, uid: str | None) -> None:
        pubsub = self.service._pubsub_client
        assert isinstance(pubsub, LocalQueue)
        pubsub.publish(RFID_STATE, RfidState(uid))
        pubsub.wait_until_delivered()

    def tags(self) -> dict[str, Any]:
        return {tag["uid"]: tag for tag in self.control.list_tags()}

    def files(self, folder: str) -> list[str]:
        try:
            return sorted(name for name in os.listdir(self.folder / "albums" / folder) if not name.startswith("."))
        except FileNotFoundError:
            return []


def create_namespace(folder: Path) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        folder=folder,
        albums_folder=str(folder / "albums"),
        tags_file=str(folder / "json" / "tags.json"),
        targets_file=str(folder / "json" / "targets.json"),
        activities_file=str(folder / "json" / "activity.json"),
    )


class Bed:
    def __init__(self, folder: Path, logger: logging.Logger, certificate: tuple[str, str], users: dict[str, Any]):
        self.folder = folder
        self._logger = logger
        self._certificate = certificate
        self._users = users
        self.boxes: list[Box] = []
        self.releases: dict[str, Any] = {}  # the section "releases" of the config; counts from the next restart
        self.platform = ""  # the platform of the boxes whose versions the library fetches; from the next restart
        self.bootstrap = BOOTSTRAP  # the bootstrap password of the config; from the next restart
        self.library = self._library()
        self.library.start()

    def _library(self, port: int = 0) -> Library:
        root = self.folder / "library"
        root.mkdir(exist_ok=True)
        files = (str(root / "library.crt"), str(root / "library.key"))
        if not os.path.exists(files[0]):
            for source, target in zip(self._certificate, files, strict=True):
                shutil.copy(source, target)
            UsersFile.write(str(root / "users.yaml"), self._users)
        config = {
            "data-folder": str(root / "data"),
            "albums-folder": str(root / "albums"),
            "images-folder": str(root / "images"),
            "bootstrap-password": self.bootstrap,
            "rest-api": {"host": "127.0.0.1", "port": port, "certificate": files[0], "private-key": files[1],
                         "users-file": str(root / "users.yaml")},
            "releases": self.releases,
            "platform": self.platform,
        }  # fmt: skip
        return Library(config, self._logger.getChild("library"))

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.library.api.port}"

    def restart_library(self) -> None:
        """The same data, the same address - e.g. after an update of the package."""
        port = self.library.api.port
        self.library.stop()
        self.library = self._library(port)
        self.library.start()

    def box(
        self,
        name: str,
        empty: bool = False,
        connect: bool = True,
        bootstrap: str = BOOTSTRAP,
        identity: Path | None = None,
    ) -> Box:
        box = Box(self.folder / name, self._logger, empty, identity)
        if connect:
            box.connect(self.url, bootstrap)
        self.boxes.append(box)
        return box.start()

    def admin(self) -> ApiClient:
        """A client that is logged in on the page of the library."""
        client = ApiClient(self.library.api.port, self._certificate[0])
        client.login(*ADMIN)
        return client

    @contextlib.contextmanager
    def as_admin(self) -> Any:
        """Changes made inside count as entered on the page of the library."""
        with acting_as(f"web:{ADMIN[0]}"):
            yield self.library.control
        self.library.settle()

    def catalog(self, kind: str) -> dict[str, Any]:
        return self.library.store.working(kind)

    def stop(self) -> None:
        for box in self.boxes:
            box.stop()
        self.library.stop()
