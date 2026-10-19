"""Internet radio: plays a stream with ffmpeg and aplay (see :mod:`~tinyjukebox.albumplayer.simpleplayer`) until it is
stopped - and connects again whenever the stream breaks off."""

import logging
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Protocol

from tinyjukebox.albumplayer import simpleplayer
from tinyjukebox.core.logging import get_child
from tinyjukebox.data.target import check_stream_url
from tinyjukebox.diagnostics import diagnostic

__all__ = [
    "PLAYLIST_EXTENSIONS",
    "RETRY_DELAYS",
    "RadioPlayer",
    "RadioPlayerInterface",
    "RadioState",
    "StateHook",
    "resolve_stream_url",
]

PLAYLIST_EXTENSIONS = (".m3u", ".pls")
"""Urls with these endings are playlists that list the stream - ffmpeg cannot play them itself (HLS: ``.m3u8``
is played by ffmpeg directly)."""
MAX_PLAYLIST_SIZE = 64 * 1024
PLAYLIST_TIMEOUT = 10.0
RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
"""Seconds to wait before the 1st, 2nd, ... attempt to connect again - the last one is repeated."""
STABLE_PLAYBACK = 60.0
"""A stream that played this long counts as working again - the next retry starts with the first delay."""
STARTUP_WINDOW = 30.0
"""Seconds after the start of a connection in which breaks of the sound count as breaks of its start."""
DEFAULT_BUFFER = 0.5
"""Seconds of aplay's buffer if none is configured: the sound is heard only when it is full."""


def _ms(since: float | None, until: float | None) -> int | None:
    return None if since is None or until is None else round((until - since) * 1000)


def _reason(error: str) -> str:
    """Which part failed: the station or the network, the decoder, or the output (sound card, headset)."""
    text = error.lower()
    if text.startswith("cannot load playlist"):
        return "playlist"
    if text.startswith("cannot start"):
        return "start"
    if "aplay" in text or "alsa" in text or "audio open error" in text:
        return "output"
    if any(word in text for word in ("http", "connection", "timed out", "timeout", "network", "resolve", "refused",
                                     "i/o error", "end of file", "server returned")):  # fmt: skip
        return "connection"
    if text == "stream ended":
        return "ended"
    return "decoder"


class _Connection:
    """One connection to a stream, measured on a clock that does not jump (docs/developer/library.md, 4.22): what its
    start took, phase by phase, and how often the sound broke off. ``PLAYING`` only means that ffmpeg runs."""

    def __init__(self, attempt: int, began: float) -> None:
        self.attempt = attempt
        self.player_began = began  # when the player was started: about when the tag was placed
        self.began = time.monotonic()
        self.resolved: float | None = None
        self.kind = "direct"
        self.started: float | None = None  # ffmpeg and aplay run
        self.output: float | None = None  # the first audio came from ffmpeg
        self.underruns: list[tuple[float, float]] = []  # (when, ms)
        self.reported = False
        self.lock = threading.Lock()

    def first_output(self) -> None:
        with self.lock:
            if self.output is None:
                self.output = time.monotonic()

    def underrun(self, ms: float) -> None:
        with self.lock:
            self.underruns.append((time.monotonic(), ms))

    def startup(self, buffer: float) -> dict[str, Any]:
        """What the start of this connection took, in ms from its beginning, and its breaks in the first seconds."""
        with self.lock:
            window = None if self.started is None else self.started + STARTUP_WINDOW
            initial = [ms for when, ms in self.underruns if window is not None and when <= window]
            return {
                "attempt": self.attempt, "kind": self.kind, "resolve_ms": _ms(self.began, self.resolved),
                "pipeline_ms": _ms(self.began, self.started), "first_audio_ms": _ms(self.began, self.output),
                # heard when aplay's buffer is full - an estimate: the output gives no sign
                "audible_ms_estimated": None if self.output is None else _ms(self.began, self.output + buffer),
                "buffer_ms": round(buffer * 1000), "since_player_start_ms": _ms(self.player_began, self.began),
                "underruns": len(initial), "underrun_ms": round(sum(initial)),
            }  # fmt: skip


class RadioState(Enum):
    CONNECTING = "connecting"  # resolving the playlist and starting ffmpeg
    PLAYING = "playing"  # ffmpeg is running
    RETRYING = "retrying"  # the stream failed or ended - waiting to connect again
    STOPPED = "stopped"


StateHook = Callable[[RadioState], object]
"""Called with every new state (from the player thread) - must not block."""

Opener = Callable[..., Any]


def resolve_stream_url(url: str, timeout: float = PLAYLIST_TIMEOUT, opener: Opener = urllib.request.urlopen) -> str:
    """The url of the stream: ``url`` itself or - for m3u/pls playlists - the first http(s) entry of the playlist
    (relative entries are resolved against ``url``). HLS playlists (``#EXT-X-`` tags) are left to ffmpeg.

    :raises OSError: if the playlist cannot be loaded. :raises ValueError: if it lists no stream.
    """
    if not urllib.parse.urlsplit(url).path.lower().endswith(PLAYLIST_EXTENSIONS):
        return url
    with opener(url, timeout=timeout) as response:
        content = response.read(MAX_PLAYLIST_SIZE)
    text = content.decode("utf-8-sig", errors="replace")
    if "#EXT-X-" in text:
        return url
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith(("#", "[")):
            continue
        key, separator, value = entry.partition("=")
        if separator and key.lower().startswith("file"):  # pls: File1=http://...
            entry = value.strip()
        elif separator and "://" not in key:
            continue  # other pls keys (Title1=..., NumberOfEntries=...)
        try:
            return check_stream_url(urllib.parse.urljoin(url, entry))
        except ValueError:
            continue
    raise ValueError(f"playlist '{url}' lists no http(s) stream.")


class RadioPlayerInterface(Protocol):
    """What the radio playback (:class:`~tinyjukebox.stm.playradio.PlayRadio`) needs of a radio player."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def state(self) -> RadioState: ...

    @property
    def error(self) -> str | None: ...


class Pipeline(Protocol):
    def wait(self) -> tuple[bool, str]: ...

    def stop(self) -> None: ...


PipelineFactory = Callable[[str], Pipeline]


class RadioPlayer:
    """Plays the stream at ``url`` in a thread of its own until :meth:`stop` is called.

    If the stream cannot be loaded, fails or ends, the player connects again after :data:`RETRY_DELAYS`. ``error``
    is the reason of the last failure (``None`` while the stream plays). ``on_play`` is called whenever ffmpeg has been
    started, ``on_state`` with every new state.
    """

    def __init__(
        self,
        logger: logging.Logger,
        url: str,
        on_state: StateHook | None = None,
        null_output: bool = False,
        on_play: Callable[[], None] | None = None,
        buffer_time: float | None = None,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        resolve: Callable[[str], str] = resolve_stream_url,
        pipeline_factory: PipelineFactory | None = None,
        output: simpleplayer.AudioOutput | None = None,
    ) -> None:
        """``output``: where to play, looked up for every connection (default: the speaker - ``null_output`` and
        ``buffer_time`` apply only then)."""
        self._logger = get_child(logger, type(self).__name__)
        self.url = url
        self._on_state = on_state
        self._on_play = on_play
        self._retry_delays = tuple(retry_delays) or RETRY_DELAYS
        self._resolve = resolve
        audio_output = output or simpleplayer.AudioOutput(null_output, buffer_time)
        self._pipeline_factory: PipelineFactory = pipeline_factory or (
            lambda source: simpleplayer.AudioPipeline(
                source, output=audio_output.current()[0], stream=True, on_output=self._first_output,
                on_underrun=self._underrun,
            )
        )  # fmt: skip
        self._buffer = (
            output.buffer_time if output is not None else buffer_time
        ) or DEFAULT_BUFFER  # fmt: skip
        self._began = time.monotonic()
        self._connection: _Connection | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._pipeline: Pipeline | None = None
        self._thread: threading.Thread | None = None
        self._attempts = 0
        self._state = RadioState.STOPPED
        self._error: str | None = None

    @property
    def state(self) -> RadioState:
        return self._state

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        """Start playing - returns immediately (connecting happens in the player thread)."""
        with self._lock:
            if self._thread is not None:
                return
            self._stopped.clear()
            self._began = time.monotonic()
            self._thread = threading.Thread(target=self._run, name="RadioPlayer", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the playback. Waits up to ``timeout`` seconds for the player thread - a thread that still loads a
        playlist ends by itself without starting the stream."""
        with self._lock:
            self._stopped.set()
            pipeline, self._pipeline = self._pipeline, None
            thread, self._thread = self._thread, None
            self._state = RadioState.STOPPED
        if pipeline is not None:
            pipeline.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._logger.info("stopped")

    def _set_state(self, state: RadioState, error: str | None = None) -> bool:
        """Set the state unless the player has been stopped (False then). Called with ``_lock`` held."""
        if self._stopped.is_set():
            return False
        self._state, self._error = state, error
        if self._on_state is not None:
            self._on_state(state)
        return True

    def _run(self) -> None:
        attempt = 0
        while not self._stopped.is_set():
            with self._lock:
                if not self._set_state(RadioState.CONNECTING, self._error):
                    break
            result = self._play_once()
            if result is None:  # stopped
                break
            error, stable = result
            if stable:  # the stream works in general - start over with the shortest delay
                attempt = 0
            delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
            attempt += 1
            self._logger.warning(
                "stream '%s' failed: %s - connecting again in %.0f s", self.url, error, delay,
                extra=self._ended(error, delay),
            )  # fmt: skip
            with self._lock:
                if not self._set_state(RadioState.RETRYING, error):
                    break
            self._stopped.wait(delay)

    # --- what the start of a connection took (docs/developer/library.md, section 4.22) -------------------------------

    def _first_output(self) -> None:
        connection = self._connection
        if connection is None:
            return
        connection.first_output()
        measured = connection.startup(self._buffer)
        self._logger.info(
            "first audio from the stream after %s ms (heard about %s ms after the start of the connection)",
            measured["first_audio_ms"], measured["audible_ms_estimated"],
        )  # fmt: skip

    def _underrun(self, ms: float) -> None:
        if self._connection is not None:
            self._connection.underrun(ms)

    def _report_startup(self, connection: _Connection) -> None:
        """Once per connection: when its first seconds are over, or when it ends before."""
        with connection.lock:
            if connection.reported:
                return
            connection.reported = True
        measured = connection.startup(self._buffer)
        self._logger.info(
            "start of the stream: playlist %s ms, ffmpeg and aplay %s ms, first audio %s ms, heard about %s ms; "
            "%d breaks of the sound (%d ms) in the first %.0f s",
            measured["resolve_ms"], measured["pipeline_ms"], measured["first_audio_ms"],
            measured["audible_ms_estimated"], measured["underruns"], measured["underrun_ms"], STARTUP_WINDOW,
            extra=diagnostic("radio.startup", text=f"start of a stream of {self._host()}", host=self._host(),
                             **measured),
        )  # fmt: skip

    def _ended(self, error: str, delay: float) -> dict[str, Any]:
        """The structured part of the warning about a connection that ended."""
        connection = self._connection
        params: dict[str, Any] = {"reason": _reason(error), "delay_s": delay, "host": self._host()}
        if connection is not None:
            self._report_startup(connection)
            with connection.lock:
                params["attempt"] = connection.attempt
                params["played_s"] = (
                    None if connection.started is None else round(time.monotonic() - connection.started)
                )
                params["underruns"] = len(connection.underruns)
        text = f"a stream of {self._host()} ended ({params['reason']}): connecting again in {delay:.0f} s"
        return diagnostic("radio.retry", text=text, **params)

    def _host(self) -> str:
        """The station by its host - never the whole address, which may carry a token."""
        return urllib.parse.urlsplit(self.url).hostname or "?"

    def _play_once(self) -> tuple[str, bool] | None:
        """Load and play the stream until it ends. Returns (error, played for at least :data:`STABLE_PLAYBACK`
        seconds) or ``None`` if the player has been stopped."""
        connection = self._connection = _Connection(self._attempts, self._began)
        self._attempts += 1
        try:
            source = self._resolve(self.url)
        except (OSError, ValueError) as e:
            connection.resolved = time.monotonic()
            return f"cannot load playlist: {e}", False
        connection.resolved = time.monotonic()
        if source != self.url:
            connection.kind = "playlist"
            self._logger.info("playlist '%s' -> stream '%s'", self.url, source)
        elif urllib.parse.urlsplit(self.url).path.lower().endswith((".m3u8", *PLAYLIST_EXTENSIONS)):
            connection.kind = "hls"
        with self._lock:
            if self._stopped.is_set():
                return None
            try:
                pipeline = self._pipeline_factory(source)
            except OSError as e:
                return f"cannot start ffmpeg/aplay: {e}", False
            self._pipeline = pipeline
            self._set_state(RadioState.PLAYING)
        connection.started = started = time.monotonic()
        self._logger.info("playing stream '%s'", source)
        if self._on_play is not None:
            self._on_play()
        report = threading.Timer(STARTUP_WINDOW, self._report_startup, (connection,))
        report.name, report.daemon = "RadioPlayer.startup", True
        report.start()
        try:
            success, errors = pipeline.wait()
        finally:
            report.cancel()
        self._report_startup(connection)  # a connection that ends within its first seconds - also by a stop
        with self._lock:
            if self._stopped.is_set() or self._pipeline is not pipeline:
                return None
            self._pipeline = None
        return errors or (
            "stream ended" if success else "playback failed"
        ), time.monotonic() - started >= STABLE_PLAYBACK
