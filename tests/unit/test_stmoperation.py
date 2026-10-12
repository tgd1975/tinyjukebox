"""STMOperation - the state machine that reacts on rfid tags."""

import json
import threading
import time

import pytest

from tests.support import TAG_ALBUM, TAG_UNKNOWN_ALBUM, TAG_UNMAPPED, deliver_rfid, wait_for
from tinyjukebox.messages import RFID_POLL, RFID_STATE
from tinyjukebox.rgbled import Color
from tinyjukebox.stm.stmoperation import States, STMOperation

POLL_INTERVAL = 0.05


class FakeAlbum:
    """Stands in for STMPlayAlbum - records the calls of STMOperation."""

    def __init__(self, target, activity):
        self.target = target
        self.activity = activity
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def cmd_next_handler(self):
        pass

    def cmd_prev_handler(self):
        pass

    def cmd_restart_album(self):
        pass

    def get_info(self):
        return {}


class RecordingSTMOperation(STMOperation):
    """STMOperation that keeps the albums created by the default factory of :func:`create_stm`."""

    albums: list[FakeAlbum]


def create_stm(library, pubsub, rgbled, logger, factory=None, poll_interval=POLL_INTERVAL, **kwargs):
    albums: list[FakeAlbum] = []

    def default_factory(target, activity):
        album = FakeAlbum(target, activity)
        albums.append(album)
        return album

    stm = RecordingSTMOperation(pubsub, logger, rgbled, library.tags,
                                library.targets, library.activities, factory or default_factory,
                                poll_interval=poll_interval, **kwargs)  # fmt: skip
    stm.albums = albums
    return stm


@pytest.fixture
def stm(library, pubsub, rgbled, logger):
    stm = create_stm(library, pubsub, rgbled, logger, poll_interval=60)  # forced polling not part of these tests
    stm.start()
    yield stm
    if stm.get_active_state() != States.END:
        stm.stop()


def settle(stm):
    stm.wait_until_idle()
    return stm.get_active_state()


def test_start(stm, pubsub, rgbled):
    assert stm.get_active_state() == States.WAIT_FOR_TAG
    assert rgbled.last() == ("color", Color.GREEN)
    assert pubsub.messages(RFID_POLL) == [None]  # ask the rfid driver for the current state
    assert stm.get_album() is None


def test_led_blinks_after_a_long_time_without_tag(library, pubsub, rgbled, logger):
    """As on the old box (there after 10 minutes): green blinking - somebody forgot to switch the box off."""
    stm = create_stm(library, pubsub, rgbled, logger, poll_interval=60, idle_blink_after=0.1)
    stm.start()
    try:
        assert rgbled.last() == ("color", Color.GREEN)
        assert wait_for(lambda: stm.get_active_state() == States.IDLE_LONG)
        assert rgbled.last() == ("blink", Color.GREEN)
        deliver_rfid(pubsub, TAG_ALBUM, present=False)  # no tag: nothing changes
        assert settle(stm) == States.IDLE_LONG
        deliver_rfid(pubsub, TAG_ALBUM)  # a tag is handled as usual
        assert settle(stm) == States.PLAY_ALBUM
        time.sleep(0.2)
        assert stm.get_active_state() == States.PLAY_ALBUM  # the time runs only while no tag is on the box
        deliver_rfid(pubsub, TAG_ALBUM, present=False)
        assert settle(stm) == States.WAIT_FOR_TAG
        assert rgbled.last() == ("color", Color.GREEN)
        assert wait_for(lambda: stm.get_active_state() == States.IDLE_LONG)  # and starts again
    finally:
        stm.stop()
    assert stm.get_active_state() == States.END


def test_forced_polling_stops_with_first_rfid_message(library, pubsub, rgbled, logger):
    stm = create_stm(library, pubsub, rgbled, logger)
    stm.start()
    try:
        assert wait_for(lambda: len(pubsub.messages(RFID_POLL)) >= 4)  # initial request + polling
        deliver_rfid(pubsub, "", present=False)
        polls = len(pubsub.messages(RFID_POLL))
        time.sleep(POLL_INTERVAL * 4)
        # no more polls - except for one that might have been in progress when the message arrived
        assert len(pubsub.messages(RFID_POLL)) <= polls + 1
        assert set(pubsub.messages(RFID_POLL)) == {None}
    finally:
        stm.stop()


def test_new_tag_is_registered_as_unmapped(stm, pubsub, library, rgbled):
    deliver_rfid(pubsub, "9:9:9:9")
    assert settle(stm) == States.UNMAPPED_TAG
    assert "9:9:9:9" in library.tags.tags
    assert library.tags.get_tag("9:9:9:9").target_id == -1
    with open(library.tags_file, encoding="utf-8") as f:
        assert "9:9:9:9" in json.load(f)
    assert rgbled.last() == ("color", Color.YELLOW)


def test_unmapped_tag(stm, pubsub, rgbled):
    deliver_rfid(pubsub, TAG_UNMAPPED)
    assert settle(stm) == States.UNMAPPED_TAG
    assert stm.get_current_uid() == TAG_UNMAPPED
    assert rgbled.last() == ("color", Color.YELLOW)


def test_tag_with_unknown_album(stm, pubsub, rgbled):
    deliver_rfid(pubsub, TAG_UNKNOWN_ALBUM)
    assert settle(stm) == States.UNKNOWN_ALBUM
    assert rgbled.last() == ("alternate", Color.YELLOW, Color.RED)


def test_mapped_tag_starts_album(stm, pubsub, library):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    assert len(stm.albums) == 1
    album = stm.albums[0]
    assert album.started
    assert album.target is library.targets.get_target("1")
    assert album.activity is library.activities.get_activity(TAG_ALBUM)
    assert stm.get_album() is album


def test_activity_is_created_for_new_link(stm, pubsub, library):
    library.activities.delete_activity(TAG_ALBUM)
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    activity = library.activities.get_activity(TAG_ALBUM)
    assert (activity.target_id, activity.track_id, activity.track_time, activity.finished) == ("1", 0, 0, False)


def test_activity_is_reset_after_relinking(stm, pubsub, library):
    library.activities.get_activity(TAG_ALBUM).update(2, 1.5, False)
    library.tags.assign_target(TAG_ALBUM, library.tags.get_tag(TAG_ALBUM).target_type, "2")
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    activity = stm.albums[0].activity
    assert (activity.target_id, activity.track_id, activity.track_time) == ("2", 0, 0)


def test_removing_tag_stops_album(stm, pubsub, rgbled):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    deliver_rfid(pubsub, TAG_ALBUM, present=False)
    assert settle(stm) == States.WAIT_FOR_TAG
    assert stm.albums[0].stopped
    assert stm.get_current_uid() is None
    assert stm.get_album() is None
    assert rgbled.last() == ("color", Color.GREEN)


def test_same_tag_is_ignored(stm, pubsub):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    assert len(stm.albums) == 1
    assert not stm.albums[0].stopped


def test_swapping_tags_without_no_tag_message(stm, pubsub):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    deliver_rfid(pubsub, TAG_UNMAPPED)
    assert settle(stm) == States.UNMAPPED_TAG
    assert stm.albums[0].stopped
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    assert len(stm.albums) == 2


def test_no_tag_while_waiting_is_ignored(stm, pubsub):
    deliver_rfid(pubsub, "", present=False)
    assert settle(stm) == States.WAIT_FOR_TAG


def test_invalid_rfid_message_is_ignored(stm, pubsub):
    pubsub.deliver(RFID_STATE, "no rfid state")
    pubsub.deliver(RFID_STATE, {"uid": "1:2:3:4", "present": "True"})
    assert settle(stm) == States.WAIT_FOR_TAG
    deliver_rfid(pubsub, TAG_ALBUM)  # machine still works
    assert settle(stm) == States.PLAY_ALBUM


def test_stop(stm, pubsub, rgbled):
    deliver_rfid(pubsub, TAG_ALBUM)
    assert settle(stm) == States.PLAY_ALBUM
    stm.stop()
    assert stm.get_active_state() == States.END
    assert stm.albums[0].stopped
    assert pubsub.handlers(RFID_STATE) == []
    assert rgbled.last() == ("color", Color.MAGENTA)


def test_stop_before_start_does_not_raise(library, pubsub, rgbled, logger):
    stm = create_stm(library, pubsub, rgbled, logger)
    stm.stop()
    assert pubsub.handlers(RFID_STATE) == []
    assert pubsub.messages(RFID_POLL) == []


def test_rfid_handler_only_queues(library, pubsub, rgbled, logger):
    """The rfid handler runs in the pubsub dispatch thread - it must return while the album is still starting."""
    release = threading.Event()
    entered = threading.Event()
    albums = []

    def blocking_factory(target, activity):
        entered.set()
        release.wait(5)
        album = FakeAlbum(target, activity)
        albums.append(album)
        return album

    stm = create_stm(library, pubsub, rgbled, logger, factory=blocking_factory, poll_interval=60)
    stm.start()
    try:
        deliver_rfid(pubsub, TAG_ALBUM)  # returns although the album start blocks
        assert entered.wait(5)
        deliver_rfid(pubsub, TAG_ALBUM, present=False)  # still not blocking - queued behind the album start
        assert stm.get_current_uid() is None
        release.set()
        assert settle(stm) == States.WAIT_FOR_TAG
        assert albums[0].stopped
    finally:
        release.set()
        stm.stop()


def test_recheck_tag_starts_album_after_linking(stm, pubsub, library):
    deliver_rfid(pubsub, TAG_UNMAPPED)
    assert settle(stm) == States.UNMAPPED_TAG
    library.tags.assign_target(TAG_UNMAPPED, library.tags.get_tag(TAG_UNMAPPED).target_type, "2")
    stm.recheck_tag()
    assert settle(stm) == States.PLAY_ALBUM
    assert stm.albums[0].target is library.targets.get_target("2")


def test_broken_album(library, pubsub, rgbled, logger):
    def broken_factory(target, activity):
        raise RuntimeError("broken")

    stm = create_stm(library, pubsub, rgbled, logger, factory=broken_factory, poll_interval=60)
    stm.start()
    try:
        deliver_rfid(pubsub, TAG_ALBUM)
        assert settle(stm) == States.BROKEN_ALBUM
        assert stm.get_album() is None
        assert rgbled.last() == ("alternate", Color.RED, Color.BLACK)
    finally:
        stm.stop()
