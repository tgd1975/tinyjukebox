"""The playback of one entry of Spotify with Soloist (docs/developer/architecture.md, section 4.1).

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
- **The phone has no influence**: what the box did not ask for itself is undone - a pause, another track,
  shuffle, repeat, the volume. Music started elsewhere with the account of the box takes the playback away: the
  player is "elsewhere" until "play" takes it back.
- **No sound while "playing"** (open report no. 5) for :data:`STALL_SECONDS`: the track is started again at its
  position.
- **Soloist ends:** connecting again after :data:`RETRY_DELAYS` - in a loop, for hours if the box has no internet; an
  expired build is fetched anew (exit code 10). In a pause it is started again only with "play". While "Connect" runs,
  no Soloist may run: the player waits, trying every :data:`BUSY_WAIT` seconds - that is no failure.
- **A pause is silent at once:** :meth:`SpotifyPlayer.pause` mutes the sound in the thread of the caller; the player
  takes the sound back only with "play" or "restart".
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
from tinyjukebox.spotify.account import EXPIRED, Busy, Unavailable
from tinyjukebox.spotify.soloist import Soloist, SoloistError
from tinyjukebox.spotify.sound import PipeReader

__all__ = [
    "BUSY_WAIT",
    "OWN_WINDOW",
    "RETRY_DELAYS",
    "STALL_SECONDS",
    "Engine",
    "SpotifyPlayer",
    "SpotifyState",
    "Track",
]

RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
BUSY_WAIT = 1.0
"""Seconds between two tries while no Soloist may run ("Connect")."""
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
    """Soloist has ended or cannot be started: start it again, from the position the player has. ``counted``: a
    failure, which makes the next wait longer; ``code``: the exit code of Soloist."""

    def __init__(self, error: str, counted: bool = True, code: int | None = None) -> None:
        super().__init__(error)
        self.counted = counted
        self.code = code


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
        busy_wait: float = BUSY_WAIT,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self.uri = uri
        self._engine = engine
        self._clock = clock
        self._wall = wall
        self._retry_delays = retry_delays
        self._stall = stall
        self._wait = wait
        self._busy_wait = busy_wait
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
        self._logged_out_at: float | None = None  # since when Soloist says "not logged in"
        self._previous: list[dict[str, Any]] = []
        self._upcoming: list[dict[str, Any]] = []
        self._own_until = 0.0
        self._skipping = False
        self._attempt = 0
        self._quiet = False  # paused by the box: no sound until "play" or "restart" (under _lock)
        self.on_state: Callable[[SpotifyState], None] = lambda state: None
        self.on_track: Callable[[Track, float, bool], None] = lambda track, seconds, to_end: None
        """A track ended or was left: the track, the seconds listened, whether it was played to its end."""
        self.on_finished: Callable[[], None] = lambda: None
        self.on_lost: Callable[[], None] = lambda: None
        """Soloist says that the box has no login: the account is not connected any more."""
        self.on_output_lost: Callable[[], None] = lambda: None
        """``aplay`` failed on the headset: it has gone."""
        self.first = 0
        """The place of the track where "from the beginning" starts - a chapter of a calendar."""
        self.single = False
        """Only the track at :attr:`first` plays - a chapter of a calendar: at its end, and with "next", the player is
        finished, and "back" goes to the beginning of the track."""
        self._onward = False

    # --- what the box asks for ---------------------------------------------------------------------------------------

    @property
    def state(self) -> SpotifyState:
        return self._state

    def start(self, track: Track, seconds: float, onward: bool = False) -> None:
        """Start the playback at ``track`` (its place and address) and ``seconds`` - returns at once. ``onward``: the
        entry was finished at ``track`` - if a track of the entry itself follows it now (a growing calendar), the
        playback starts there, otherwise at the beginning of ``track``."""
        with self._lock:
            self._onward = onward
            self._track, self._anchor = track, (seconds * 1000, self._wall() * 1000, 0.0)
            self._thread = threading.Thread(target=self._run, name="SpotifyPlayer", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Pause - silent at once, before Soloist is paused in the thread of the player (the headset may have gone,
        and the speaker may become the output as soon as this returns)."""
        with self._lock:
            self._quiet = True
            reader = self._engine.reader
            if reader is not None:
                reader.muted = True
        self._queue.put(("pause", None))

    def play(self) -> None:
        with self._lock:
            self._quiet = False
        self._queue.put(("play", None))

    def next(self) -> None:
        self._queue.put(("next", None))

    def prev(self) -> None:
        self._queue.put(("prev", None))

    def restart(self) -> None:
        """From the beginning of the entry."""
        with self._lock:
            self._quiet = False
        self._queue.put(("restart", None))

    def output_changed(self) -> None:
        """Another output (headset or speaker): the sound continues there - another ``aplay``, started in the thread
        of the player."""
        self._queue.put(("output", None))

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

    def suspended(self) -> None:
        self._queue.put(("suspended", None))

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
            begin = True
            while True:  # a loop, never a recursion: a box without the internet tries again for hours
                try:
                    if begin:
                        self._begin()
                    while True:
                        self._handle(*self._next_item())
                except _Again as again:
                    begin = self._recover(again)
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

    def _handle(self, kind: str, value: Any) -> None:  # noqa: PLR0912 - one branch per command
        """One command or event. :raises _Again: Soloist has ended (or could not be started again)."""
        if kind == "stop":
            raise _Stopped
        if kind == "event":
            self._event(value)
        if self._lost_login():
            return
        if kind in ("exited", "suspended"):
            if self._state not in (SpotifyState.STOPPED, SpotifyState.ERROR):
                raise self._ended(kind, value)
        elif kind == "tick":
            self._tick()
        elif kind == "output":
            self._restart_output()
        elif self._state in (SpotifyState.ERROR, SpotifyState.RETRYING, SpotifyState.CONNECTING):
            return
        elif kind == "pause" and self._state == SpotifyState.PLAYING:
            self._own("pause")
            self._silence()
            self._set_state(SpotifyState.PAUSED)
        elif kind == "play" and self._state in (SpotifyState.PAUSED, SpotifyState.ELSEWHERE):
            if self._soloist is None:  # Soloist ended in the pause
                self._begin()
                return
            if self._state == SpotifyState.ELSEWHERE:
                self._own("activate")
            self._own("play")
            self._sound()
            self._set_state(SpotifyState.PLAYING)
        elif kind == "next" and self.single and self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED):
            self._finished()  # the next track is the next day
        elif kind == "prev" and self.single and self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED):
            self._own("seek", "0")  # the track before is the day before
            with self._lock:
                self._anchor = (0.0, self._wall() * 1000, 1.0 if self._state == SpotifyState.PLAYING else 0.0)
        elif kind in ("next", "prev") and self._state in (SpotifyState.PLAYING, SpotifyState.PAUSED):
            self._own(kind)
            if self._state == SpotifyState.PAUSED:
                self._own("pause")
        elif kind == "restart" or (kind == "prev" and self._state == SpotifyState.FINISHED):
            with self._lock:
                self._onward = False
                self._track, self._anchor = Track(self.first), (0.0, self._wall() * 1000, 0.0)
            if self._soloist is None:  # Soloist ended in the pause
                self._begin()
                return
            self._set_state(SpotifyState.CONNECTING)  # if Soloist ends on the way, it is started again
            self._go_to(Track(self.first), 0.0)
            self._set_state(SpotifyState.PLAYING)

    def _lost_login(self) -> bool:
        """Has Soloist said "not logged in" for longer than it takes to sign in again? Then the login is gone."""
        out = self._logged_out_at
        if out is None or self._state not in (SpotifyState.PLAYING, SpotifyState.PAUSED):
            return False
        if self._clock() - out < self._wait:
            return False
        self.on_lost()
        self._set_state(SpotifyState.ERROR, "not_connected")
        return True

    @staticmethod
    def _ended(kind: str, code: Any) -> _Again:
        if kind == "suspended":
            return _Again("Soloist was ended by the box", counted=False)
        return _Again(f"Soloist has ended ({code})", code=code)

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
        """The sound is heard again - unless the box has paused meanwhile."""
        with self._lock:
            reader = self._engine.reader
            if reader is not None and not self._quiet:
                reader.sound()

    def _restart_output(self) -> None:
        reader = self._engine.reader
        if reader is not None:
            reader.restart_output()

    # --- start and going to a position ------------------------------------------------------------------------------

    def _begin(self) -> None:
        """Soloist, the login, the entry at the saved position. :raises _Again: Soloist has to be started again"""
        if self._state != SpotifyState.RETRYING:  # RETRYING stays until Soloist runs: no change every second
            self._set_state(SpotifyState.CONNECTING)
        try:
            self._soloist = self._engine.open(self)
        except Busy as e:
            raise _Again(str(e), counted=False) from e
        except Unavailable as e:
            if e.reason == "not_connected":
                self.on_lost()
            self._set_state(SpotifyState.ERROR, e.reason)
            return
        except OSError as e:
            self._logger.error("cannot start Soloist: %s", e)
            raise _Again(str(e)) from e
        self._set_state(SpotifyState.CONNECTING)
        self._silence()
        if not self._await(lambda: self._logged_in is not None):
            raise _Again("Soloist does not answer")
        # Soloist reports "not logged in" first and "logged in" a moment later, once it has signed in with the login
        # it keeps (0.7 s on the Pi 3): only a "not logged in" that stays is no login
        if not self._logged_in and not self._await(lambda: bool(self._logged_in)):
            self.on_lost()
            self._set_state(SpotifyState.ERROR, "not_connected")
            return
        track, seconds = self.position()
        self._own("shuffle", "off")
        self._own("repeat", "off")
        self._own("volume", "100")
        if not self._go_to(track, seconds, onward=self._onward):
            raise _Again("the entry does not start")
        self._onward = False  # started again after a failure, it goes to where it is now
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
            elif kind in ("exited", "suspended"):
                raise self._ended(kind, value)
            elif kind == "output":
                self._restart_output()
            else:
                self._deferred.append((kind, value))
        return True

    def _go_to(self, track: Track, seconds: float, onward: bool = False) -> bool:
        """The entry from the beginning, then to ``track`` and ``seconds``, without sound on the way - ``onward``: to
        the track after it, if the entry has one now (:meth:`start`)."""
        self._silence()
        self._skipping = True
        try:
            started = self._play_entry()
            if started and track.index > 0:
                started = self._skip_to(track)
            if started and track.uri is not None and self._track.uri != track.uri:
                started = self._find(track)
            if started and onward and track.uri is not None and self._track.uri == track.uri:
                started = self._onward_from(track)
                seconds = 0.0
            if started and seconds > 1 and self._track.uri == track.uri:
                self._own("seek", str(round(seconds * 1000)))
                with self._lock:
                    self._anchor = (seconds * 1000, self._wall() * 1000, 1.0)
        except _Again:
            self._skipping = False
            with self._lock:  # Soloist ended on the way: the next one goes there again, not to where this one was
                self._track, self._anchor = track, (seconds * 1000, self._wall() * 1000, 0.0)
            raise
        self._skipping = False
        if started:
            self._sound()  # not if the box paused on the way
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

    def _onward_from(self, track: Track) -> bool:
        """The player is at ``track``, where the entry was finished: the next track if the entry itself has one now
        (not one that Spotify adds, ``source: autoplay``) - otherwise ``track`` again from its beginning."""
        soloist = self._soloist
        if soloist is None:
            return False
        try:
            queued = soloist.json("queue", "1", "--json")
        except SoloistError as e:
            self._logger.warning("%s", e)
            queued = {}
        upcoming = [entry for entry in queued.get("upcoming") or [] if isinstance(entry, dict)]
        following = upcoming[0] if upcoming else None
        if following is None or following.get("source") == "autoplay" or not following.get("item", {}).get("uri"):
            self._logger.info("nothing new after %s - it plays again", track.uri)
            return True
        self._logger.info("a new track after %s: %s", track.uri, following["item"]["uri"])
        index = self._track.index
        self._own("next")
        return self._await(lambda: self._track.index > index)

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

    def _recover(self, again: _Again) -> bool:
        """Soloist has ended, or cannot be started now - whether to start it again now (after a wait). In a pause it
        is started again only with "play"; the position stays where the sound stopped."""
        self._soloist = None
        self._logged_in = self._logged_out_at = None  # the next Soloist answers for itself
        self._engine.close(self)
        _, seconds = self.position()
        with self._lock:
            self._anchor = (seconds * 1000, self._wall() * 1000, 0.0)
            quiet = self._quiet
        if again.code == EXPIRED:
            self._set_state(SpotifyState.ERROR, "expired")
            return False
        if self._state in (SpotifyState.STOPPED, SpotifyState.ERROR):
            return False
        if quiet or self._state in (SpotifyState.PAUSED, SpotifyState.ELSEWHERE, SpotifyState.FINISHED):
            self._logger.info("%s - it is started again with the next command", again)
            if self._state not in (SpotifyState.ELSEWHERE, SpotifyState.FINISHED):
                self._set_state(SpotifyState.PAUSED)
            return False
        return self._wait_to_retry(again)

    def _wait_to_retry(self, again: _Again) -> bool:
        """RETRYING: wait the next of the retry delays after a failure, :data:`BUSY_WAIT` while no Soloist may run -
        True: start it again; False: the box paused meanwhile (Soloist starts again with "play")."""
        if again.counted:
            delay = self._retry_delays[min(self._attempt, len(self._retry_delays) - 1)]
            self._attempt += 1
            self._logger.warning("%s - starting again in %.0f s", again, delay)
        else:
            delay = self._busy_wait
        self._set_state(SpotifyState.RETRYING, str(again))
        end = self._clock() + delay
        while self._clock() < end:
            try:
                kind, value = self._queue.get(timeout=min(0.5, max(0.01, end - self._clock())))
            except queue.Empty:
                continue
            if kind == "stop":
                raise _Stopped
            if kind == "pause":
                self._set_state(SpotifyState.PAUSED)
                return False
            if kind not in ("event", "exited", "suspended", "output"):
                self._deferred.append((kind, value))
        return True

    # --- what Soloist reports ----------------------------------------------------------------------------------------

    def _event(self, event: dict[str, Any]) -> None:  # noqa: PLR0912 - one branch per kind of event
        kind = event.get("type")
        if kind == "auth_state":
            self._logged_in = bool(event.get("logged_in"))
            if self._logged_in:
                self._logged_out_at = None
            elif self._logged_out_at is None:
                self._logged_out_at = self._clock()  # it counts once it lasts (:meth:`_tick`)
        elif kind == "playback_state":
            self._logged_in = True
            self._logged_out_at = None
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
        if self.single and current.uri is not None and not self._skipping and index != current.index:
            self._finished()  # the chapter has ended: the next track belongs to another day
            return
        if current.uri is not None and not self._skipping:
            track, seconds = self.position()
            self.on_track(track, seconds, track.length is not None and seconds >= track.length - END_TOLERANCE)
        running = self._state in (SpotifyState.PLAYING, SpotifyState.CONNECTING)  # Soloist plays, if silently
        with self._lock:
            self._track = _track_of(item, index) or Track(index, uri)
            self._anchor = (0.0, self._wall() * 1000, 1.0 if running else 0.0)
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
