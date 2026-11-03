"""STMOperation - the state machine of the box: reacts on rfid tags and starts/stops the album playback."""

import logging
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.common import is_unmapped
from tinyjukebox.data.tag import Tag
from tinyjukebox.data.tags import Tags
from tinyjukebox.data.target import TargetAlbum, TargetRadio, TargetSpotify
from tinyjukebox.data.targets import Targets
from tinyjukebox.latency import ReactionTimer
from tinyjukebox.messages import BUTTON_PRESSED, JOURNAL, RFID_POLL, RFID_STATE, Button, JournalEvent, RfidState
from tinyjukebox.rgbled import WAITING, Color, RGBLed
from tinyjukebox.stm.base import State, create_machine, write_dot_file

if TYPE_CHECKING:
    from tinyjukebox.sync.box import BoxSync

__all__ = [
    "IDLE_BLINK_AFTER",
    "RADIO_TEST_DURATION",
    "AlbumControl",
    "AlbumFactory",
    "RadioFactory",
    "STMOperation",
    "Speaker",
    "SpotifyFactory",
    "States",
    "Transitions",
]

FORCED_POLLING_INTERVAL = 5.0
"""Seconds between the requests for the current rfid state until the first rfid message has been received."""
RADIO_TEST_DURATION = 60.0
"""Seconds after which a radio test (:meth:`STMOperation.test_radio`) ends by itself."""
IDLE_BLINK_AFTER = 600.0
"""Seconds without tag after which the green led blinks - the box has been left switched on (as the old box)."""


class States(Enum):
    """States of the STMOperation state machine."""

    WAIT_FOR_TAG = "wait_for_tag"  # waiting for an rfid tag
    IDLE_LONG = "idle_long"  # waiting for an rfid tag for a long time - the led blinks
    CHECK_TAG = "check_tag"  # checking the detected tag
    PLAY_ALBUM = "play_album"  # playing the album or radio station linked to the tag
    UNMAPPED_TAG = "unknown_tag"  # tag is not linked to an album or radio station
    UNKNOWN_ALBUM = "unknown_album"  # tag is linked to an album/radio station that does not exist
    BROKEN_ALBUM = "broken_album"  # album cannot be played (e.g. missing folder, missing or invalid mp3 files)
    TEST_RADIO = "test_radio"  # testing a radio stream or an entry of Spotify while no tag is on the box
    SPOTIFY_SPEAKER = "spotify_speaker"  # no tag: the Spotify app plays on the box
    FETCH_ALBUM = "fetch_album"  # the album is fetched from the library - it plays as soon as its track is there
    BLOCKED = "blocked"  # the box plays nothing: the library is too old for it
    END = "end"  # final state


class Transitions(Enum):
    """Transitions of the STMOperation state machine."""

    NO_TAG = "no_tag"  # no rfid tag present
    IDLE_TIMEOUT = "idle_timeout"  # no rfid tag for a long time
    NEW_TAG = "new_tag"  # new rfid tag detected
    TAG_UNMAPPED = "unknown_tag"  # tag is not linked to an album
    ALBUM_UNKNOWN = "unknown_album"  # linked album does not exist
    ALBUM_KNOWN = "play_album"  # linked album exists
    ALBUM_BROKEN = "broken_album"  # album could not be started
    TEST_RADIO = "test_radio"  # start (or switch) a radio test
    TEST_END = "test_end"  # radio test stopped or timed out
    ALBUM_FETCH = "fetch_album"  # files of the linked album are missing on the box
    ALBUM_READY = "album_ready"  # the track that is due has arrived
    FETCH_FAILED = "fetch_failed"  # the album cannot be fetched
    BLOCKED = "blocked"  # the box does not work
    SPEAKER_PLAYS = "speaker_plays"  # the Spotify app plays on the box
    SPEAKER_ENDS = "speaker_ends"  # ... it does not any more
    END = "end"  # shutdown


class AlbumControl(Protocol):
    """An album playback (:class:`~tinyjukebox.stm.stmplayalbum.STMPlayAlbum`) as used by STMOperation and the
    box control."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def cmd_next_handler(self) -> None: ...

    def cmd_prev_handler(self) -> None: ...

    def cmd_restart_album(self) -> None: ...

    def cmd_pause(self) -> None: ...

    def cmd_play(self) -> None: ...

    def cmd_output_lost(self, then: Callable[[], None] | None = None) -> None:
        """The headset has gone: pause. ``then`` is called once the playback has handled it - in its worker, after
        everything that was queued before; the headset switches the output to the speaker only then."""
        ...

    def cmd_output_changed(self) -> None: ...

    def get_info(self) -> dict[str, Any]: ...


AlbumFactory = Callable[[TargetAlbum, ActivityAlbum], AlbumControl]
RadioFactory = Callable[[TargetRadio], AlbumControl]
SpotifyFactory = Callable[[TargetSpotify, ActivityAlbum | None], AlbumControl]
"""(entry, activity) -> playback; activity None: a test"""


class Speaker(Protocol):
    """The box as a Spotify speaker while no tag is on it (:class:`~tinyjukebox.spotify.engine.SpotifyEngine`)."""

    def idle(self) -> None: ...

    def taken(self, spotify: bool) -> None: ...

    def listen(self) -> None: ...

    def silence(self) -> None: ...

    def button(self, button: Button) -> None: ...

    def playback(self) -> AlbumControl: ...

    on_speaker_plays: Callable[[], None]
    on_speaker_ends: Callable[[], None]
    on_speaker_paused: Callable[[bool], None]


UidSource = Callable[[], str | None]


class _LedState(State):
    """State that only sets the led on entry."""

    def __init__(self, state_id: States, logger: logging.Logger, rgbled: RGBLed, color: Color) -> None:
        super().__init__(state_id, logger)
        self._rgbled = rgbled
        self._color = color

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_color(self._color)
        return None


class End(_LedState):
    def __init__(self, logger: logging.Logger, rgbled: RGBLed) -> None:
        super().__init__(States.END, logger, rgbled, Color.MAGENTA)


class WaitForTag(_LedState):
    # the current uid is maintained by _rfid_handler only - resetting it here would drop a tag that has been placed
    # while the "no tag" event was still queued
    def __init__(self, logger: logging.Logger, rgbled: RGBLed, speaker: Speaker | None = None) -> None:
        super().__init__(States.WAIT_FOR_TAG, logger, rgbled, Color.GREEN)
        self._speaker = speaker

    def _on_entry(self) -> Enum | None:
        super()._on_entry()
        if self._speaker is not None:
            self._speaker.idle()
        return None


class IdleLong(State):
    def __init__(self, logger: logging.Logger, rgbled: RGBLed, speaker: Speaker | None = None) -> None:
        super().__init__(States.IDLE_LONG, logger)
        self._rgbled = rgbled
        self._speaker = speaker

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_blink_color(Color.GREEN, 1)
        if self._speaker is not None:
            self._speaker.idle()
        return None


class SpotifySpeaker(State):
    """No tag on the box, and the Spotify app plays on it: the sound is heard, the buttons steer what the phone
    started. A tag or a test ends it (docs/developer/architecture.md, section 2.5)."""

    def __init__(self, logger: logging.Logger, rgbled: RGBLed, speaker: Speaker, pubsub_client: PubSubClient) -> None:
        super().__init__(States.SPOTIFY_SPEAKER, logger)
        self._rgbled = rgbled
        self._speaker = speaker
        self._pubsub_client = pubsub_client

        self._playback: AlbumControl | None = None

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_color(Color.BLUE)
        self._playback = self._speaker.playback()
        self._playback.start()
        self._pubsub_client.subscribe(BUTTON_PRESSED, self._button)
        return None

    def _on_exit(self) -> None:
        self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._button, ignore_not_found=True)
        playback, self._playback = self._playback, None
        if playback is not None:
            playback.stop()

    def get_album(self) -> AlbumControl | None:
        return self._playback

    def _button(self, message: object) -> None:
        if isinstance(message, Button):
            self._speaker.button(message)


class CheckTag(State):
    def __init__(
        self,
        logger: logging.Logger,
        tags: Tags,
        targets: Targets,
        get_current_uid: UidSource,
        sync: "BoxSync | None" = None,
        start_track: Callable[[str], int] = lambda uid: 0,
        speaker: Speaker | None = None,
    ) -> None:
        super().__init__(States.CHECK_TAG, logger)
        self._speaker = speaker
        self._tags = tags
        self._targets = targets
        self._get_current_uid = get_current_uid
        self._sync = sync
        self._start_track = start_track

    def _on_entry(self) -> Enum | None:
        uid = self._get_current_uid()
        if uid is None:
            return Transitions.NO_TAG
        if self._sync is not None and self._sync.blocked:
            return Transitions.BLOCKED
        if uid not in self._tags.tags and self._sync is not None:
            # an unknown tag: compare the catalog with the library before it is taken for a new one
            self._sync.lookup()
        if uid not in self._tags.tags and uid not in self._tags.unavailable:
            self._logger.info("new tag '%s' - adding it to tags as unmapped tag.", uid)
            self._tags.add_tag(uid)
            return Transitions.TAG_UNMAPPED
        # no tag: known, but this version cannot use what it is linked to (docs/developer/library.md, section 4.21)
        tag = self._tags.tags.get(uid)
        target = None if tag is None or is_unmapped(tag.target_id) else self._targets.targets.get(str(tag.target_id))
        if self._speaker is not None:
            self._speaker.taken(isinstance(target, TargetSpotify))
        if tag is not None and is_unmapped(tag.target_id):
            return Transitions.TAG_UNMAPPED
        if tag is None or target is None or target.target_type != tag.target_type:
            return Transitions.ALBUM_UNKNOWN
        return Transitions.ALBUM_KNOWN if self._is_here(uid, target) else Transitions.ALBUM_FETCH

    def _is_here(self, uid: str, target: object) -> bool:
        """Whether the target can play at once: for an album of the library the track that is due must be on the
        box."""
        if not isinstance(target, TargetAlbum) or self._sync is None or not self._sync.attached:
            return True
        ready = self._sync.files.ready(str(target.target_id), self._start_track(uid))
        target.incomplete = bool(self._sync.files.lacking(str(target.target_id)) or not ready)
        return ready


class FetchAlbum(State):
    """Files of the album are missing on the box: they are fetched from the library, beginning with the track that
    is due. LED: green, aqua, blue flowing back and forth."""

    def __init__(self, logger: logging.Logger, rgbled: RGBLed, fetch: Callable[[], Enum | None]) -> None:
        super().__init__(States.FETCH_ALBUM, logger)
        self._rgbled = rgbled
        self._fetch = fetch

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_sequence(WAITING)
        return self._fetch()


class Blocked(State):
    """The library is too old for this box (docs/developer/library.md, section 4.15): it plays nothing.
    The led shows the alarm pattern as an overlay, with or without a tag."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(States.BLOCKED, logger)

    def _on_entry(self) -> Enum | None:
        self._logger.error("this box does not work: the library is too old for it")
        return None


class UnmappedTag(State):
    def __init__(self, logger: logging.Logger, rgbled: RGBLed, get_current_uid: UidSource) -> None:
        super().__init__(States.UNMAPPED_TAG, logger)
        self._rgbled = rgbled
        self._get_current_uid = get_current_uid

    def _on_entry(self) -> Enum | None:
        self._logger.warning("unmapped tag for uid '%s'", self._get_current_uid())
        self._rgbled.set_color(Color.YELLOW)
        return None


class UnknownAlbum(State):
    def __init__(self, logger: logging.Logger, rgbled: RGBLed, get_current_uid: UidSource) -> None:
        super().__init__(States.UNKNOWN_ALBUM, logger)
        self._rgbled = rgbled
        self._get_current_uid = get_current_uid

    def _on_entry(self) -> Enum | None:
        self._logger.warning("unknown album mapped to uid '%s'", self._get_current_uid())
        self._rgbled.set_alternate_colors(Color.YELLOW, Color.RED, 1)
        return None


class BrokenAlbum(State):
    def __init__(self, logger: logging.Logger, rgbled: RGBLed, get_current_uid: UidSource) -> None:
        super().__init__(States.BROKEN_ALBUM, logger)
        self._rgbled = rgbled
        self._get_current_uid = get_current_uid

    def _on_entry(self) -> Enum | None:
        self._logger.warning("album mapped to uid '%s' cannot be played", self._get_current_uid())
        self._rgbled.set_alternate_colors(Color.RED, Color.BLACK, 1)
        return None


class PlayAlbum(State):
    def __init__(
        self,
        logger: logging.Logger,
        rgbled: RGBLed,
        tags: Tags,
        targets: Targets,
        activities: Activities,
        get_current_uid: UidSource,
        album_factory: AlbumFactory,
        radio_factory: RadioFactory | None,
        spotify_factory: SpotifyFactory | None = None,
    ) -> None:
        super().__init__(States.PLAY_ALBUM, logger)
        self._rgbled = rgbled
        self._tags = tags
        self._targets = targets
        self._activities = activities
        self._get_current_uid = get_current_uid
        self._album_factory = album_factory
        self._radio_factory = radio_factory
        self._spotify_factory = spotify_factory
        self._album: AlbumControl | None = None

    def _get_activity(self, tag: Tag) -> ActivityAlbum:
        """The activity of the tag - (re)created if it is missing or belongs to a previously linked target."""
        activity = self._activities.activities.get(str(tag.tag_uid))
        if activity is not None and str(activity.target_id) == str(tag.target_id):
            return activity
        if activity is not None:
            self._logger.info(
                "activity for tag '%s' belongs to old target '%s' - resetting it.", tag.tag_uid, activity.target_id
            )
            self._activities.delete_activity(tag.tag_uid)
        else:
            self._logger.info("no activity for tag '%s' - creating it.", tag.tag_uid)
        return self._activities.add_activity(tag)

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_color(Color.AQUA)
        uid = self._get_current_uid()
        if uid is None:
            return Transitions.NO_TAG
        tag = self._tags.get_tag(uid)
        target = self._targets.get_target(str(tag.target_id))
        try:
            if isinstance(target, TargetRadio):
                if self._radio_factory is None:
                    raise RuntimeError("no radio player")
                album = self._radio_factory(target)
            elif isinstance(target, TargetSpotify):
                if self._spotify_factory is None:
                    raise RuntimeError("no player for Spotify")
                album = self._spotify_factory(target, self._get_activity(tag))
            else:
                album = self._album_factory(target, self._get_activity(tag))
            album.start()
        except Exception as e:
            self._logger.error("cannot play '%s': %s", target.to_text(), e)
            return Transitions.ALBUM_BROKEN
        self._album = album
        return None

    def _on_exit(self) -> None:
        album, self._album = self._album, None
        if album is not None:
            album.stop()

    def get_album(self) -> AlbumControl | None:
        return self._album


class TestRadio(State):
    """Plays a radio stream for a test - only while no tag is on the box: a tag always wins (the test ends when a
    tag is placed, and a test that was queued behind a tag does not start)."""

    def __init__(
        self,
        logger: logging.Logger,
        get_current_uid: UidSource,
        get_station: Callable[[], TargetRadio | TargetSpotify | None],
        radio_factory: RadioFactory | None,
        spotify_factory: SpotifyFactory | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        super().__init__(States.TEST_RADIO, logger)
        self._get_current_uid = get_current_uid
        self._get_station = get_station
        self._radio_factory = radio_factory
        self._spotify_factory = spotify_factory
        self._speaker = speaker
        self._playback: AlbumControl | None = None

    def _on_entry(self) -> Enum | None:
        station = self._get_station()
        spotify = isinstance(station, TargetSpotify)
        factory = self._spotify_factory if spotify else self._radio_factory
        if self._get_current_uid() is not None or station is None or factory is None:
            self._logger.info("test not started - a tag is on the box or there is nothing to test")
            return Transitions.TEST_END
        if self._speaker is not None:
            self._speaker.taken(spotify)
        self._logger.info("testing %s", station.to_text())
        if isinstance(station, TargetSpotify):
            assert self._spotify_factory is not None  # noqa: S101 - checked above
            playback = self._spotify_factory(station, None)
        else:
            assert self._radio_factory is not None  # noqa: S101 - checked above
            playback = self._radio_factory(station)
        try:
            playback.start()
        except Exception as e:
            self._logger.error("radio test failed: %s", e)
            return Transitions.TEST_END
        self._playback = playback
        return None

    def _on_exit(self) -> None:
        playback, self._playback = self._playback, None
        if playback is not None:
            playback.stop()

    def get_album(self) -> AlbumControl | None:
        return self._playback


class STMOperation:
    """Reacts on the :class:`~tinyjukebox.messages.RfidState` messages of the rfid reader
    (:data:`~tinyjukebox.messages.RFID_STATE`).

    The rfid handler only records the current uid and queues an event - it never blocks the pubsub dispatch thread.
    Until the first rfid message has been received, the current rfid state is requested every
    ``poll_interval`` seconds (:data:`~tinyjukebox.messages.RFID_POLL`).
    """

    def __init__(
        self,
        pubsub_client: PubSubClient,
        logger: logging.Logger,
        rgbled: RGBLed,
        tags: Tags,
        targets: Targets,
        activities: Activities,
        album_factory: AlbumFactory,
        poll_interval: float = FORCED_POLLING_INTERVAL,
        latency: ReactionTimer | None = None,
        radio_factory: RadioFactory | None = None,
        radio_test_duration: float = RADIO_TEST_DURATION,
        idle_blink_after: float = IDLE_BLINK_AFTER,
        sync: "BoxSync | None" = None,
        spotify_factory: SpotifyFactory | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        """:param latency: measures tag read → playback started (default: a timer of its own).
        :param radio_factory: playback of radio stations (``None``: tags linked to a station end in BROKEN_ALBUM).
        :param radio_test_duration: seconds after which a radio test ends by itself.
        :param idle_blink_after: seconds without tag after which the green led blinks.
        :param sync: the synchronization with the library: unknown tags are looked up there, missing albums are
            fetched.
        :param spotify_factory: playback of entries of Spotify (``None``: such tags end in BROKEN_ALBUM).
        :param speaker: the box as a Spotify speaker while no tag is on it."""
        self._logger = get_child(logger, type(self).__name__)
        self._sync = sync
        self._tags = tags
        self._activities = activities
        self._fetching: tuple[str, int] | None = None  # album and track that FETCH_ALBUM waits for
        self._pubsub_client = pubsub_client
        self._latency = latency or ReactionTimer("tag", self._logger)
        self._poll_interval = poll_interval
        self._current_uid: str | None = None
        self._rfid_handler_lock = threading.Lock()
        self._rfid_received = threading.Event()
        self._polling_thread: threading.Thread | None = None
        self._radio_factory = radio_factory
        self._test_station: TargetRadio | TargetSpotify | None = None
        self._spotify_factory = spotify_factory
        self._speaker = speaker
        self._rgbled = rgbled
        self._radio_test_duration = radio_test_duration
        self._idle_blink_after = idle_blink_after

        self._machine = create_machine(self._logger)
        self._play_album_state = PlayAlbum(
            self._logger,
            rgbled,
            tags,
            targets,
            activities,
            self.get_current_uid,
            album_factory,
            radio_factory,
            spotify_factory,
        )
        self._test_radio_state = TestRadio(
            self._logger, self.get_current_uid, lambda: self._test_station, radio_factory, spotify_factory, speaker
        )
        for state in [
            self._play_album_state,
            self._test_radio_state,
            End(self._logger, rgbled),
            CheckTag(self._logger, tags, targets, self.get_current_uid, sync, self._start_track, speaker),
            FetchAlbum(self._logger, rgbled, self._fetch_album),
            Blocked(self._logger),
            UnknownAlbum(self._logger, rgbled, self.get_current_uid),
            UnmappedTag(self._logger, rgbled, self.get_current_uid),
            BrokenAlbum(self._logger, rgbled, self.get_current_uid),
            WaitForTag(self._logger, rgbled, speaker),
            IdleLong(self._logger, rgbled, speaker),
        ]:
            self._machine.addstate(state)
        self._speaker_state: SpotifySpeaker | None = None
        if speaker is not None:
            self._speaker_state = SpotifySpeaker(self._logger, rgbled, speaker, pubsub_client)
            self._machine.addstate(self._speaker_state)
            speaker.on_speaker_plays = self.speaker_plays
            speaker.on_speaker_ends = self.speaker_ends
            speaker.on_speaker_paused = self.speaker_paused
        self._add_transitions()

    def _add_transitions(self) -> None:
        machine = self._machine
        machine.setstartstate(States.WAIT_FOR_TAG)
        machine.addtransition(States.WAIT_FOR_TAG, Transitions.NEW_TAG, States.CHECK_TAG)
        # no tag for a long time: only the led changes - IDLE_LONG is left like WAIT_FOR_TAG
        machine.addtransition(States.WAIT_FOR_TAG, Transitions.IDLE_TIMEOUT, States.IDLE_LONG)
        machine.addtimeoutevent(States.WAIT_FOR_TAG, Transitions.IDLE_TIMEOUT, self._idle_blink_after)
        machine.addtransition(States.IDLE_LONG, Transitions.NEW_TAG, States.CHECK_TAG)
        machine.addtransition(States.CHECK_TAG, Transitions.NO_TAG, States.WAIT_FOR_TAG)
        machine.addtransition(States.CHECK_TAG, Transitions.TAG_UNMAPPED, States.UNMAPPED_TAG)
        machine.addtransition(States.CHECK_TAG, Transitions.ALBUM_UNKNOWN, States.UNKNOWN_ALBUM)
        machine.addtransition(States.CHECK_TAG, Transitions.ALBUM_KNOWN, States.PLAY_ALBUM)
        machine.addtransition(States.PLAY_ALBUM, Transitions.ALBUM_BROKEN, States.BROKEN_ALBUM)
        # the album is fetched from the library; it plays as soon as the track that is due has arrived
        machine.addtransition(States.CHECK_TAG, Transitions.ALBUM_FETCH, States.FETCH_ALBUM)
        machine.addtransition(States.FETCH_ALBUM, Transitions.ALBUM_READY, States.PLAY_ALBUM)
        machine.addtransition(States.FETCH_ALBUM, Transitions.FETCH_FAILED, States.BROKEN_ALBUM)
        machine.addtransition(States.CHECK_TAG, Transitions.BLOCKED, States.BLOCKED)
        # tag removed or replaced by another tag without a "no tag" message in between
        for state in [
            States.PLAY_ALBUM,
            States.UNMAPPED_TAG,
            States.UNKNOWN_ALBUM,
            States.BROKEN_ALBUM,
            States.FETCH_ALBUM,
            States.BLOCKED,
        ]:
            machine.addtransition(state, Transitions.NO_TAG, States.WAIT_FOR_TAG)
            machine.addtransition(state, Transitions.NEW_TAG, States.CHECK_TAG)
        # radio test: only from "no tag"; a placed tag ends it, CHECK_TAG decides what follows the test
        for state in [States.WAIT_FOR_TAG, States.IDLE_LONG]:
            machine.addtransition(state, Transitions.TEST_RADIO, States.TEST_RADIO)
        machine.addtransition(States.TEST_RADIO, Transitions.TEST_RADIO, States.TEST_RADIO)
        machine.addtransition(States.TEST_RADIO, Transitions.TEST_END, States.CHECK_TAG)
        machine.addtransition(States.TEST_RADIO, Transitions.NEW_TAG, States.CHECK_TAG)
        machine.addtransition(States.TEST_RADIO, Transitions.NO_TAG, States.WAIT_FOR_TAG)
        machine.addtimeoutevent(States.TEST_RADIO, Transitions.TEST_END, self._radio_test_duration)
        if self._speaker is not None:
            # the box as a Spotify speaker: only from "no tag"; a tag or a test ends it
            for state in [States.WAIT_FOR_TAG, States.IDLE_LONG]:
                machine.addtransition(state, Transitions.SPEAKER_PLAYS, States.SPOTIFY_SPEAKER)
            machine.addtransition(States.SPOTIFY_SPEAKER, Transitions.SPEAKER_ENDS, States.WAIT_FOR_TAG)
            machine.addtransition(States.SPOTIFY_SPEAKER, Transitions.NEW_TAG, States.CHECK_TAG)
            machine.addtransition(States.SPOTIFY_SPEAKER, Transitions.TEST_RADIO, States.TEST_RADIO)
        for state in States:
            if state != States.END and (state != States.SPOTIFY_SPEAKER or self._speaker is not None):
                machine.addtransition(state, Transitions.END, States.END)

    @property
    def machine(self) -> object:
        """The tantamount machine (diagram export, tests)."""
        return self._machine

    def dump_to_dot(self, filename: str | Path) -> None:
        write_dot_file(self._machine, filename)

    def get_current_uid(self) -> str | None:
        return self._current_uid

    def get_album(self) -> AlbumControl | None:
        """The active album or radio playback (also a radio test) or None."""
        speaker = self._speaker_state.get_album() if self._speaker_state is not None else None
        return self._play_album_state.get_album() or self._test_radio_state.get_album() or speaker

    def _note(self, event: str, **fields: object) -> None:
        """Something for the journal of the box."""
        self._pubsub_client.publish(JOURNAL, JournalEvent.of(event, **fields))

    def _linked_to(self, uid: str) -> dict[str, object]:
        """What the tag ``uid`` plays, for the journal: ``kind`` (album, radio) and ``target``, or nothing."""
        tag = self._tags.tags.get(uid)
        if tag is None or is_unmapped(tag.target_id) or tag.target_type is None:
            return {}
        return {"kind": tag.target_type.name, "target": str(tag.target_id)}

    def _start_track(self, uid: str) -> int:
        """The track at which the album of the tag ``uid`` continues."""
        tag = self._tags.tags.get(uid)
        activity = self._activities.activities.get(uid)
        if tag is None or activity is None or str(activity.target_id) != str(tag.target_id) or activity.finished:
            return 0
        return max(0, activity.track_id)

    def _fetch_album(self) -> Enum | None:
        """Entry of FETCH_ALBUM: ask for the album of the tag on the box, the track that is due first."""
        uid = self.get_current_uid()
        tag = self._tags.tags.get(uid) if uid is not None else None
        if uid is None or tag is None or self._sync is None:
            return Transitions.NO_TAG if uid is None else Transitions.FETCH_FAILED
        album, track = str(tag.target_id), self._start_track(uid)
        self._fetching = (album, track)
        self._sync.files.need(album, track)
        return Transitions.ALBUM_READY if self._sync.files.ready(album, track) else None

    def _file_arrived(self, album: str) -> None:
        """A file of an album arrived, or its download ended (thread of the download) - only FETCH_ALBUM reacts."""
        fetching = self._fetching
        if self._sync is None or fetching is None or fetching[0] != album:
            return
        if self._sync.files.ready(*fetching):
            self._machine.asyncoperate(Transitions.ALBUM_READY)
        elif self._sync.files.failed(album) is not None:
            self._machine.asyncoperate(Transitions.FETCH_FAILED)

    def test_radio(self, station: TargetRadio | TargetSpotify) -> None:
        """Test the stream of ``station`` (may be unsaved) or an entry of Spotify for :data:`RADIO_TEST_DURATION`
        seconds - only while no tag is on the box. :raises RuntimeError: without player or if a tag is on the box."""
        if (self._spotify_factory if isinstance(station, TargetSpotify) else self._radio_factory) is None:
            raise RuntimeError("no radio player")
        with self._rfid_handler_lock:
            if self._current_uid is not None:
                raise RuntimeError("a tag is on the box")
            self._test_station = station
            self._machine.asyncoperate(Transitions.TEST_RADIO)

    def stop_radio_test(self) -> None:
        self._machine.asyncoperate(Transitions.TEST_END)

    def speaker_plays(self) -> None:
        """The Spotify app plays on the box - heard only while no tag is on it."""
        with self._rfid_handler_lock:
            if self._current_uid is None:
                self._machine.asyncoperate(Transitions.SPEAKER_PLAYS)

    def speaker_ends(self) -> None:
        self._machine.asyncoperate(Transitions.SPEAKER_ENDS)

    def speaker_paused(self, paused: bool) -> None:
        """LED of the speaker: white/blue while what the phone plays is paused, blue while it plays."""
        if self.get_active_state() == States.SPOTIFY_SPEAKER:
            if paused:
                self._rgbled.set_alternate_colors(Color.WHITE, Color.BLUE, 1)
            else:
                self._rgbled.set_color(Color.BLUE)

    def get_active_state(self) -> States:
        return self._machine.get_active_state().id  # type: ignore[no-any-return]

    def wait_until_idle(self) -> None:
        """Block until all queued events have been processed (tests)."""
        self._machine.wait_until_idle()

    def recheck_tag(self) -> None:
        """Evaluate the present tag again - e.g. after it has been linked to another album."""
        with self._rfid_handler_lock:
            if self._current_uid is not None:
                self._logger.info("recheck_tag - %s", self._current_uid)
                self._machine.asyncoperate(Transitions.NEW_TAG)

    def _forced_polling(self) -> None:
        while not self._rfid_received.wait(self._poll_interval):
            self._request_rfid_state()

    def _request_rfid_state(self) -> None:
        self._logger.info("requesting the current rfid state")
        self._pubsub_client.publish(RFID_POLL, None)

    def _rfid_handler(self, message: object) -> None:
        self._rfid_received.set()
        self._logger.info("_rfid_handler - received message %r", message)
        if not isinstance(message, RfidState):
            self._logger.error("_rfid_handler - cannot process message %r: no RfidState", message)
            return
        uid = message.uid
        with self._rfid_handler_lock:
            if uid is None:
                if self._current_uid is not None:
                    self._logger.info("_rfid_handler - tag removed.")
                    self._note("end", uid=self._current_uid, reason="tag removed")
                    self._current_uid = None
                    self._machine.asyncoperate(Transitions.NO_TAG)
            elif self._current_uid != uid:
                self._logger.info("_rfid_handler - activating uid %s", uid)
                if self._current_uid is not None:
                    self._note("end", uid=self._current_uid, reason="tag replaced")
                self._note("tag", uid=uid, **self._linked_to(uid))
                self._current_uid = uid
                self._latency.begin(self._pubsub_client.published_at())
                self._latency.step("NEW_TAG")
                self._machine.asyncoperate(Transitions.NEW_TAG)
            else:
                self._logger.info("_rfid_handler - ignored uid (%s) - this one is already active.", uid)

    def start(self) -> None:
        self._machine.start()
        if self._sync is not None:
            self._sync.files.subscribe(self._file_arrived)
        self._pubsub_client.subscribe(RFID_STATE, self._rfid_handler)
        self._rfid_received.clear()
        self._request_rfid_state()
        self._polling_thread = threading.Thread(target=self._forced_polling, name="STMOperation.polling", daemon=True)
        self._polling_thread.start()

    def stop(self) -> None:
        self._rfid_received.set()  # ends the forced polling
        if self._polling_thread is not None:
            self._polling_thread.join()
            self._polling_thread = None
        self._pubsub_client.unsubscribe(RFID_STATE, self._rfid_handler, ignore_not_found=True)
        if self._sync is not None:
            self._sync.files.unsubscribe(self._file_arrived)
        if self._machine._startdone:
            self._machine.operate(Transitions.END)
            self._machine.stop()
