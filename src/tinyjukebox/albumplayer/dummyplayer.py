"""Player without audio: a track 'plays' for its length (timer) - for tests with a manually advanced clock."""

import logging

from tinyjukebox.albumplayer.aalbumplayer import AAlbumPlayer, ATrack, TrackEndHook
from tinyjukebox.albumplayer.clock import SYSTEM_CLOCK, Clock, TimerHandle
from tinyjukebox.albumplayer.player_interface import Hook
from tinyjukebox.data.target import TargetAlbum

__all__ = ["DummyPlayer", "DummyTrack"]


class DummyTrack(ATrack):
    """Ends ``time_left()`` seconds after :meth:`play` (according to ``clock``)."""

    def __init__(
        self,
        filename: str | None,
        logger: logging.Logger,
        end_of_track_hook: TrackEndHook,
        start_time: float = 0,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        super().__init__(filename, logger, end_of_track_hook, start_time, clock)
        self._timer: TimerHandle | None = None

    def _play(self, play_id: int) -> None:
        self._timer = self._clock.call_later(self.time_left(), lambda: self._end_of_track(play_id))

    def _stop(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()


class DummyPlayer(AAlbumPlayer):
    def __init__(
        self,
        logger: logging.Logger,
        album: TargetAlbum,
        end_of_track_hook: Hook,
        end_of_album_hook: Hook,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._clock = clock
        super().__init__(logger, album, end_of_track_hook, end_of_album_hook)

    def _create_track(self, filename: str, end_of_track_hook: TrackEndHook) -> DummyTrack:
        return DummyTrack(filename, self._logger, end_of_track_hook, clock=self._clock)
