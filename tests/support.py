"""Test doubles, test data and helpers shared by unit and integration tests."""

import email.message
import heapq
import http.client
import itertools
import json
import logging
import shutil
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Self

from tinyjukebox.core.pubsub.base import Payload, PubSubClient
from tinyjukebox.messages import RFID_STATE, RfidState

DATA = Path(__file__).parent / "data"
ALBUMS_FOLDER = DATA / "albums"
TARGETS_DATA = DATA / "targets"  # targets.json scenarios and test albums (also broken ones)
ONE_SECOND_MP3 = str(ALBUMS_FOLDER / "simple_album_a" / "1.mp3")  # 0.98 s, 48 kHz
SHORT_MP3 = str(DATA / "audio" / "short.mp3")  # 0.34 s, 44.1 kHz - for real time playback tests

TAG_ALBUM = "04:1A:2B:3C:4D:5E:6F"  # linked to album "1" (three short tracks) - 7 byte uid as on the box
TAG_UNMAPPED = "04:9C:2B:3C:4D:5E:6F"  # not linked
TAG_UNKNOWN_ALBUM = "DE:AD:BE:EF"  # linked to an album id that does not exist
CREATED = "2020-12-01T12:34:56.789000Z"

NULL_OUTPUT = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "-",
               "-af", "arealtime", "-f", "null", "-"]  # fmt: skip


def wait_for(condition: Callable[[], Any], timeout: float = 5.0, interval: float = 0.01) -> Any:
    """Poll ``condition()`` until it is truthy or ``timeout`` expired. Returns the last value of ``condition()``."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(interval)


class SyncPubSub(PubSubClient):
    """Pubsub client that delivers messages synchronously within ``publish`` - deterministic unit tests."""

    def __init__(self, logger: Any) -> None:
        super().__init__(logger, "SyncPubSub")
        self.published: list[tuple[str, Payload]] = []

    def connect(self) -> None:
        self.is_disconnected.clear()
        self.is_connected.set()

    def disconnect(self) -> None:
        self.is_connected.clear()
        self.is_disconnected.set()

    def publish(self, topic: str, msg: Payload) -> None:
        self.published.append((topic, msg))
        self.stats.sent(topic)
        self._dispatch(topic, msg)

    def messages(self, topic: str) -> list[Payload]:
        return [payload for t, payload in self.published if t == topic]

    def subscribed(self, topic: str) -> bool:
        return topic in self.subscribed_topics()

    def _activate_subscription(self, topic: str) -> None:
        pass

    def _deactivate_subscriptions(self, topics: list[str]) -> None:
        pass


class FakePubSub(PubSubClient):
    """Pubsub client for unit tests: records published messages; :meth:`deliver` dispatches a message to the
    subscribers synchronously (handlers get the object itself - like with LocalQueue)."""

    def __init__(self, logger: Any = None) -> None:
        super().__init__(logger or logging.getLogger("tests.pubsub"), "FakePubSub")
        self.published: list[tuple[str, Payload]] = []
        self.connect()

    def connect(self) -> None:
        self.is_disconnected.clear()
        self.is_connected.set()

    def disconnect(self) -> None:
        self.is_connected.clear()
        self.is_disconnected.set()

    def publish(self, topic: str, msg: Payload) -> None:
        self.published.append((topic, msg))
        self.stats.sent(topic)

    def deliver(self, topic: str, msg: Payload) -> None:
        """A message from a driver - dispatched synchronously to all handlers of the topic."""
        self._dispatch(topic, msg)

    def messages(self, topic: str) -> list[Payload]:
        return [payload for t, payload in self.published if t == topic]

    def handlers(self, topic: str) -> list[str]:
        return self.subscribed_topics().get(topic, [])

    def _activate_subscription(self, topic: str) -> None:
        pass

    def _deactivate_subscriptions(self, topics: list[str]) -> None:
        pass


class LoopbackPubSub(FakePubSub):
    """Like LocalQueue: published messages are delivered to the subscribers of the topic as well (synchronously)."""

    def publish(self, topic: str, msg: Payload) -> None:
        super().publish(topic, msg)
        self.deliver(topic, msg)


class FakeRGBLed:
    def __init__(self) -> None:
        self.commands: list[tuple[Any, ...]] = []

    def set_color(self, color: Any) -> None:
        self.commands.append(("color", color))

    def set_blink_color(self, color: Any, interval: float) -> None:
        self.commands.append(("blink", color))

    def set_alternate_colors(self, color_a: Any, color_b: Any, interval: float) -> None:
        self.commands.append(("alternate", color_a, color_b))

    def show_overlay(self, color_a: Any, color_b: Any = None, interval: float = 1, duration: Any = None) -> None:
        self.commands.append(("overlay", color_a, color_b, duration))

    def clear_overlay(self) -> None:
        self.commands.append(("clear_overlay",))

    def set_sequence(self, colors: Any, interval: float = 0.4) -> None:
        self.commands.append(("sequence", tuple(colors)))

    def show_overlay_sequence(self, colors: Any, interval: float = 0.4) -> None:
        self.commands.append(("overlay_sequence", tuple(colors)))

    def last(self) -> tuple[Any, ...] | None:
        return self.commands[-1] if self.commands else None

    def get_state(self) -> None:
        return None


HEADPHONES = {"default-volume": 40, "max-volume": 75}


def make_volumes(
    logger: Any, control: Any = None, config: Mapping[str, Any] | None = None, headphones_control: Any = None,
    limits: Any = None,
) -> Any:  # fmt: skip
    """:class:`~tinyjukebox.volumemanager.Volumes`: the speaker with ``control`` and ``config`` (section ``volume``),
    the headset with ``headphones_control`` and :data:`HEADPHONES`."""
    from tinyjukebox.volumemanager import VolumeManager, Volumes

    config = config or {"max-volume": 80, "default-volume": 50, "volume-step": 5}
    headphones = {**HEADPHONES, "volume-step": config["volume-step"]}
    if limits is not None:
        config, headphones = limits.apply("speaker", config), limits.apply("headphones", headphones)
    return Volumes(
        VolumeManager(control, config, logger),
        VolumeManager(headphones_control, headphones, logger, "VolumeManager.headphones"),
        limits,
        logger,
    )


class FakeRadioPlayer:
    """Radio player without network and ffmpeg: plays as soon as it is started (``players``: all instances)."""

    players: ClassVar[list["FakeRadioPlayer"]] = []

    def __init__(self, logger: Any, url: str, on_state: Callable[[Any], object] | None = None) -> None:
        from tinyjukebox.albumplayer.radioplayer import RadioState

        self.url = url
        self._on_state = on_state
        self.state = RadioState.STOPPED
        self.error: str | None = None
        self.started = False
        FakeRadioPlayer.players.append(self)

    def _set(self, state: Any) -> None:
        self.state = state
        if self._on_state is not None:
            self._on_state(state)

    def start(self) -> None:
        from tinyjukebox.albumplayer.radioplayer import RadioState

        self.started = True
        self._set(RadioState.PLAYING)

    def stop(self) -> None:
        from tinyjukebox.albumplayer.radioplayer import RadioState

        self.state = RadioState.STOPPED


class _Timer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualClock:
    """Clock for the players whose time only advances with :meth:`advance` - timers fire in the calling thread."""

    def __init__(self) -> None:
        self._now = 1000.0
        self._timers: list[tuple[float, int, _Timer, Callable[[], object]]] = []
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def now(self) -> float:
        return self._now

    def call_later(self, delay: float, callback: Callable[[], object]) -> _Timer:
        timer = _Timer()
        with self._lock:
            heapq.heappush(self._timers, (self._now + max(0.0, delay), next(self._counter), timer, callback))
        return timer

    def pending(self) -> int:
        with self._lock:
            return sum(1 for _, _, timer, _ in self._timers if not timer.cancelled)

    def advance(self, seconds: float) -> None:
        """Move the time forward and fire all timers that are due (in order of their due time)."""
        target = self._now + seconds
        while True:
            with self._lock:
                if not self._timers or self._timers[0][0] > target:
                    break
                due, _, timer, callback = heapq.heappop(self._timers)
                self._now = max(self._now, due)
            if not timer.cancelled:
                callback()
        self._now = target


def make_album(folder: str | Path, name: str, tracks: list[tuple[str, str]]) -> str:
    """Create an album folder with a playlist; ``tracks``: list of (file name, source mp3)."""
    album_folder = Path(folder) / name
    album_folder.mkdir(parents=True)
    for filename, source in tracks:
        shutil.copy(source, album_folder / filename)
    (album_folder / "playlist.m3u").write_text("".join(f"{filename}\n" for filename, _ in tracks))
    return str(album_folder)


def create_library(folder: Path, logger: Any, track: str = SHORT_MP3) -> SimpleNamespace:
    """Tags, targets and activities backed by json files in ``folder``.

    Albums: "1" = folder "short" (three copies of ``track``), "2" = "simple_album_b" (tracks of 4, 3 and 5 s).
    Tags: :data:`TAG_ALBUM` -> album 1, :data:`TAG_UNMAPPED`, :data:`TAG_UNKNOWN_ALBUM` -> album 99.
    """
    from tinyjukebox.data.activities import Activities
    from tinyjukebox.data.tags import Tags
    from tinyjukebox.data.targets import Targets

    albums_folder = folder / "albums"
    albums_folder.mkdir()
    make_album(albums_folder, "short", [(f"{i}.mp3", track) for i in (1, 2, 3)])
    shutil.copytree(ALBUMS_FOLDER / "simple_album_b", albums_folder / "simple_album_b")
    targets = {
        "1": {"target-id": "1", "target-type": "album", "album-folder": "short", "playlist": "playlist.m3u",
              "created": CREATED},
        "2": {"target-id": "2", "target-type": "album", "album-folder": "simple_album_b", "playlist": "playlist.m3u",
              "created": CREATED},
    }  # fmt: skip
    tags = {
        TAG_ALBUM: {"tag": {"uid": TAG_ALBUM, "name": "album"}, "target": {"type": "album", "id": "1"},
                    "created": CREATED},
        TAG_UNMAPPED: {"tag": {"uid": TAG_UNMAPPED, "name": "unmapped"}, "target": {"type": "album", "id": -1},
                       "created": CREATED},
        TAG_UNKNOWN_ALBUM: {"tag": {"uid": TAG_UNKNOWN_ALBUM, "name": "unknown"},
                            "target": {"type": "album", "id": "99"}, "created": CREATED},
    }  # fmt: skip
    json_folder = folder / "json"
    json_folder.mkdir()
    (json_folder / "targets.json").write_text(json.dumps(targets))
    (json_folder / "tags.json").write_text(json.dumps(tags))
    lib = SimpleNamespace(
        folder=folder,
        albums_folder=str(albums_folder),
        tags_file=str(json_folder / "tags.json"),
        targets_file=str(json_folder / "targets.json"),
        activities_file=str(json_folder / "activity.json"),
    )
    lib.tags = Tags(lib.tags_file, logger)
    lib.targets = Targets(lib.targets_file, lib.albums_folder, logger)
    lib.activities = Activities(lib.activities_file, lib.tags, logger)
    return lib


def box_config(library: SimpleNamespace, **overrides: Any) -> dict[str, Any]:
    """A complete, valid config for the TinyJukebox service on top of ``library``."""
    tinyjukebox: dict[str, Any] = {
        "albums-folder": library.albums_folder,
        "playlist-filename": "playlist.m3u",
        "volume": {"max-volume": 80, "default-volume": 50, "volume-step": 5},
        "albumplayer": {"rewind-max-time": 5, "update-schedule": 0.1, "pause-between-tracks": 0},
        "json": {"tags": library.tags_file, "targets": library.targets_file, "activities": library.activities_file},
    }  # fmt: skip
    tinyjukebox.update(overrides)
    return {
        "logger": {"log-level": "DEBUG", "log-file": str(library.folder / "tinyjukebox.log")},
        "tinyjukebox": tinyjukebox,
    }


@dataclass
class Response:
    """An http response, read completely before the connection was closed."""

    status: int
    headers: email.message.Message
    content: bytes
    json: Any  # the decoded body - None for an empty or non json body

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)


class ApiClient:
    """Minimal https client for the rest api (verifies the self signed ``certificate``).

    After :meth:`login` every request sends the bearer token (``token`` overrides it for a single request, ``""``
    sends none). ``body`` is sent as json, ``raw`` as is.
    """

    def __init__(self, port: int, certificate: str) -> None:
        self.port = port
        self.context = ssl.create_default_context(cafile=certificate)
        self.token: str | None = None

    def request(self, method: str, path: str, body: Any = None, token: str | None = None,
                headers: Mapping[str, str] | None = None, raw: bytes | None = None) -> Response:  # fmt: skip
        connection = http.client.HTTPSConnection("localhost", self.port, context=self.context, timeout=10)
        request_headers = dict(headers or {})
        token = token if token is not None else self.token
        if token:
            request_headers["Authorization"] = "Bearer " + token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
        finally:
            connection.close()
        try:
            decoded = json.loads(content) if content else None
        except ValueError:
            decoded = None
        return Response(response.status, response.headers, content, decoded)

    def login(self, username: str, password: str) -> Self:
        response = self.post("/api/login", {"username": username, "password": password}, token="")
        assert response.status == 200, response.content
        self.token = response.json["token"]
        return self

    def get(self, path: str, **kwargs: Any) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Response:
        if body is None and "raw" not in kwargs:
            body = {}
        return self.request("POST", path, body, **kwargs)

    def put(self, path: str, body: Any = None, **kwargs: Any) -> Response:
        return self.request("PUT", path, body, **kwargs)

    def patch(self, path: str, body: Any = None, **kwargs: Any) -> Response:
        return self.request("PATCH", path, body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Response:
        return self.request("DELETE", path, **kwargs)


# --- small helpers -------------------------------------------------------------------------------------------------


def noop(*args: object) -> None:
    """Callback that does nothing (e.g. ``update_json`` of an entry without collection)."""


class CallRecorder:
    """Callback that records its calls - for hooks (end of track/album) and ``update_json`` callbacks.

    ``calls`` holds the arguments of every call, ``count`` their number; ``event`` is set by the first call.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.event = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, *args: Any) -> None:
        with self._lock:
            self.calls.append(args)
        self.event.set()

    @property
    def count(self) -> int:
        return len(self.calls)

    def is_set(self) -> bool:
        return self.event.is_set()


def file_id(path: str | Path) -> tuple[int, int, int]:
    """Changes if the file was (re)written: every write creates a new file (temp file + os.replace)."""
    stat = Path(path).stat()
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def deliver_rfid(pubsub: FakePubSub, uid: str, present: bool = True) -> None:
    """The message of the rfid reader (driver ``rfid``) - tag ``uid`` placed or removed."""
    pubsub.deliver(RFID_STATE, RfidState(uid if present else None))


def prepare_targets(folder: Path, scenario: str, albums: tuple[str, ...] | list[str] = ()) -> tuple[str, str]:
    """targets.json of ``tests/data/targets/<scenario>`` and copies of the test ``albums`` in ``folder``.

    Returns (targets file, albums root folder).
    """
    shutil.copy(TARGETS_DATA / scenario / "targets.json", folder / "targets.json")
    root = folder / "albums"
    root.mkdir()
    for album in albums:
        shutil.copytree(TARGETS_DATA / "albums" / album, root / album)
    return str(folder / "targets.json"), str(root)


def album_dict(album_folder: str | None = "simple_album_a", **fields: Any) -> dict[str, Any]:
    """Entry of targets.json for an album (``created``: now); fields set to ``None`` are left out."""
    from tinyjukebox.data.common import TargetType, now

    target: dict[str, Any] = {
        "target-id": "id",
        "target-type": TargetType.album.name,
        "album-folder": album_folder,
        "playlist": "playlist.m3u",
        "created": now(),
    }
    target.update(fields)
    return {key: value for key, value in target.items() if value is not None}
