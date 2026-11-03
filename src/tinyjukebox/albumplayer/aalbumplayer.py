"""Common implementation of tracks and album players."""

import functools
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from tinyjukebox.albumplayer.clock import SYSTEM_CLOCK, Clock
from tinyjukebox.albumplayer.player_interface import Hook, PlayerInterface, TrackFailedHook
from tinyjukebox.core.logging import get_child
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.metadata import mp3_info

__all__ = ["AAlbumPlayer", "ATrack", "TrackEndHook"]

TrackEndHook = Callable[[int], object]
"""Called with the play id of the playback that reached the end of the track."""


class ATrack(ABC):
    """One mp3 file: length, position and the play/stop life cycle.

    Every :meth:`play` gets a new *play id*. Implementations report the end of a playback with
    :meth:`_end_of_track` and its play id; ends of playbacks that have been stopped or restarted meanwhile are
    ignored. This avoids a race between a track ending by itself and a stop/next at the same moment.

    Stopping keeps the position - the next :meth:`play` resumes there.
    """

    def __init__(
        self,
        filename: str | None,
        logger: logging.Logger,
        end_of_track_hook: TrackEndHook,
        start_time: float = 0,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._logger = get_child(logger, str(filename))
        if not filename or not os.path.isfile(filename):  # no glob - '[...]' in file names is no pattern
            message = f"unknown filename '{filename}'"
            self._logger.error(message)
            raise ValueError(message)
        self.filename = filename
        self._length, self._frequency = mp3_info(filename)
        self._end_of_track_hook = end_of_track_hook
        self.output_lost_hook: TrackEndHook | None = None  # called with the play id if the headset output failed
        self.failed_hook: Callable[[str], object] | None = (
            None  # called with what the decoder said about a damaged file
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._is_playing = False
        self._end_of_track_reached = False
        self._play_id = 0
        self._play_start: float | None = None
        self._start_time = 0.0
        self.set_start_time(start_time)
        self._logger.debug("__init__ - length: %s, frequency: %s", self._length, self._frequency)

    @property
    def play_id(self) -> int:
        """Id of the latest playback."""
        return self._play_id

    def set_start_time(self, start_time: float) -> None:
        new_time = min(self.track_length(), max(0, start_time))
        if start_time != new_time:
            self._logger.warning("set_start_time - corrected out of bounds start time %s to %s.", start_time, new_time)
        self._start_time = new_time

    def time_played(self) -> float:
        start = self._play_start
        if start is None:
            return self._start_time
        return self._clock.now() - start + self._start_time

    def track_length(self) -> float:
        return self._length

    def track_frequency(self) -> int:
        return self._frequency

    def progress(self) -> float:
        return min(1.0, self.time_played() / self.track_length()) if self.track_length() > 0 else 1.0

    def time_left(self) -> float:
        return max(0.0, self.track_length() - self.time_played())

    def is_playing(self) -> bool:
        return self._is_playing

    def end_of_track_reached(self) -> bool:
        return self._end_of_track_reached

    def play(self) -> None:
        with self._lock:
            if self._is_playing:
                return
            self._play_id += 1
            play_id = self._play_id
            self._is_playing = True
            self._end_of_track_reached = False
            self._play_start = self._clock.now()
        self._logger.info("play (%s from %.1fs)", play_id, self._start_time)
        try:
            self._play(play_id)
        except BaseException:
            with self._lock:
                self._is_playing = False
            raise

    def stop(self) -> None:
        with self._lock:
            if not self._is_playing:
                return
            position = self.time_played()
            self._is_playing = False
            self._play_start = None
            self._start_time = min(position, self.track_length())
        self._logger.info("stop at %.1fs", self._start_time)
        self._stop()

    def _end_of_track(self, play_id: int) -> None:
        """To be called by implementations when the playback ``play_id`` has reached the end of the file."""
        with self._lock:
            if not self._is_playing or play_id != self._play_id:
                self._logger.debug("_end_of_track - playback %s has been stopped already - ignored", play_id)
                return
            self._is_playing = False
            self._end_of_track_reached = True
            self._play_start = None
            self._start_time = self.track_length()
        self._logger.info("_end_of_track")
        self._end_of_track_hook(play_id)

    def _output_lost(self, play_id: int) -> None:
        """To be called by implementations if the output of the playback ``play_id`` failed (headset gone) - the
        track stops at its position, like :meth:`stop`."""
        with self._lock:
            if not self._is_playing or play_id != self._play_id:
                return
            position = self.time_played()
            self._is_playing = False
            self._play_start = None
            self._start_time = min(position, self.track_length())
        self._logger.warning("_output_lost at %.1fs", self._start_time)
        if self.output_lost_hook is not None:
            self.output_lost_hook(play_id)

    @abstractmethod
    def _play(self, play_id: int) -> None:
        """Start the playback from ``_start_time``."""

    @abstractmethod
    def _stop(self) -> None:
        """Stop the playback immediately."""


class _PendingTrack(ATrack):
    """Stands for a track whose file has not arrived yet: it has no length and plays nothing. The album player
    replaces it as soon as the file is there (:meth:`AAlbumPlayer.current_track_ready`)."""

    def __init__(self, filename: str, logger: logging.Logger) -> None:
        self._logger = get_child(logger, filename)
        self.filename = filename
        self._length, self._frequency = 0.0, 0
        self._end_of_track_hook = lambda play_id: None
        self.output_lost_hook = None
        self.failed_hook = None
        self._clock = SYSTEM_CLOCK
        self._lock = threading.Lock()
        self._is_playing = False
        self._end_of_track_reached = False
        self._play_id = 0
        self._play_start = None
        self._start_time = 0.0

    def set_start_time(self, start_time: float) -> None:
        self._start_time = max(0.0, start_time)  # kept for the real track

    def time_played(self) -> float:
        return self._start_time

    def _play(self, play_id: int) -> None:
        pass

    def _stop(self) -> None:
        pass


class AAlbumPlayer(PlayerInterface):
    """Album player on top of :class:`ATrack` implementations.

    ``_rlock`` guards the current track and the playback state; it is held while tracks are started and stopped
    (short - no waiting for the end of a track).
    """

    def __init__(
        self, logger: logging.Logger, album: TargetAlbum, end_of_track_hook: Hook, end_of_album_hook: Hook
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._album = album
        self._logger.info("__init__ for %s", album.to_text())
        self._end_of_track_hook = end_of_track_hook
        self._end_of_album_hook = end_of_album_hook
        self._output_lost_hook: Hook | None = None
        self._track_failed_hook: TrackFailedHook | None = None
        self._rlock = threading.RLock()
        self._playback_active = False
        self._album_end_reached = False
        self._track_end_reached = False
        self._tracks: list[ATrack] = [
            self._new_track(index, filename) for index, filename in enumerate(album.get_tracks())
        ]
        if not self._tracks:
            message = "no tracks provided for album - an album must contain at least one track."
            self._logger.error(message)
            raise ValueError(message)
        self._album_length_raw = sum(track.track_length() for track in self._tracks)
        self._current_track_id = 0
        self._current_track = self._tracks[0]

    @abstractmethod
    def _create_track(self, filename: str, end_of_track_hook: TrackEndHook) -> ATrack: ...

    def _new_track(self, index: int, filename: str) -> ATrack:
        if self._album.incomplete and not os.path.isfile(filename):
            return _PendingTrack(filename, self._logger)  # the album is being fetched from the library
        track = self._create_track(filename, functools.partial(self._on_track_end, index))
        track.output_lost_hook = functools.partial(self._on_output_lost, index)
        track.failed_hook = functools.partial(self._on_track_failed, index)
        return track

    def current_track_ready(self) -> bool:
        with self._rlock:
            pending = self._current_track
            if isinstance(pending, _PendingTrack) and os.path.isfile(pending.filename):
                track = self._new_track(self._current_track_id, pending.filename)
                track.set_start_time(pending.time_played())
                self._tracks[self._current_track_id] = self._current_track = track
                self._album_length_raw = sum(track.track_length() for track in self._tracks)
            return not isinstance(self._current_track, _PendingTrack)

    def set_output_lost_hook(self, hook: Hook) -> None:
        self._output_lost_hook = hook

    def set_track_failed_hook(self, hook: TrackFailedHook) -> None:
        self._track_failed_hook = hook

    def start_player(self) -> None:
        self._logger.info("start_player")

    def stop_player(self) -> None:
        self._logger.info("stop_player")
        with self._rlock:
            self._playback_active = False
            self._current_track.stop()

    # --- playback ----------------------------------------------------------------------------------------------

    def play(self) -> None:
        """:raises OSError: if the track cannot be started (ffmpeg or aplay) - the player is not playing then."""
        with self._rlock:
            if not self._playback_active:
                self._logger.info("play")
                self._playback_active = True
                try:
                    self._current_track.play()
                except BaseException:
                    self._playback_active = False
                    raise

    def pause(self) -> None:
        with self._rlock:
            if self._playback_active:
                self._logger.info("pause")
                self._playback_active = False
                self._current_track.stop()

    def is_playing(self) -> bool:
        return self._playback_active

    def set_track(self, track_id: int, track_start_time: float = 0) -> None:
        with self._rlock:
            track_id = min(max(track_id, 0), len(self._tracks) - 1)
            self._logger.info("set_track %s at %s", track_id, track_start_time)
            self._track_end_reached = False
            self._album_end_reached = False
            self._current_track.stop()
            self._current_track_id = track_id
            self._current_track = self._tracks[track_id]
            self._current_track.set_start_time(track_start_time)
            if self._playback_active:
                self._current_track.play()

    def to_first(self) -> None:
        self.set_track(0)

    def to_next(self) -> bool:
        with self._rlock:
            if self._current_track_id >= len(self._tracks) - 1:
                self._end_of_album()
                return False
            self.set_track(self._current_track_id + 1)
            return True

    def to_prev(self) -> None:
        with self._rlock:
            self.set_track(self._current_track_id - 1)

    def rewind_track(self) -> None:
        with self._rlock:
            self.set_track(self._current_track_id, 0)

    def _on_track_end(self, index: int, play_id: int) -> None:
        """End-of-track hook of the tracks (player thread of the track)."""
        with self._rlock:
            track = self._tracks[index]
            if index != self._current_track_id or play_id != track.play_id or not self._playback_active:
                self._logger.info("_on_track_end - end of an outdated playback of track %s - ignored", index)
                return
            self._track_end_reached = True
            if index == len(self._tracks) - 1:
                self._end_of_album()
            else:
                self._end_of_track_hook()

    def _on_output_lost(self, index: int, play_id: int) -> None:
        """Output-lost hook of the tracks: the playback stops (the track keeps its position)."""
        with self._rlock:
            track = self._tracks[index]
            if index != self._current_track_id or play_id != track.play_id or not self._playback_active:
                return
            self._playback_active = False
        if self._output_lost_hook is not None:
            self._output_lost_hook()

    def _on_track_failed(self, index: int, error: str) -> None:
        """Failed hook of the tracks: the file of the track is damaged or cannot be decoded at all."""
        if self._track_failed_hook is not None:
            self._track_failed_hook(index, error)

    def _end_of_album(self) -> None:
        with self._rlock:
            self._logger.info("_end_of_album")
            self._current_track.stop()
            self._album_end_reached = True
            self._track_end_reached = True
        self._end_of_album_hook()

    # --- information -------------------------------------------------------------------------------------------

    def album_end_reached(self) -> bool:
        return self._album_end_reached

    def current_track_end_reached(self) -> bool:
        return self._track_end_reached

    def current_track_id(self) -> int:
        return self._current_track_id

    def current_track_filename(self) -> str:
        return self._current_track.filename

    def current_track_length(self) -> float:
        return self._current_track.track_length()

    def current_track_time_left(self) -> float:
        return self._current_track.time_left()

    def current_track_time_played(self) -> float:
        return self._current_track.time_played()

    def current_track_time(self) -> float:
        return self.current_track_time_played()

    def current_track_progress(self) -> float:
        return self._current_track.progress()

    def album_track_count(self) -> int:
        return len(self._tracks)

    def album_length(self, pause_between_tracks: float = 0) -> float:
        return self._album_length_raw + pause_between_tracks * (len(self._tracks) - 1)

    def album_time_played(self, pause_between_tracks: float = 0) -> float:
        with self._rlock:
            index = self._current_track_id
            played = sum(track.track_length() for track in self._tracks[:index])
            return played + index * pause_between_tracks + self._current_track.time_played()

    def album_time_left(self, pause_between_tracks: float = 0) -> float:
        return self.album_length(pause_between_tracks) - self.album_time_played(pause_between_tracks)

    def album_progress(self, pause_between_tracks: float = 0) -> float:
        length = self.album_length(pause_between_tracks)
        return self.album_time_played(pause_between_tracks) / length if length > 0 else 0

    def get_track(self, track_id: int) -> ATrack:
        return self._tracks[track_id]
