"""The playback of one entry of Spotify with Soloist (docs/developer/concept/spotify.md, sections 3, 4 and 7.3).

Everything happens in a thread of its own, one thing after the other: the commands of the box and the events of
Soloist are queued. What the player does:

- **Start:** Soloist and its sound (:class:`~tinyjukebox.spotify.engine.SpotifyEngine`), shuffle and repeat off,
  ``play`` with the address of the entry. With a saved position the sound is dropped meanwhile: as many times ``next``
  as the saved place says, then the address of the track there is compared with the saved one. If it differs, the track
  is looked up in the queue (what came before, up to 80 ahead); if it is not there, the entry starts from the
  beginning. Then ``seek`` and sound.
- **The place of the track** follows from the queue: the track that starts is the next one or the one before.
- **The end:** a track that Spotify added by itself (``source: autoplay``), or nothing plays any more after the last
  track - the player pauses there: finished.
- **The phone has no influence** (decision 7): what the box did not ask for itself is undone - a pause, another track,
  shuffle, repeat, the volume. Music started elsewhere with the account of the box takes the playback away: the
  player is "elsewhere" until "play" takes it back.
- **No sound while "playing"** (open report no. 5) for :data:`STALL_SECONDS`: the track is started again at its
  position.
- **Soloist ends:** connecting again after :data:`RETRY_DELAYS`; an expired build is fetched anew (exit code 10).
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from tinyjukebox.core.logging import get_child
from tinyjukebox.spotify.account import EXPIRED, Unavailable
from tinyjukebox.spotify.soloist import Soloist, SoloistError
from tinyjukebox.spotify.sound import PipeReader

__all__ = [
    "OWN_WINDOW",
    "RETRY_DELAYS",
    "STALL_SECONDS",
    "Engine",
    "SpotifyPlayer",
    "SpotifyState",
    "Track",
]

RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
STALL_SECONDS = 15.0
OWN_WINDOW = 3.0
"""Seconds after a command of the box in which what Soloist reports counts as its answer, not as the phone."""
WAIT = 10.0
"""Seconds the player waits for an answer of Soloist (a track that starts, the login)."""
END_TOLERANCE = 3.0
"""A track counts as played to its end if no more than this is left."""


class SpotifyState(Enum):
    CONNECTING = "connecting"  # Soloist starts, the box goes to the saved position
    PLAYING = "playing"
    PAUSED = "paused"
    ELSEWHERE = "elsewhere"  # the account of the box plays somewhere else now
    RETRYING = "retrying"  # Soloist has ended - starting it again
    FINISHED = "finished"
    STOPPED = "stopped"
    ERROR = "error"  # cannot play: no key, not connected, no player ...


@dataclass(frozen=True)
class Track:
    """The track (chapter, episode) of the entry: its place, address, title and length in seconds."""

    index: int = 0
    uri: str | None = None
    title: str | None = None
    length: float | None = None


class Engine(Protocol):
    """What the player needs of :class:`~tinyjukebox.spotify.engine.SpotifyEngine`."""

    @property
    def reader(self) -> PipeReader | None: ...

    def open(self, listener: Any) -> Soloist: ...

    def close(self, listener: Any) -> None: ...


class _Stopped(Exception):  # noqa: N818
    pass


class _Again(Exception):  # noqa: N818
    """Start again from the position the player has."""


def _track_of(item: object, index: int) -> Track | None:
    if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
        return None
    decorations = item.get("decorations") or {}
    duration = (decorations.get("playback") or {}).get("duration_ms")
    return Track(
        index,
        item["uri"],
        (decorations.get("identity") or {}).get("name"),
        duration / 1000 if isinstance(duration, int | float) else None,
    )


class SpotifyPlayer:
    """Plays the entry ``uri`` with the Soloist of ``engine`` until :meth:`stop`."""

    def __init__(
        self,
        logger: logging.Logger,
        uri: str,
        engine: Engine,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        stall: float = STALL_SECONDS,
        wait: float = WAIT,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self.uri = uri
        self._engine = engine
        self._clock = clock
        self._wall = wall
        self._retry_delays = retry_delays
        self._stall = stall
        self._wait = wait
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._deferred: list[tuple[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._soloist: Soloist | None = None
        self._state = SpotifyState.STOPPED
        self.error: str | None = None
        self._track = Track()
        self._anchor: tuple[float, float, float] = (0.0, 0.0, 0.0)  # position ms, at epoch ms, speed
        self._status: str | None = None
        self._logged_in: bool | None = None
        self._previous: list[dict[str, Any]] = []
        self._upcoming: list[dict[str, Any]] = []
        self._own_until = 0.0
        self._skipping = False
        self._attempt = 0
        self.on_state: Callable[[SpotifyState], None] = lambda state: None
        self.on_track: Callable[[Track, float, bool], None] = lambda track, seconds, to_end: None
        """A track ended or was left: the track, the seconds listened, whether it was played to its end."""
        self.on_finished: Callable[[], None] = lambda: None
        self.on_lost: Callable[[], None] = lambda: None
        """Soloist says that the box has no login: the account is not connected any more."""
        self.on_output_lost: Callable[[], None] = lambda: None
        """``aplay`` failed on the headset: it has gone."""

    # --- what the box asks for ---------------------------------------------------------------------------------------

    @property
    def state(self) -> SpotifyState:
        return self._state

    def start(self, track: Track, seconds: float) -> None:
        """Start the playback at ``track`` (its place and address) and ``seconds`` - returns at once."""
        with self._lock:
            self._track, self._anchor = track, (seconds * 1000, self._wall() * 1000, 0.0)
            self._thread = threading.Thread(target=self._run, name="SpotifyPlayer", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        self._queue.put(("pause", None))

    def play(self) -> None:
        self._queue.put(("play", None))

    def next(self) -> None:
        self._queue.put(("next", None))

    def prev(self) -> None:
        self._queue.put(("prev", None))

    def restart(self) -> None:
        """From the beginning of the entry."""
        self._queue.put(("restart", None))

    def output_changed(self) -> None:
        """Another output (headset or speaker): the sound continues there."""
        reader = self._engine.reader
        if reader is not None:
            reader.restart_output()

    def stop(self, timeout: float = 10.0) -> None:
        self._queue.put(("stop", None))
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def position(self) -> tuple[Track, float]:
        """The track and the seconds within it."""
        with self._lock:
            track, (position, at, speed) = self._track, self._anchor
        seconds = (position + max(0.0, self._wall() * 1000 - at) * speed) / 1000
        if track.length is not None:
            seconds = min(seconds, track.length)
        return track, max(0.0, seconds)

    # --- what Soloist says (engine.Listener) -------------------------------------------------------------------------

    def event(self, event: dict[str, Any]) -> None:
        self._queue.put(("event", event))

    def exited(self, code: int) -> None:
        self._queue.put(("exited", code))

    def output_lost(self) -> None:
        self.on_output_lost()

    # --- the thread --------------------------------------------------------------------------------------------------

    def _set_state(self, state: SpotifyState, error: str | None = None) -> None:
        if state == self._state and error == self.error:
            return
        self._state, self.error = state, error
        self._logger.info("state: %s%s", state.value, f" ({error})" if error else "")
        self.on_state(state)

    def _run(self) -> None:
        try:
            self._begin()
            while True:
                self._handle(*self._next_item())
        except _Stopped:
            pass
        except Exception:
            self._logger.exception("the playback of %s failed", self.uri)
            self._set_state(SpotifyState.ERROR, "unexpected")
        finally:
            self._finish()

    def _next_item(self) -> tuple[str, Any]:
        if self._deferred:
            return self._deferred.pop(0)
        try:
            return self._queue.get(timeout=1.0)
        except queue.Empty:
            return "tick", None

    def _finish(self) -> None:
        track, seconds = self.position()
        if self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED) and track.uri is not None:
            self.on_track(track, seconds, False)
        soloist, self._soloist = self._soloist, None
        if soloist is not None and soloist.running:
            try:
                soloist.ctl("pause")
            except SoloistError as e:
                self._logger.info("pause at the end: %s", e)
        reader = self._engine.reader
        if reader is not None:
            reader.silence()
        self._engine.close(self)
        if self._state not in (SpotifyState.ERROR, SpotifyState.FINISHED):
            self._set_state(SpotifyState.STOPPED)

    def _handle(self, kind: str, value: Any) -> None:
        if kind == "stop":
            raise _Stopped
        if kind == "event":
            self._event(value)
        elif kind == "exited":
            self._exited(value)
        elif kind == "tick":
            self._tick()
        elif self._state in (SpotifyState.ERROR, SpotifyState.RETRYING, SpotifyState.CONNECTING):
            return
        elif kind == "pause" and self._state == SpotifyState.PLAYING:
            self._own("pause")
            self._silence()
            self._set_state(SpotifyState.PAUSED)
        elif kind == "play" and self._state in (SpotifyState.PAUSED, SpotifyState.ELSEWHERE):
            if self._state == SpotifyState.ELSEWHERE:
                self._own("activate")
            self._own("play")
            self._sound()
            self._set_state(SpotifyState.PLAYING)
        elif kind in ("next", "prev") and self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED):
            self._own(kind)
            if self._state == SpotifyState.PAUSED:
                self._own("pause")
        elif kind == "restart" or (kind == "prev" and self._state == SpotifyState.FINISHED):
            self._go_to(Track(), 0.0)
            self._set_state(SpotifyState.PLAYING)

    def _own(self, *command: str) -> None:
        """A command of the box: what Soloist reports in the next seconds is its answer."""
        soloist = self._soloist
        if soloist is None:
            return
        self._own_until = self._clock() + OWN_WINDOW
        try:
            soloist.ctl(*command)
        except SoloistError as e:
            self._logger.warning("%s", e)

    def _is_own(self) -> bool:
        return self._skipping or self._clock() < self._own_until

    def _silence(self) -> None:
        reader = self._engine.reader
        if reader is not None:
            reader.silence()

    def _sound(self) -> None:
        reader = self._engine.reader
        if reader is not None:
            reader.sound()

    # --- start and going to a position ------------------------------------------------------------------------------

    def _begin(self) -> None:
        """Soloist, the login, the entry at the saved position."""
        self._set_state(SpotifyState.CONNECTING)
        try:
            self._soloist = self._engine.open(self)
        except Unavailable as e:
            if e.reason == "not_connected":
                self.on_lost()
            self._set_state(SpotifyState.ERROR, e.reason)
            return
        except OSError as e:
            self._logger.error("cannot start Soloist: %s", e)
            self._retry(str(e))
            return
        self._silence()
        if not self._await(lambda: self._logged_in is not None):
            self._retry("Soloist does not answer")
            return
        if not self._logged_in:
            self.on_lost()
            self._set_state(SpotifyState.ERROR, "not_connected")
            return
        track, seconds = self.position()
        self._own("shuffle", "off")
        self._own("repeat", "off")
        self._own("volume", "100")
        if not self._go_to(track, seconds):
            self._retry("the entry does not start")
            return
        self._attempt = 0
        self._set_state(SpotifyState.PLAYING)

    def _await(self, condition: Callable[[], bool], timeout: float | None = None) -> bool:
        """Handle the events until ``condition()`` holds - commands of the box wait. :raises _Stopped:"""
        end = self._clock() + (self._wait if timeout is None else timeout)
        while not condition():
            if self._clock() >= end:
                return False
            try:
                kind, value = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "stop":
                raise _Stopped
            if kind == "event":
                self._event(value)
            elif kind == "exited":
                raise _Again
            else:
                self._deferred.append((kind, value))
        return True

    def _go_to(self, track: Track, seconds: float) -> bool:
        """The entry from the beginning, then to ``track`` and ``seconds``, without sound on the way."""
        self._silence()
        self._skipping = True
        try:
            started = self._play_entry()
            if started and track.index > 0:
                started = self._skip_to(track)
            if started and track.uri is not None and self._track.uri != track.uri:
                started = self._find(track)
            if started and seconds > 1 and self._track.uri == track.uri:
                self._own("seek", str(round(seconds * 1000)))
                with self._lock:
                    self._anchor = (seconds * 1000, self._wall() * 1000, 1.0)
        except _Again:
            self._skipping = False
            raise
        self._skipping = False
        if started:
            self._sound()
        return started

    def _play_entry(self) -> bool:
        with self._lock:
            self._track = Track()
        self._own("play", self.uri)
        return self._await(lambda: self._track.uri is not None)

    def _skip_to(self, track: Track) -> bool:
        for _ in range(track.index):
            index = self._track.index
            self._own("next")
            if not self._await(lambda index=index: self._track.index > index):  # type: ignore[misc]
                return False
        return True

    def _find(self, track: Track) -> bool:
        """The saved track is not at its place (the playlist was changed): look for it around it."""
        soloist = self._soloist
        if soloist is None:
            return False
        try:
            queued = soloist.json("queue", "80", "--json")
        except SoloistError as e:
            self._logger.warning("%s", e)
            queued = {}
        for name, command in (("upcoming", "next"), ("previous", "prev")):
            uris = [entry.get("item", {}).get("uri") for entry in queued.get(name) or []]
            if track.uri in uris:
                for _ in range(uris.index(track.uri) + 1):
                    current = self._track.uri
                    self._own(command)
                    if not self._await(lambda current=current: self._track.uri != current):  # type: ignore[misc]
                        return False
                return True
        self._logger.info("the saved track %s is not in the entry any more - from the beginning", track.uri)
        return self._play_entry()

    def _retry(self, error: str) -> None:
        delay = self._retry_delays[min(self._attempt, len(self._retry_delays) - 1)]
        self._attempt += 1
        self._set_state(SpotifyState.RETRYING, error)
        self._logger.warning("%s - starting again in %.0f s", error, delay)
        self._engine.close(self)
        self._soloist = None
        end = self._clock() + delay
        while self._clock() < end:
            try:
                kind, value = self._queue.get(timeout=min(0.5, max(0.01, end - self._clock())))
            except queue.Empty:
                continue
            if kind == "stop":
                raise _Stopped
            if kind not in ("event", "exited"):
                self._deferred.append((kind, value))
        try:
            self._begin()
        except _Again:
            self._retry("Soloist has ended")

    # --- what Soloist reports ----------------------------------------------------------------------------------------

    def _event(self, event: dict[str, Any]) -> None:  # noqa: PLR0912 - one branch per kind of event
        kind = event.get("type")
        if kind == "auth_state":
            self._logged_in = bool(event.get("logged_in"))
            if not self._logged_in and self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED):
                self.on_lost()
                self._set_state(SpotifyState.ERROR, "not_connected")
        elif kind == "playback_state":
            self._logged_in = True
            self._queue_entries(event)
            if event.get("item") is not None:
                self._changed_track(event["item"])
            self._position(event.get("position"))
            self._playback(event.get("status"))
        elif kind == "track_changed":
            self._changed_track(event.get("item"))
        elif kind == "position_sync":
            self._position(event.get("position"))
        elif kind == "playback_changed":
            self._playback(event.get("status"))
        elif kind == "queue_changed":
            self._queue_entries(event)
        elif kind == "options_changed":
            self._options(event.get("options") or {})
        elif kind == "volume_changed":
            if event.get("volume") not in (None, 100) and not self._skipping:
                self._own("volume", "100")
        elif kind == "device_changed" and event.get("is_active") is False:
            if self._state == SpotifyState.PLAYING:
                self._logger.info("the account of the box plays somewhere else now")
                self._silence()
                self._set_state(SpotifyState.ELSEWHERE)

    def _queue_entries(self, event: dict[str, Any]) -> None:
        if isinstance(event.get("upcoming"), list):
            self._upcoming = event["upcoming"]
        if isinstance(event.get("previous"), list):
            self._previous = event["previous"]

    def _position(self, position: object) -> None:
        if isinstance(position, dict) and isinstance(position.get("position_ms"), int | float):
            with self._lock:
                speed = position.get("speed")
                self._anchor = (
                    float(position["position_ms"]),
                    float(position.get("timestamp_ms") or self._wall() * 1000),
                    float(speed) if isinstance(speed, int | float) else 1.0,
                )

    def _options(self, options: dict[str, Any]) -> None:
        if self._skipping:
            return
        if options.get("shuffle"):
            self._own("shuffle", "off")
        if options.get("repeat") not in (None, "off", False):
            self._own("repeat", "off")

    def _changed_track(self, item: object) -> None:
        uri = item.get("uri") if isinstance(item, dict) else None
        if uri is None:
            return
        current = self._track
        if uri == current.uri:
            new = _track_of(item, current.index)
            with self._lock:
                self._track = replace(current, title=new.title, length=new.length) if new else current
            return
        upcoming = [entry for entry in self._upcoming if isinstance(entry, dict)]
        previous = [entry for entry in self._previous if isinstance(entry, dict)]
        source = None
        if current.uri is None:
            index = 0
        elif upcoming and upcoming[0].get("item", {}).get("uri") == uri:
            index, source = current.index + 1, upcoming[0].get("source")
        elif previous and previous[0].get("item", {}).get("uri") == uri:
            index = max(0, current.index - 1)
        elif self._is_own():
            index = current.index + 1
        else:
            self._logger.info("another track was started from the Spotify app - back to the track of the box")
            track, seconds = self.position()
            self._go_to(track, seconds)
            return
        if current.uri is not None and not self._skipping:
            track, seconds = self.position()
            self.on_track(track, seconds, track.length is not None and seconds >= track.length - END_TOLERANCE)
        with self._lock:
            self._track = _track_of(item, index) or Track(index, uri)
            self._anchor = (0.0, self._wall() * 1000, 1.0 if self._state == SpotifyState.PLAYING else 0.0)
        if source == "autoplay" and not self._skipping:
            self._finished()

    def _playback(self, status: object) -> None:
        self._status = status if isinstance(status, str) else self._status
        if self._skipping or self._state != SpotifyState.PLAYING:
            return
        if status == "idle":
            self._finished()
        elif status == "paused" and not self._is_own():
            track, seconds = self.position()
            if track.length is not None and seconds >= track.length - END_TOLERANCE and not self._upcoming:
                self._finished()
            else:
                self._logger.info("paused from the Spotify app - the box plays on")
                self._own("play")

    def _finished(self) -> None:
        if self._state == SpotifyState.FINISHED:
            return
        track, seconds = self.position()
        self._own("pause")
        self._silence()
        if track.uri is not None:
            self.on_track(track, seconds, True)
        self._set_state(SpotifyState.FINISHED)
        self.on_finished()

    def _exited(self, code: int) -> None:
        self._soloist = None
        if code == EXPIRED:
            self._set_state(SpotifyState.ERROR, "expired")
            return
        if self._state in (SpotifyState.STOPPED, SpotifyState.ERROR):
            return
        self._retry(f"Soloist has ended ({code})")

    def _tick(self) -> None:
        """Once a second: does sound come while Soloist says "playing"?"""
        reader = self._engine.reader
        if self._state != SpotifyState.PLAYING or reader is None or reader.muted:
            return
        if self._clock() - reader.last_sound > self._stall:
            self._logger.warning("no sound for %.0f s while playing - starting the track again", self._stall)
            track, seconds = self.position()
            reader.last_sound = self._clock()
            self._go_to(track, seconds)
