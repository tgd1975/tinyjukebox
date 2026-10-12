"""DummyPlayer (and the common AAlbumPlayer logic) - timed by the ManualClock."""

import os

import pytest

from tests.support import CallRecorder, prepare_targets
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.data.targets import Targets


@pytest.fixture
def album_root(tmp_path):
    """targets.json of 'testdata_one' (album '1' = simple_album_a, five tracks of 1 to 5 s) in tmp_path."""
    return prepare_targets(tmp_path, "testdata_one", ["simple_album_a"])[1]


@pytest.fixture
def targets(album_root, logger):
    return Targets(os.path.join(os.path.dirname(album_root), "targets.json"), album_root, logger)


@pytest.fixture
def player(targets, logger, clock):
    p = DummyPlayer(logger, targets.get_target("1"), CallRecorder(), CallRecorder(), clock=clock)
    yield p
    p.stop_player()


def test_check_testdata(targets, album_root):
    assert targets.sanity_check()
    assert len(targets.targets) == 1
    target = targets.get_target("1")
    assert isinstance(target, TargetAlbum)
    assert target.target_id == "1"
    assert target.target_type == TargetType.album
    assert target.created == "2020-12-01T12:34:56.789Z"
    assert target.full_album_folder == os.path.join(album_root, "simple_album_a")
    assert target.root_folder == album_root
    assert target.album_folder == "simple_album_a"
    assert target.playlist == "playlist.m3u"
    assert target.get_tracks() == [os.path.join(album_root, "simple_album_a", f"{i}.mp3") for i in range(1, 6)]


def test_init(player, album_root, clock):
    assert len(player._tracks) == 5

    assert not player.album_end_reached()
    assert player.album_time_played() == 0
    assert player.album_track_count() == 5

    assert player.current_track_id() == 0
    assert player.current_track_filename() == os.path.join(album_root, "simple_album_a", "1.mp3")
    assert not player.current_track_end_reached()
    assert player.current_track_length() == pytest.approx(1, abs=0.02)
    assert player.current_track_time_played() == 0
    assert player.current_track_progress() == 0
    assert player.current_track_time_left() == player.current_track_length()

    assert not player.is_playing()

    clock.advance(player.current_track_length() * 1.1)
    assert player._end_of_track_hook.count == 0
    assert player._end_of_album_hook.count == 0


def test_tracks_end_one_after_another(player, clock):
    player.play()
    clock.advance(player.current_track_length())
    assert player._end_of_track_hook.count == 1
    assert player.current_track_end_reached()
    assert player.to_next()
    assert player.current_track_id() == 1
    assert player.is_playing()
    assert player.album_time_played() == pytest.approx(player.get_track(0).track_length())

    player.set_track(4)
    clock.advance(player.current_track_length())
    assert player._end_of_track_hook.count == 1  # the last track ends the album
    assert player._end_of_album_hook.count == 1
    assert player.album_end_reached()
    assert player.album_time_left() == pytest.approx(0)
    assert player.album_progress() == pytest.approx(1)


def test_pause_keeps_position(player, clock):
    player.play()
    clock.advance(0.5)
    player.pause()
    assert not player.is_playing()
    clock.advance(10)
    assert player.current_track_time() == pytest.approx(0.5)
    assert player._end_of_track_hook.count == 0
    player.play()
    clock.advance(player.current_track_length() - 0.5)
    assert player._end_of_track_hook.count == 1


def test_outdated_end_of_track_is_ignored(player, clock):
    """An end of track of an old playback (track paused, restarted or changed meanwhile) must not skip a track."""
    player.play()
    old_play_id = player.get_track(0).play_id
    player.pause()
    player._on_track_end(0, old_play_id)  # paused
    assert player._end_of_track_hook.count == 0
    assert not player.current_track_end_reached()

    player.play()
    player._on_track_end(0, old_play_id)  # playing again - with a new play id
    assert player._end_of_track_hook.count == 0
    assert player.current_track_id() == 0
    assert player.is_playing()

    player.set_track(1)
    player._on_track_end(0, player.get_track(0).play_id)  # track 0 is not the current track any more
    assert player._end_of_track_hook.count == 0
    assert player._end_of_album_hook.count == 0
    assert player.current_track_id() == 1
    assert not player.current_track_end_reached()

    track = player.get_track(1)
    first_play_id = track.play_id
    player.pause()
    player.play()
    track._end_of_track(first_play_id)  # outdated end reported by the track itself
    assert player._end_of_track_hook.count == 0
    assert not track.end_of_track_reached()
    assert player.current_track_id() == 1
    assert player.is_playing()


def test_end_of_track_after_pause_is_ignored(player, clock):
    """The track's timer fires right after a pause (race in a real player) - nothing happens."""
    player.play()
    track = player.get_track(0)
    play_id = track.play_id
    clock.advance(player.current_track_length() - 0.1)
    player.pause()
    track._end_of_track(play_id)
    clock.advance(1)
    assert player._end_of_track_hook.count == 0
    assert not track.end_of_track_reached()
    assert player.current_track_time() == pytest.approx(player.current_track_length() - 0.1)
