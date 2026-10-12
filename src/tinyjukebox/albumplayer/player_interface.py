"""Interface of the album players as used by the play-album state machine."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeAlias

__all__ = ["Hook", "PlayerInterface", "TrackFailedHook"]

Hook: TypeAlias = Callable[[], object]
TrackFailedHook: TypeAlias = Callable[[int, str], object]
"""Called with the id of a track whose file is damaged or cannot be decoded at all, and what the decoder said."""


class PlayerInterface(ABC):
    """Plays the tracks of one album. Positions are in seconds, track ids are 0-based.

    The player calls the end-of-track hook when a track has been played to its end (and the end-of-album hook
    instead for the last track). Hooks are called from a player thread - they must not block.
    """

    def set_output_lost_hook(self, hook: Hook) -> None:  # noqa: B027 - optional, players without headset ignore it
        """``hook`` is called (instead of the end-of-track hook) if the headset output fails while a track plays -
        the track stops at its position."""

    def set_track_failed_hook(self, hook: TrackFailedHook) -> None:  # noqa: B027 - optional, as above
        """``hook`` is called if the decoder found the file of a track damaged - when the track has ended, before
        the end-of-track hook; the player goes on as after every track."""

    def current_track_ready(self) -> bool:
        """False while the file of the current track has not arrived yet - an album that is fetched from the
        library plays as soon as the track that is due is there."""
        return True

    @abstractmethod
    def start_player(self) -> None:
        """Acquire resources (before the first :meth:`play`)."""

    @abstractmethod
    def stop_player(self) -> None:
        """Stop playback and release all resources."""

    @abstractmethod
    def play(self) -> None:
        """Start or resume playback of the current track."""

    @abstractmethod
    def pause(self) -> None:
        """Pause playback - the position is kept."""

    @abstractmethod
    def is_playing(self) -> bool: ...

    @abstractmethod
    def set_track(self, track_id: int, track_start_time: float = 0) -> None:
        """Select a track (ids and times out of range are corrected); continues playing if the player plays."""

    @abstractmethod
    def to_first(self) -> None: ...

    @abstractmethod
    def to_next(self) -> bool:
        """Next track - False (and end of album) if the current track is the last one."""

    @abstractmethod
    def to_prev(self) -> None: ...

    @abstractmethod
    def rewind_track(self) -> None:
        """Back to the start of the current track."""

    @abstractmethod
    def album_end_reached(self) -> bool: ...

    @abstractmethod
    def current_track_end_reached(self) -> bool: ...

    @abstractmethod
    def current_track_id(self) -> int: ...

    @abstractmethod
    def current_track_filename(self) -> str: ...

    @abstractmethod
    def current_track_time(self) -> float:
        """Position within the current track."""

    @abstractmethod
    def current_track_length(self) -> float: ...

    @abstractmethod
    def current_track_time_left(self) -> float: ...

    @abstractmethod
    def album_track_count(self) -> int: ...

    @abstractmethod
    def album_length(self, pause_between_tracks: float = 0) -> float: ...
