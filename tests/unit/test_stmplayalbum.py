"""STMPlayAlbum - the state logic with the DummyPlayer and the ManualClock, one end-to-end test with SimplePlayer."""

import functools
import os

import pytest

from tests.support import ONE_SECOND_MP3, TAG_ALBUM, create_library, wait_for
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.albumplayer.simpleplayer import SimplePlayer
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, PLAYBACK, Button, PlaybackStatus
from tinyjukebox.rgbled import Color
from tinyjukebox.stm import base
from tinyjukebox.stm.base import PauseReason
from tinyjukebox.stm.stmplayalbum import BOTH_BUTTONS_WINDOW, BothButtons, States, STMPlayAlbum

TOPIC = BUTTON_PRESSED


@pytest.fixture
def library(tmp_path, logger):
    """Album "1" with three ~1 s tracks (only their length matters - the DummyPlayer plays nothing)."""
    return create_library(tmp_path, logger, track=ONE_SECOND_MP3)


def create(library, pubsub, rgbled, logger, player_factory, activity_values=None, target="1", **config):
    """STMPlayAlbum for album "1" (``target="2"``: tracks of 4, 3 and 5 s)."""
    cfg = {"rewind-max-time": 5, "update-schedule": 0.05, "pause-between-tracks": 0}
    cfg.update(config)
    activity = library.activities.get_activity(TAG_ALBUM)
    if activity_values is not None:
        activity.update(*activity_values)
    return STMPlayAlbum(cfg, pubsub, logger, rgbled, library.targets.get_target(target), activity, player_factory)


@pytest.fixture
def make_album(library, pubsub, rgbled, logger, clock):
    """Factory for started STMPlayAlbum instances (DummyPlayer with ``clock`` unless another factory is given)."""
    albums = []

    def factory(player_factory=None, lib=None, **kwargs):
        player_factory = player_factory or functools.partial(DummyPlayer, clock=clock)
        album = create(lib or library, pubsub, rgbled, logger, player_factory, **kwargs)
        albums.append(album)
        album.start()
        album.wait_until_idle()
        return album

    yield factory
    for album in albums:
        if album.get_state() != States.END:
            album.stop()


def state(album):
    return album.get_info()["state"]


def track(album):
    return album.get_info()["track"]


def plays_track(album, track_id):
    return state(album) == "play" and track(album)["track_id"] == track_id


def track_length(album):
    return album.player.current_track_length()


def test_start_resumes_from_activity(make_album, rgbled):
    a = make_album(activity_values=(1, 0.5, False))
    assert state(a) == "play"
    assert track(a)["track_id"] == 1
    assert track(a)["pos"] >= 0.5
    assert track(a)["length"] == pytest.approx(0.98, abs=0.05)
    assert rgbled.last() == ("color", Color.BLUE)


def test_finished_album_starts_from_beginning(make_album):
    a = make_album(activity_values=(2, 0.9, True))
    assert state(a) == "play"
    assert track(a)["track_id"] == 0
    assert track(a)["pos"] < 0.5


def test_next_and_prev_commands(make_album, pubsub):
    a = make_album()
    assert state(a) == "play"
    pubsub.deliver(TOPIC, Button.NEXT)
    a.wait_until_idle()
    assert track(a)["track_id"] == 1
    assert state(a) == "play"
    pubsub.deliver(TOPIC, Button.PREV)  # within rewind-max-time -> previous track
    a.wait_until_idle()
    assert track(a)["track_id"] == 0
    assert state(a) == "play"
    pubsub.deliver(TOPIC, Button.VOLUME_UP)  # ignored - for STMVolume
    a.wait_until_idle()
    assert state(a) == "play"
    assert track(a)["track_id"] == 0


def test_prev_held_plays_the_album_from_the_beginning(make_album, pubsub, clock):
    """As on the old box: the press goes back at once, the hold event starts the album again."""
    a = make_album(activity_values=(2, 0.3, False))
    assert plays_track(a, 2)
    pubsub.deliver(TOPIC, Button.PREV)
    a.wait_until_idle()
    assert plays_track(a, 1)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    a.wait_until_idle()
    assert plays_track(a, 0)
    assert track(a)["pos"] < 0.1
    pubsub.deliver(BUTTON_RELEASED, Button.PREV)
    pubsub.deliver(BUTTON_HELD, Button.NEXT)  # no meaning for the album
    pubsub.deliver(BUTTON_HELD, Button.VOLUME_UP)
    a.wait_until_idle()
    assert plays_track(a, 0)


def test_prev_held_restarts_a_finished_and_a_paused_album(make_album, pubsub, clock):
    a = make_album(activity_values=(2, 0.7, False))
    clock.advance(track_length(a) - 0.7)
    a.wait_until_idle()
    assert state(a) == "finished"
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    a.wait_until_idle()
    assert plays_track(a, 0)
    a.cmd_next_handler()
    a.cmd_pause()
    a.wait_until_idle()
    assert state(a) == "paused"
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    a.wait_until_idle()
    assert plays_track(a, 0)


@pytest.mark.parametrize("first", [Button.NEXT, Button.PREV])
def test_prev_held_together_with_next_does_not_restart(make_album, pubsub, first):
    """Both buttons held pair a headset (Headset) - the album only reacts on the two presses."""
    a = make_album(activity_values=(2, 0.3, False))
    second = Button.PREV if first is Button.NEXT else Button.NEXT
    pubsub.deliver(TOPIC, first)
    pubsub.deliver(TOPIC, second)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    a.wait_until_idle()
    assert state(a) != "play" or track(a)["track_id"] != 0
    pubsub.deliver(BUTTON_RELEASED, Button.NEXT)  # next released: prev is held alone
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    a.wait_until_idle()
    assert plays_track(a, 0)


def test_handlers_are_unsubscribed_on_stop(make_album, pubsub):
    a = make_album()
    assert len(pubsub.handlers(BUTTON_HELD)) == len(pubsub.handlers(BUTTON_RELEASED)) == 1
    a.stop()
    assert pubsub.handlers(BUTTON_PRESSED) == pubsub.handlers(BUTTON_HELD) == pubsub.handlers(BUTTON_RELEASED) == []


def test_both_buttons():
    now = [100.0]
    buttons = BothButtons(clock=lambda: now[0])
    assert not buttons.next_is_pressed()
    buttons.pressed(Button.NEXT)
    buttons.pressed(Button.PREV)
    assert buttons.next_is_pressed()
    buttons.released(Button.NEXT)
    assert not buttons.next_is_pressed()
    buttons.pressed("SIDEWAYS")  # any payload
    buttons.released("SIDEWAYS")

    # a press without release (web page, button of the headset) is forgotten: prev pressed later is held alone
    buttons.pressed(Button.NEXT)
    now[0] += BOTH_BUTTONS_WINDOW + 0.1
    buttons.pressed(Button.PREV)
    assert not buttons.next_is_pressed()
    buttons.released(Button.PREV)
    assert not buttons.next_is_pressed()  # hold event without press (web page): the time of the hold counts

    # next pressed while prev is down already
    buttons.pressed(Button.PREV)
    now[0] += 0.5
    buttons.pressed(Button.NEXT)
    now[0] += 60
    assert buttons.next_is_pressed()


def test_prev_after_rewind_max_time_rewinds_track(make_album, pubsub, clock):
    a = make_album(**{"rewind-max-time": 0.2})
    a.cmd_next_handler()
    a.wait_until_idle()
    assert track(a)["track_id"] == 1
    assert state(a) == "play"
    clock.advance(0.5)
    assert track(a)["pos"] > 0.4
    pubsub.deliver(TOPIC, Button.PREV)
    a.wait_until_idle()
    assert track(a)["pos"] < 0.3
    assert state(a) == "play"
    assert track(a)["track_id"] == 1


def test_album_plays_to_the_end(make_album, library, rgbled, clock):
    a = make_album(**{"pause-between-tracks": 0.05})
    for track_id in range(3):
        # the pause between the tracks is a (real time) timeout of the state machine
        assert wait_for(functools.partial(plays_track, a, track_id))
        a.wait_until_idle()  # the timeout event that has been observed is processed completely
        clock.advance(track_length(a))
        a.wait_until_idle()
    assert state(a) == "finished"
    assert track(a)["track_id"] == 2
    activity = library.activities.get_activity(TAG_ALBUM)
    assert activity.finished is True
    assert rgbled.last() == ("blink", Color.BLUE)


def test_end_of_track_without_pause_plays_next_track(make_album, clock):
    a = make_album()
    clock.advance(track_length(a))
    a.wait_until_idle()
    assert state(a) == "play"
    assert track(a)["track_id"] == 1
    assert track(a)["pos"] == 0


def test_commands_after_the_end(make_album, clock):
    a = make_album(activity_values=(2, 0.7, False))
    clock.advance(track_length(a) - 0.7)
    a.wait_until_idle()
    assert state(a) == "finished"
    a.cmd_next_handler()  # undefined in FINISHED - must be ignored without killing the machine
    a.wait_until_idle()
    assert state(a) == "finished"
    a.cmd_restart_album()
    a.wait_until_idle()
    assert state(a) == "play"
    assert track(a)["track_id"] == 0


def test_position_is_stored_periodically(make_album, library, clock):
    a = make_album()
    clock.advance(0.5)
    assert wait_for(lambda: library.activities.get_activity(TAG_ALBUM).track_time > 0.3)
    assert state(a) == "play"


def test_stop_stores_position_and_stops_playback(make_album, library, pubsub, clock):
    a = make_album()
    clock.advance(0.5)
    assert track(a)["pos"] > 0.3
    a.stop()
    assert a.get_state() == States.END
    activity = library.activities.get_activity(TAG_ALBUM)
    assert activity.track_id == 0
    assert activity.track_time > 0.3
    assert pubsub.handlers(TOPIC) == []
    assert not a.player.is_playing()
    assert clock.pending() == 0  # no end of track timer left


def test_album_plays_to_the_end_with_simpleplayer(make_album, tmp_path, logger, rgbled, pubsub):
    """End to end with real ffmpeg pipelines: three short tracks, a pause between them, stop afterwards."""
    folder = tmp_path / "short_library"
    folder.mkdir()
    lib = create_library(folder, logger)  # three 0.34 s tracks
    a = make_album(player_factory=SimplePlayer, lib=lib, **{"pause-between-tracks": 0.1})
    assert state(a) == "play"
    assert wait_for(lambda: state(a) == "finished", timeout=8)
    assert track(a)["track_id"] == 2
    activity = lib.activities.get_activity(TAG_ALBUM)
    assert activity.finished is True
    assert rgbled.last() == ("blink", Color.BLUE)
    a.stop()
    assert a.get_state() == States.END
    assert pubsub.handlers(TOPIC) == []
    assert all(a.player.get_track(i)._pipeline is None for i in range(3))


# --- pause ------------------------------------------------------------------------------------------------------------


def test_pause_keeps_position_and_play_continues(make_album, pubsub, rgbled, library, clock):
    a = make_album()
    clock.advance(0.4)
    pubsub.deliver(TOPIC, Button.PAUSE)
    a.wait_until_idle()
    assert state(a) == "paused"
    assert rgbled.last() == ("alternate", Color.WHITE, Color.BLUE)
    assert not a.player.is_playing()
    assert library.activities.get_activity(TAG_ALBUM).track_time == pytest.approx(0.4)
    clock.advance(5)  # nothing plays - the position stays
    assert track(a)["pos"] == pytest.approx(0.4)
    pubsub.deliver(TOPIC, Button.PAUSE)  # paused already
    pubsub.deliver(TOPIC, Button.VOLUME_UP)  # the volume does not end the pause
    a.wait_until_idle()
    assert state(a) == "paused"
    pubsub.deliver(TOPIC, Button.PLAY)
    a.wait_until_idle()
    assert state(a) == "play"
    assert track(a) == {**track(a), "track_id": 0, "pos": pytest.approx(0.4)}
    assert rgbled.last() == ("color", Color.BLUE)
    pubsub.deliver(TOPIC, Button.PLAY)  # plays already
    a.wait_until_idle()
    assert state(a) == "play"


def test_next_in_pause_continues_first_and_skips_then(make_album, clock):
    a = make_album()
    clock.advance(0.4)
    a.cmd_pause()
    a.cmd_next_handler()  # 1st press: continue at the same position
    a.wait_until_idle()
    assert plays_track(a, 0)
    assert track(a)["pos"] == pytest.approx(0.4)
    a.cmd_next_handler()  # 2nd press: next track
    a.wait_until_idle()
    assert plays_track(a, 1)


def test_prev_in_pause_goes_back_at_once(make_album, clock):
    a = make_album(activity_values=(1, 0.0, False), **{"rewind-max-time": 0.2})
    clock.advance(0.5)
    a.cmd_pause()
    a.cmd_prev_handler()  # after rewind-max-time: to the start of the track
    a.wait_until_idle()
    assert plays_track(a, 1)
    assert track(a)["pos"] == 0
    a.cmd_pause()
    a.cmd_prev_handler()  # within rewind-max-time: previous track
    a.wait_until_idle()
    assert plays_track(a, 0)


def test_restart_in_pause_and_pause_of_a_finished_album(make_album, clock):
    a = make_album(activity_values=(2, 0.7, False))
    a.cmd_pause()
    a.cmd_restart_album()
    a.wait_until_idle()
    assert plays_track(a, 0)
    a.player.set_track(2, 0.7)
    clock.advance(track_length(a))
    a.wait_until_idle()
    assert state(a) == "finished"
    a.cmd_pause()  # play/pause change nothing at the end of the album
    a.cmd_play()
    a.wait_until_idle()
    assert state(a) == "finished"


def test_pause_between_two_tracks_continues_with_the_next_track(make_album, clock):
    a = make_album(**{"pause-between-tracks": 30})
    clock.advance(track_length(a))
    a.wait_until_idle()
    assert state(a) == "delay"
    a.cmd_pause()
    a.wait_until_idle()
    assert state(a) == "paused"
    assert track(a)["track_id"] == 1
    assert track(a)["pos"] == 0
    a.cmd_play()
    a.wait_until_idle()
    assert plays_track(a, 1)


def test_playback_status_is_published(make_album, pubsub, clock):
    a = make_album(activity_values=(2, 0.0, False))
    a.cmd_pause()
    a.cmd_play()
    a.wait_until_idle()
    clock.advance(track_length(a))
    a.wait_until_idle()
    assert state(a) == "finished"
    a.cmd_restart_album()
    a.wait_until_idle()
    a.stop()
    assert pubsub.messages(PLAYBACK) == [
        PlaybackStatus.PLAYING, PlaybackStatus.PAUSED, PlaybackStatus.PLAYING, PlaybackStatus.STOPPED,
        PlaybackStatus.PLAYING, PlaybackStatus.STOPPED,
    ]  # fmt: skip


# --- headset: output lost / changed ---------------------------------------------------------------------------------


def test_lost_headset_pauses_and_its_return_continues(make_album, clock, rgbled, library):
    a = make_album(target="2", activity_values=(1, 0.0, False))
    clock.advance(2.5)
    a.cmd_output_lost()
    a.wait_until_idle()
    assert state(a) == "paused"
    assert a.pause.reason is PauseReason.OUTPUT
    assert track(a)["track_id"] == 1
    assert track(a)["pos"] == pytest.approx(1.5)  # a second back - the end has not been heard
    assert library.activities.get_activity(TAG_ALBUM).track_time == pytest.approx(1.5)
    assert rgbled.last() == ("alternate", Color.WHITE, Color.BLUE)
    a.cmd_output_lost()  # e.g. the failing output and the driver both report it
    a.cmd_output_changed()  # the headset is back
    a.wait_until_idle()
    assert plays_track(a, 1)
    assert track(a)["pos"] == pytest.approx(1.5)


def test_lost_headset_while_waiting_for_a_file_pauses(make_album, library):
    """The file of the track that is due has not arrived from the library yet: the headset goes. The box pauses - the
    file that arrives afterwards does not start the track (on the speaker)."""
    album = library.targets.get_target("1")
    first = album.get_tracks()[0]
    os.rename(first, first + ".away")
    album.incomplete = True
    a = make_album()
    assert a.get_state() == States.WAIT
    handled = []
    a.cmd_output_lost(then=lambda: handled.append(a.get_state()))
    a.wait_until_idle()
    assert a.get_state() == States.PAUSED
    assert a.pause.reason is PauseReason.OUTPUT
    assert handled == [States.PAUSED]  # called in the worker, after the loss was handled
    os.rename(first + ".away", first)
    a._file_arrived("1")
    a.wait_until_idle()
    assert a.get_state() == States.PAUSED
    a.cmd_play()
    a.wait_until_idle()
    assert a.get_state() == States.PLAY


def test_the_call_after_a_loss_also_comes_when_the_album_is_paused_already(make_album):
    a = make_album()
    a.cmd_pause()
    handled = []
    a.cmd_output_lost(then=lambda: handled.append(a.get_state()))
    a.wait_until_idle()
    assert handled == [States.PAUSED]
    assert a.pause.reason is PauseReason.USER


def test_a_card_that_cannot_be_written_stops_neither_the_album_nor_its_end(make_album, library, monkeypatch, caplog):
    """The card became read-only while the album plays: the album plays on, "next" works, the stop ends everything -
    before, the machine stood in PLAY with nothing playing, and stop() raised and left its threads running."""
    a = make_album()
    activity = library.activities.get_activity(TAG_ALBUM)

    def broken(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(activity, "update", broken)
    a.cmd_next_handler()
    a.wait_until_idle()
    assert plays_track(a, 1)
    a.cmd_pause()
    a.wait_until_idle()
    assert a.get_state() == States.PAUSED
    a.stop()
    assert a.get_state() == States.END
    assert len([r for r in caplog.records if "cannot be saved" in r.getMessage()]) == 1  # said once


def test_a_track_that_cannot_be_started_later_pauses_and_play_tries_again(make_album, clock, monkeypatch):
    """ffmpeg cannot be started any more (removed meanwhile, no process left): paused instead of standing in PLAY
    with nothing playing. At the start of the album the error is raised - STMOperation shows the album as broken
    (test_failures.py)."""
    from tinyjukebox.albumplayer.dummyplayer import DummyTrack

    reported = []
    monkeypatch.setattr("tinyjukebox.core.unexpected.report", lambda error, where: reported.append(where))
    original = DummyTrack._play
    failing = [False]

    def start(self, play_id):
        if failing[0]:
            raise OSError(11, "Resource temporarily unavailable")
        original(self, play_id)

    monkeypatch.setattr(DummyTrack, "_play", start)
    a = make_album()
    assert plays_track(a, 0)
    failing[0] = True
    a.cmd_next_handler()
    a.wait_until_idle()
    assert a.get_state() == States.PAUSED
    assert not a.player.is_playing()
    assert reported == ["album player"]
    failing[0] = False
    a.cmd_play()
    a.wait_until_idle()
    assert plays_track(a, 1)


def test_headset_returning_too_late_does_not_continue(make_album):
    a = make_album()
    a.cmd_output_lost()
    a.wait_until_idle()
    a.pause.since -= base.RESUME_WINDOW + 1
    a.cmd_output_changed()
    a.wait_until_idle()
    assert state(a) == "paused"
    assert a.pause.reason is PauseReason.OUTPUT  # still - play continues as after any pause
    a.cmd_play()
    a.wait_until_idle()
    assert state(a) == "play"


def test_user_pause_survives_loss_and_return_of_the_headset(make_album):
    a = make_album()
    a.cmd_pause()
    a.cmd_output_lost()  # paused before the headset went
    a.cmd_output_changed()
    a.wait_until_idle()
    assert state(a) == "paused"
    assert a.pause.reason is PauseReason.USER


def test_new_output_while_playing_continues_a_second_earlier(make_album, clock, pubsub):
    a = make_album(target="2")
    clock.advance(2.5)
    a.cmd_output_changed()  # the headset has connected
    a.wait_until_idle()
    assert plays_track(a, 0)
    assert track(a)["pos"] == pytest.approx(1.5)
    assert a.player.is_playing()
    a.cmd_output_changed()  # at the start of a track: not before it
    a.cmd_output_changed()
    a.wait_until_idle()
    assert plays_track(a, 0)
    assert track(a)["pos"] == 0
    assert pubsub.messages(PLAYBACK) == [PlaybackStatus.PLAYING]  # for the headset it never stopped


def test_lost_headset_between_two_tracks(make_album, clock):
    a = make_album(**{"pause-between-tracks": 30})
    clock.advance(track_length(a))
    a.wait_until_idle()
    assert state(a) == "delay"
    a.cmd_output_lost()
    a.cmd_output_changed()
    a.wait_until_idle()
    assert plays_track(a, 1)


def test_failing_headset_output_pauses_instead_of_skipping(make_album, tmp_path, logger, monkeypatch):
    """Real pipelines: the output process fails (the headset has gone) - the track is not skipped."""
    from tinyjukebox.albumplayer import simpleplayer

    folder = tmp_path / "short_library"
    folder.mkdir()
    lib = create_library(folder, logger)
    output = simpleplayer.AudioOutput()
    output.use_headset(lambda: "bluealsa:DEV=00:11:22:33:44:55,PROFILE=a2dp")
    monkeypatch.setattr(
        simpleplayer, "output_command", lambda null_output=False, buffer_time=None, device=None: ["false"]
    )
    a = make_album(player_factory=functools.partial(SimplePlayer, output=output), lib=lib)
    assert wait_for(lambda: state(a) == "paused")
    assert a.pause.reason is PauseReason.OUTPUT
    assert track(a)["track_id"] == 0
    # the same failure on the speaker: the box goes on (as before)
    output.use_speaker()
    a.cmd_play()
    assert wait_for(lambda: state(a) == "finished", timeout=8)
