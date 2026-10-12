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
            lambda source: simpleplayer.AudioPipeline(source, output=audio_output.current()[0], stream=True)
        )
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._pipeline: Pipeline | None = None
        self._thread: threading.Thread | None = None
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
            self._logger.warning("stream '%s' failed: %s - connecting again in %.0f s", self.url, error, delay)
            with self._lock:
                if not self._set_state(RadioState.RETRYING, error):
                    break
            self._stopped.wait(delay)

    def _play_once(self) -> tuple[str, bool] | None:
        """Load and play the stream until it ends. Returns (error, played for at least :data:`STABLE_PLAYBACK`
        seconds) or ``None`` if the player has been stopped."""
        try:
            source = self._resolve(self.url)
        except (OSError, ValueError) as e:
            return f"cannot load playlist: {e}", False
        if source != self.url:
            self._logger.info("playlist '%s' -> stream '%s'", self.url, source)
        with self._lock:
            if self._stopped.is_set():
                return None
            try:
                pipeline = self._pipeline_factory(source)
            except OSError as e:
                return f"cannot start ffmpeg/aplay: {e}", False
            self._pipeline = pipeline
            self._set_state(RadioState.PLAYING)
        self._logger.info("playing stream '%s'", source)
        if self._on_play is not None:
            self._on_play()
        started = time.monotonic()
        success, errors = pipeline.wait()
        with self._lock:
            if self._stopped.is_set() or self._pipeline is not pipeline:
                return None
            self._pipeline = None
        return errors or (
            "stream ended" if success else "playback failed"
        ), time.monotonic() - started >= STABLE_PLAYBACK
