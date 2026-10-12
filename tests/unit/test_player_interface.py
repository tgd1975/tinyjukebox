"""PlayerInterface: the abstract interface of the album players."""

import pytest

from tinyjukebox.albumplayer.player_interface import PlayerInterface

EXPECTED_METHODS = {
    "play", "pause", "stop_player", "start_player", "set_track", "to_first", "to_next", "to_prev", "rewind_track",
    "is_playing", "album_end_reached", "current_track_end_reached", "current_track_id", "current_track_filename",
    "current_track_time", "current_track_length", "current_track_time_left", "album_track_count", "album_length",
}  # fmt: skip


class MockPlayer(PlayerInterface):
    def play(self):
        pass

    def pause(self):
        pass

    def stop_player(self):
        pass

    def start_player(self):
        pass

    def set_track(self, track_id, track_start_time=0):
        pass

    def to_first(self):
        pass

    def to_next(self):
        return True

    def to_prev(self):
        pass

    def rewind_track(self):
        pass

    def is_playing(self):
        return True

    def album_end_reached(self):
        return False

    def current_track_end_reached(self):
        return False

    def current_track_id(self):
        return 0

    def current_track_filename(self):
        return "1.mp3"

    def current_track_time(self):
        return 0.0

    def current_track_length(self):
        return 0.0

    def current_track_time_left(self):
        return 0.0

    def album_track_count(self):
        return 0

    def album_length(self, pause_between_tracks=0): return 0.0  # fmt: skip


def test_interface_definition():
    assert hasattr(PlayerInterface, "__abstractmethods__")
    assert set(PlayerInterface.__abstractmethods__) == EXPECTED_METHODS


def test_interface_inheritance():
    player = MockPlayer()
    assert isinstance(player, PlayerInterface)
    assert isinstance(player, MockPlayer)


def test_incomplete_implementation_cannot_be_instantiated():
    methods = {name: getattr(MockPlayer, name) for name in EXPECTED_METHODS - {"current_track_filename"}}
    incomplete = type("Incomplete", (PlayerInterface,), methods)
    with pytest.raises(TypeError, match="current_track_filename"):
        incomplete()
