"""Stress tests with real SimplePlayer processes: many events in quick succession must not lead to deadlocks,
double playback or dead machines."""

import logging
import random
import time

import psutil
import pytest

from tests.support import SHORT_MP3, TAG_ALBUM, TAG_UNKNOWN_ALBUM, TAG_UNMAPPED, deliver_rfid, wait_for
from tinyjukebox.albumplayer.simpleplayer import SimplePlayer
from tinyjukebox.messages import BUTTON_PRESSED, Button
from tinyjukebox.metadata import mp3_info
from tinyjukebox.stm.stmoperation import States, STMOperation
from tinyjukebox.stm.stmplayalbum import STMPlayAlbum

TRACK_LENGTH = mp3_info(SHORT_MP3)[0]  # album "1": three of them


def audio_processes():
    processes = []
    for process in psutil.Process().children(recursive=True):
        try:
            if process.status() != psutil.STATUS_ZOMBIE and "ffmpeg" in process.name():
                processes.append(process)
        except psutil.NoSuchProcess:
            pass
    return processes


def pipelines():
    """Number of running audio pipelines (decoder + null sink = two ffmpeg processes each)."""
    return len(audio_processes()) / 2


@pytest.fixture
def box(library, pubsub, rgbled, logger, caplog):
    """STMOperation with real STMPlayAlbum/SimplePlayer instances."""
    config = {"rewind-max-time": 5, "update-schedule": 0.05, "pause-between-tracks": 0}

    def factory(target, activity):
        return STMPlayAlbum(config, pubsub, logger, rgbled, target, activity, SimplePlayer)

    caplog.set_level(logging.INFO)
    stm = STMOperation(
        pubsub, logger, rgbled, library.tags, library.targets,
        library.activities, factory, poll_interval=0.05,
    )  # fmt: skip
    stm.start()
    yield stm
    if stm.get_active_state() != States.END:
        stm.stop()
    assert wait_for(lambda: pipelines() == 0), "audio processes left after stop"
    failures = [r.getMessage() for r in caplog.records if "_async_worker" in r.getMessage()]
    assert failures == [], f"a state raised an exception: {failures}"


def assert_never_double_playback(duration=0.3):
    """At no time more than one audio pipeline (during a track change there may briefly be none)."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        assert pipelines() <= 1
        time.sleep(0.02)


def album_state(stm):
    album = stm.get_album()
    return album.get_info()["state"] if album is not None else None


def test_rapid_tag_changes(box, pubsub):
    rnd = random.Random(4711)
    for _ in range(40):
        uid = rnd.choice([TAG_ALBUM, TAG_UNMAPPED, TAG_UNKNOWN_ALBUM, None])
        if uid is None:
            deliver_rfid(pubsub, "", present=False)
        else:
            deliver_rfid(pubsub, uid)
        time.sleep(rnd.uniform(0, 0.02))
    deliver_rfid(pubsub, "", present=False)
    deliver_rfid(pubsub, TAG_ALBUM)
    assert wait_for(lambda: box.get_active_state() == States.PLAY_ALBUM and album_state(box) == "play")
    assert wait_for(lambda: pipelines() == 1)
    assert_never_double_playback()


def test_button_mashing_during_playback(box, pubsub):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert wait_for(lambda: album_state(box) == "play")
    rnd = random.Random(42)
    for _ in range(30):
        pubsub.deliver(BUTTON_PRESSED, rnd.choice([Button.NEXT, Button.PREV]))
        time.sleep(rnd.uniform(0, 0.01))
    assert wait_for(lambda: album_state(box) in ("play", "finished"))
    assert wait_for(lambda: pipelines() <= 1)
    assert_never_double_playback()
    # the box still reacts
    deliver_rfid(pubsub, TAG_ALBUM, present=False)
    assert wait_for(lambda: box.get_active_state() == States.WAIT_FOR_TAG)
    assert wait_for(lambda: pipelines() == 0)


@pytest.mark.parametrize("delay", [0.05, 0.1, 0.15, 0.2])
def test_tag_removed_around_end_of_track(box, pubsub, library, delay):
    # start 0.1s before the end of the first track and remove the tag around the track change
    library.activities.get_activity(TAG_ALBUM).update(0, TRACK_LENGTH - 0.1, False)
    deliver_rfid(pubsub, TAG_ALBUM)
    assert wait_for(lambda: album_state(box) == "play")
    time.sleep(delay)
    deliver_rfid(pubsub, TAG_ALBUM, present=False)
    assert wait_for(lambda: box.get_active_state() == States.WAIT_FOR_TAG)
    assert wait_for(lambda: pipelines() == 0)
    activity = library.activities.get_activity(TAG_ALBUM)
    assert activity.track_id in (0, 1)
    assert 0 <= activity.track_time <= TRACK_LENGTH + 0.01
    assert activity.finished is False


def test_tag_removed_around_end_of_album(box, pubsub, library):
    library.activities.get_activity(TAG_ALBUM).update(2, TRACK_LENGTH - 0.13, False)
    for _ in range(3):
        deliver_rfid(pubsub, TAG_ALBUM)
        time.sleep(0.12)
        deliver_rfid(pubsub, TAG_ALBUM, present=False)
        assert wait_for(lambda: box.get_active_state() == States.WAIT_FOR_TAG)
    assert wait_for(lambda: pipelines() == 0)
    deliver_rfid(pubsub, TAG_ALBUM)
    assert wait_for(lambda: album_state(box) == "play")


def test_stop_during_track_changes(box, pubsub):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert wait_for(lambda: album_state(box) == "play")
    for _ in range(5):
        pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
    box.stop()
    assert box.get_active_state() == States.END
