"""DummyTrack: length, position and the play/stop life cycle of a track - timed by the ManualClock."""

import os

import pytest

from tests.support import CallRecorder, prepare_targets
from tinyjukebox.albumplayer.dummyplayer import DummyTrack
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
def filename(targets):
    return targets.get_target("1").get_tracks()[0]


@pytest.fixture
def hook():
    return CallRecorder()


def test_check_testdata(targets, album_root):
    assert targets is not None
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
    expected = [os.path.join(album_root, "simple_album_a", f"{i}.mp3") for i in range(1, 6)]
    assert target.get_tracks() == expected
    for index, track in enumerate(expected):
        assert target.get_tracks()[index] == track


def test_init(filename, album_root, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, clock=clock)
    assert track.track_length() == pytest.approx(1, abs=0.05)
    assert track.track_frequency() == 48000
    assert track.filename == os.path.join(album_root, "simple_album_a", "1.mp3")
    assert track.time_played() == 0
    assert track.time_left() == track.track_length()
    assert track.progress() == 0
    assert track._start_time == 0
    assert not track.is_playing()
    assert not hook.is_set()
    assert not track.end_of_track_reached()


def test_init_does_not_play(filename, album_root, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, clock=clock)
    assert track.track_length() == pytest.approx(1, abs=0.05)
    assert track.track_frequency() == 48000
    assert track.filename == os.path.join(album_root, "simple_album_a", "1.mp3")
    assert track.time_played() == 0
    assert track.time_left() == track.track_length()
    assert track.progress() == 0
    assert track._start_time == 0
    assert not track.is_playing()
    assert not track.end_of_track_reached()

    clock.advance(track.track_length() * 1.1)
    assert not hook.is_set()
    assert clock.pending() == 0


def test_init_wrong_filename(hook, logger, clock):
    with pytest.raises(ValueError, match="unknown filename"):
        DummyTrack("lalellufakjslf", logger, hook, clock=clock)
    with pytest.raises(ValueError, match="unknown filename"):
        DummyTrack(None, logger, hook, clock=clock)
    clock.advance(1.5)
    assert not hook.is_set()


def test_init_start_time(filename, album_root, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, 0.5, clock=clock)
    assert track._start_time == 0.5
    assert track.track_length() == pytest.approx(1, abs=0.05)
    assert track.track_frequency() == 48000
    assert track.filename == os.path.join(album_root, "simple_album_a", "1.mp3")
    assert track.time_played() == 0.5
    assert track.time_left() == track.track_length() - 0.5
    assert track.progress() == pytest.approx(0.5 / track.track_length(), abs=0.001)
    assert not track.is_playing()
    assert not hook.is_set()
    assert not track.end_of_track_reached()


def test_init_start_time_is_corrected(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, 0.5, clock=clock)
    assert track._start_time == 0.5
    assert track.track_length() == pytest.approx(1, abs=0.05)

    track = DummyTrack(filename, logger, hook, -1, clock=clock)
    assert track._start_time == 0

    track = DummyTrack(filename, logger, hook, 2, clock=clock)
    assert track._start_time == track.track_length()

    assert not hook.is_set()


def test_play(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, clock=clock)
    assert not track.is_playing()
    assert not track.end_of_track_reached()

    track.play()
    assert track.is_playing()
    assert not track.end_of_track_reached()
    clock.advance(track.track_length() * 0.9)
    assert not hook.is_set()
    assert track.is_playing()
    assert not track.end_of_track_reached()
    clock.advance(track.track_length() * 0.2)
    assert hook.calls == [(track.play_id,)]
    assert track.end_of_track_reached()
    assert not track.is_playing()
    assert track.time_played() == track.track_length()

    track.stop()
    assert hook.calls == [(track.play_id,)]


def test_play_and_stop(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, clock=clock)
    assert not track.is_playing()
    assert not track.end_of_track_reached()

    track.play()
    assert track.is_playing()
    assert not track.end_of_track_reached()

    clock.advance(0.2)
    assert track.is_playing()
    assert not track.end_of_track_reached()

    track.stop()
    assert not track.is_playing()
    assert not track.end_of_track_reached()
    assert track.time_played() == pytest.approx(0.2)  # stopping keeps the position

    clock.advance(track.track_length() * 0.9)
    assert not hook.is_set()
    assert not track.is_playing()
    assert not track.end_of_track_reached()
    assert track.time_played() == pytest.approx(0.2)

    track.stop()
    assert clock.pending() == 0


def test_play_from_start_time(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, 0.5, clock=clock)
    assert not track.is_playing()
    assert not track.end_of_track_reached()

    track.play()
    assert track.is_playing()
    assert not track.end_of_track_reached()
    clock.advance(track.track_length() * 0.9 - 0.5)
    assert not hook.is_set()
    assert track.is_playing()
    assert not track.end_of_track_reached()
    clock.advance(track.track_length() * 0.2)
    assert hook.is_set()
    assert track.end_of_track_reached()
    assert not track.is_playing()

    track.stop()


def test_progress_reports(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, clock=clock)
    steps = 10
    length = track.track_length()
    length_per_step = length / steps

    track.play()
    for step in range(steps):
        assert track.time_played() == pytest.approx(step * length_per_step, abs=0.05)
        assert track.time_left() == pytest.approx(length - step * length_per_step, abs=0.05)
        assert track.progress() == pytest.approx(step / steps, abs=0.05)
        clock.advance(length_per_step)

    clock.advance(0.2)
    assert hook.is_set()
    assert track.time_played() == pytest.approx(length, abs=0.05)
    assert track.time_left() == 0
    assert track.progress() == 1
    track.stop()


def test_progress_reports_start_time(filename, hook, logger, clock):
    track = DummyTrack(filename, logger, hook, 0.5, clock=clock)
    steps = 10
    length = track.track_length()
    length_per_step = length / steps

    track.play()
    for step in range(5, steps):
        assert track.time_played() == pytest.approx(step * length_per_step, abs=0.05)
        assert track.time_left() == pytest.approx(length - step * length_per_step, abs=0.05)
        assert track.progress() == pytest.approx(step / steps, abs=0.05)
        clock.advance(length_per_step)

    clock.advance(0.2)
    assert hook.is_set()
    assert track.time_played() == pytest.approx(length, abs=0.05)
    assert track.time_left() == 0
    assert track.progress() == 1
    track.stop()


def test_resume_after_stop(filename, hook, logger, clock):
    """play continues at the position of the last stop and ends after the remaining time."""
    track = DummyTrack(filename, logger, hook, clock=clock)
    track.play()
    clock.advance(0.4)
    track.stop()
    clock.advance(5)
    track.play()
    assert track.time_played() == pytest.approx(0.4)
    clock.advance(track.track_length() - 0.4 - 0.01)
    assert not hook.is_set()
    clock.advance(0.02)
    assert hook.calls == [(track.play_id,)]


def test_outdated_end_of_track_is_ignored(filename, hook, logger, clock):
    """The end of a playback that has been stopped (or restarted) meanwhile does not end the track."""
    track = DummyTrack(filename, logger, hook, clock=clock)
    track.play()
    old_play_id = track.play_id
    track.stop()
    track._end_of_track(old_play_id)
    assert not hook.is_set()
    assert not track.end_of_track_reached()

    track.play()
    assert track.play_id != old_play_id
    track._end_of_track(old_play_id)
    assert not hook.is_set()
    assert track.is_playing()
    assert not track.end_of_track_reached()
    track.stop()
