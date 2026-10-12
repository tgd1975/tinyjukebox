"""Failure scenarios on the box: broken albums, outdated activities, missing tools."""

import functools
import os
import shutil

import pytest

from tests.support import TAG_ALBUM, TAG_UNMAPPED, deliver_rfid
from tinyjukebox.albumplayer import simpleplayer
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.albumplayer.simpleplayer import SimplePlayer
from tinyjukebox.rgbled import Color
from tinyjukebox.stm.stmoperation import States, STMOperation
from tinyjukebox.stm.stmplayalbum import STMPlayAlbum

ALBUMPLAYER = {"rewind-max-time": 5, "update-schedule": 0.2, "pause-between-tracks": 0}


@pytest.fixture
def make_stm(library, pubsub, rgbled, logger):
    """STMOperation with real STMPlayAlbum instances - ``player_factory``: DummyPlayer or SimplePlayer."""
    machines = []

    def make(player_factory):
        def factory(target, activity):
            return STMPlayAlbum(ALBUMPLAYER, pubsub, logger, rgbled, target, activity,
                                player_factory)  # fmt: skip

        stm = STMOperation(pubsub, logger, rgbled, library.tags,
                           library.targets, library.activities, factory, poll_interval=60)  # fmt: skip
        stm.start()
        machines.append(stm)
        return stm

    yield make
    for stm in machines:
        if stm.get_active_state() != States.END:
            stm.stop()


@pytest.fixture
def stm(make_stm, clock):
    """Broken albums are detected when the player is created - no audio needed."""
    return make_stm(functools.partial(DummyPlayer, clock=clock))


@pytest.fixture
def real_stm(make_stm):
    """With ffmpeg/aplay - for the tests of missing tools."""
    return make_stm(SimplePlayer)


def rfid(stm, pubsub, uid, present=True):
    deliver_rfid(pubsub, uid, present)
    stm.wait_until_idle()
    return stm.get_active_state()


def assert_box_still_works(stm, pubsub):
    """After a failure the box must still react on tags."""
    assert rfid(stm, pubsub, TAG_UNMAPPED) == States.UNMAPPED_TAG
    assert rfid(stm, pubsub, TAG_UNMAPPED, present=False) == States.WAIT_FOR_TAG


def test_deleted_album_folder(stm, pubsub, library, rgbled):
    shutil.rmtree(os.path.join(library.albums_folder, "short"))
    assert rfid(stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    assert rgbled.last() == ("alternate", Color.RED, Color.BLACK)
    assert stm.get_album() is None
    assert rfid(stm, pubsub, TAG_ALBUM, present=False) == States.WAIT_FOR_TAG
    assert_box_still_works(stm, pubsub)


def test_missing_track_in_album(stm, pubsub, library):
    os.remove(os.path.join(library.albums_folder, "short", "2.mp3"))
    assert rfid(stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    rfid(stm, pubsub, TAG_ALBUM, present=False)
    assert_box_still_works(stm, pubsub)


def test_invalid_mp3_in_album(stm, pubsub, library):
    with open(os.path.join(library.albums_folder, "short", "2.mp3"), "wb") as f:
        f.write(b"this is no mp3")
    assert rfid(stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    rfid(stm, pubsub, TAG_ALBUM, present=False)
    assert_box_still_works(stm, pubsub)


def test_empty_playlist(stm, pubsub, library):
    open(os.path.join(library.albums_folder, "short", "playlist.m3u"), "w").close()
    assert rfid(stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    rfid(stm, pubsub, TAG_ALBUM, present=False)
    assert_box_still_works(stm, pubsub)


def test_broken_album_to_other_tag(stm, pubsub, library):
    shutil.rmtree(os.path.join(library.albums_folder, "short"))
    assert rfid(stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    assert rfid(stm, pubsub, TAG_UNMAPPED) == States.UNMAPPED_TAG  # swapped without "no tag"


@pytest.fixture
def start_album(library, pubsub, rgbled, logger, clock):
    albums = []

    def start(activity_values):
        activity = library.activities.get_activity(TAG_ALBUM)
        activity.update(*activity_values)
        album = STMPlayAlbum(ALBUMPLAYER, pubsub, logger, rgbled,
                             library.targets.get_target("1"), activity,
                             functools.partial(DummyPlayer, clock=clock))  # fmt: skip
        album.start()
        albums.append(album)
        return album

    yield start
    for album in albums:
        album.stop()


@pytest.mark.parametrize("track_id", [3, 10])
def test_saved_track_beyond_playlist_restarts_album(start_album, clock, track_id):
    # e.g. the playlist has been shortened after the position had been stored
    album = start_album((track_id, 0.5, False))
    album.wait_until_idle()
    assert album.get_info()["state"] == "play"
    track = album.get_info()["track"]
    assert track["track_id"] == 0
    assert track["pos"] < 0.3


def test_saved_position_beyond_track_end_continues_with_next_track(start_album, clock):
    album = start_album((1, 999, False))
    clock.advance(0)  # the track has no time left - it ends right away
    album.wait_until_idle()
    info = album.get_info()
    assert info["track"]["track_id"] == 2
    assert info["state"] == "play"


def test_saved_position_in_last_track_beyond_end_finishes_album(start_album, clock, library):
    album = start_album((2, 999, False))
    clock.advance(0)
    album.wait_until_idle()
    assert album.get_info()["state"] == "finished"
    assert library.activities.get_activity(TAG_ALBUM).finished


def test_missing_ffmpeg(real_stm, pubsub, monkeypatch):
    monkeypatch.setattr(simpleplayer, "decoder_command", lambda source, start_time=0: ["ffmpeg-does-not-exist"])
    assert rfid(real_stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    rfid(real_stm, pubsub, TAG_ALBUM, present=False)
    assert_box_still_works(real_stm, pubsub)


def test_missing_aplay(real_stm, pubsub, monkeypatch):
    monkeypatch.setattr(
        simpleplayer,
        "output_command",
        lambda null_output=False, buffer_time=None, device=None: ["aplay-does-not-exist"],
    )
    assert rfid(real_stm, pubsub, TAG_ALBUM) == States.BROKEN_ALBUM
    rfid(real_stm, pubsub, TAG_ALBUM, present=False)
    assert_box_still_works(real_stm, pubsub)
