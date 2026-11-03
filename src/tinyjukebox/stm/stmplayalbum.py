"""STMPlayAlbum - the state machine that plays one album (next/prev/play/pause buttons, prev held: from the
beginning, pause between tracks, resume, switching between speaker and headset, waiting for a track that is still
being fetched from the library)."""

import contextlib
import logging
import os
import time
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tinyjukebox.albumplayer.player_interface import Hook, PlayerInterface
from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.messages import (
    BUTTON_HELD,
    BUTTON_PRESSED,
    BUTTON_RELEASED,
    JOURNAL,
    PLAYBACK,
    Button,
    JournalEvent,
    PlaybackStatus,
)
from tinyjukebox.rgbled import WAITING, Color, RGBLed
from tinyjukebox.stm.base import OutputPause, PauseReason, PeriodicTask, State, create_machine, write_dot_file
from tinyjukebox.sync import SOFTWARE, actor

if TYPE_CHECKING:
    from tinyjukebox.sync.files import AlbumFiles

__all__ = [
    "BOTH_BUTTONS_WINDOW",
    "OUTPUT_REWIND",
    "BothButtons",
    "PlayerFactory",
    "STMPlayAlbum",
    "States",
    "Transitions",
]

PlayerFactory = Callable[[logging.Logger, TargetAlbum, Hook, Hook], PlayerInterface]
"""(logger, album, end_of_track_hook, end_of_album_hook) -> player"""

OUTPUT_REWIND = 1.0
"""Seconds a track goes back when the output changes or is lost - the sound in the buffers has not been heard."""

MAX_ERROR_LENGTH = 500
"""Characters of what the decoder said about a damaged file that are noted for the library - its end."""

BOTH_BUTTONS_WINDOW = 2.0
"""Seconds: 'next' pressed at most this long before 'prev' (and not released) - or after it - makes a held 'prev' the
gesture of both buttons (pairing a headset, see :mod:`tinyjukebox.headset`) and not a restart of the album. Presses
without release (web page, buttons of the headset) are forgotten after it."""


class BothButtons:
    """Tells whether 'next' is pressed together with a held 'prev'. Fed by the button handlers, which all run in the
    pubsub dispatch thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pressed_at: dict[Button, float] = {}

    def pressed(self, button: object) -> None:
        if isinstance(button, Button):
            self._pressed_at[button] = self._clock()

    def released(self, button: object) -> None:
        if isinstance(button, Button):
            self._pressed_at.pop(button, None)

    def next_is_pressed(self) -> bool:
        """Called when 'prev' is held."""
        next_pressed_at = self._pressed_at.get(Button.NEXT)
        if next_pressed_at is None:
            return False
        prev_pressed_at = self._pressed_at.get(Button.PREV, self._clock())  # held without press: web page
        return next_pressed_at >= prev_pressed_at - BOTH_BUTTONS_WINDOW


class States(Enum):
    """States of the STMPlayAlbum state machine."""

    INIT = "init"  # loading the album
    PLAY = "play"  # playing the current track
    TONEXT = "tonext"  # switching to the next track
    DELAY = "delay"  # pause between two tracks
    TOPREV = "toprev"  # switching to the previous track
    PREV = "prev"  # deciding: rewind the current track or go to the previous one
    REWIND = "rewind"  # rewinding the current track
    FINISHED = "finished"  # album played to its end
    END = "end"  # final state
    FIRST = "first"  # switching to the first track
    PAUSED = "paused"  # paused (play/pause button or headset lost) - the position is kept
    RESUME = "resume"  # deciding after the headset came back: continue or stay paused
    WAIT = "wait"  # the file of the current track has not arrived from the library yet


class Transitions(Enum):
    """Transitions of the STMPlayAlbum state machine."""

    END = "end"
    PLAY = "play"
    NEXT = "next"
    DELAY = "delay"
    PREV = "prev"
    TOPREV = "toprev"
    REWIND = "rewind"
    FIRST = "first"
    FINISHED = "finished"
    PAUSE = "pause"  # play/pause button
    OUTPUT_LOST = "output_lost"  # the headset has gone - pause
    OUTPUT_CHANGED = "output_changed"  # the box plays on another output now (headset connected)
    STAY = "stay"  # stay paused
    WAIT = "wait"  # the file of the current track is not there yet
    ARRIVED = "arrived"  # a file of the album has arrived


class _PlayerState(State):
    """State with access to the album player (created in INIT)."""

    def __init__(self, state_id: States, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(state_id, logger)
        self._owner = owner

    @property
    def _player(self) -> PlayerInterface:
        player = self._owner.player
        if player is None:
            raise RuntimeError("album player not initialized")
        return player


class Init(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rgbled: RGBLed) -> None:
        super().__init__(States.INIT, logger, owner)
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_color(Color.AQUA)
        activity = self._owner.activity
        self._logger.info("initializing album: %s", self._owner.album.to_text())
        player = self._owner.create_player()
        player.start_player()
        if activity.finished:
            self._logger.info("album has been finished last time - starting from the beginning")
        elif not 0 <= activity.track_id < player.album_track_count():
            # e.g. the playlist has been changed since the position has been stored
            self._logger.warning(
                "stored track id %s is not valid for an album with %s tracks - starting from the beginning",
                activity.track_id,
                player.album_track_count(),
            )
        else:
            player.set_track(activity.track_id, activity.track_time)
        self._owner.player = player
        return Transitions.PLAY


class Play(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rgbled: RGBLed, update_schedule: float) -> None:
        super().__init__(States.PLAY, logger, owner)
        self._rgbled = rgbled
        self._update_schedule = update_schedule
        self._update_task: PeriodicTask | None = None
        self._started: tuple[int, float] | None = None  # track and position at which the playback began

    def _update_activity(self) -> None:
        player = self._player
        self._owner.save_position(player.current_track_id(), player.current_track_time(), player.album_end_reached())

    def _on_entry(self) -> Enum | None:
        if not self._player.current_track_ready():
            return Transitions.WAIT
        self._rgbled.set_color(Color.BLUE)
        self._update_activity()
        try:
            self._player.play()
        except OSError as error:
            if not self._owner.started:
                raise  # the first track at the start: the album cannot be played (STMOperation shows it as broken)
            # later ffmpeg or aplay cannot be started (removed meanwhile, no process left): paused - "play" tries again
            self._logger.error("the track cannot be played: %s", error)
            unexpected.report(error, "album player")
            return Transitions.PAUSE
        self._started = (self._player.current_track_id(), self._player.current_track_time())
        self._owner.report(PlaybackStatus.PLAYING)
        # started after play() succeeded - on_exit is not called if on_entry raises
        self._update_task = PeriodicTask(
            self._update_schedule, self._update_activity, "STMPlayAlbum.update", self._logger
        )
        self._update_task.start()
        return None

    def _on_exit(self) -> None:
        self._player.pause()
        if self._update_task is not None:
            self._update_task.stop()
            self._update_task = None
        self._update_activity()
        if self._started is not None:
            # for the journal: which track was listened to, for how long, and whether to its end
            player, (track, position) = self._player, self._started
            self._started = None
            self._owner.note(
                "track",
                track=track,
                seconds=round(max(0.0, player.current_track_time() - position), 1),
                to_end=player.current_track_id() == track and player.current_track_end_reached(),
            )


class ToNext(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.TONEXT, logger, owner)

    def _on_entry(self) -> Enum | None:
        return Transitions.PLAY if self._player.to_next() else Transitions.FINISHED


class Prev(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rewind_max_time: float) -> None:
        super().__init__(States.PREV, logger, owner)
        self._rewind_max_time = rewind_max_time

    def _on_entry(self) -> Enum | None:
        # within the first seconds of a track 'prev' goes to the previous track, later it rewinds the current one
        if self._player.current_track_time() <= self._rewind_max_time:
            return Transitions.TOPREV
        return Transitions.REWIND


class Rewind(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.REWIND, logger, owner)

    def _on_entry(self) -> Enum | None:
        self._player.rewind_track()
        return Transitions.PLAY


class ToPrev(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.TOPREV, logger, owner)

    def _on_entry(self) -> Enum | None:
        self._player.to_prev()
        return Transitions.PLAY


class First(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.FIRST, logger, owner)

    def _on_entry(self) -> Enum | None:
        self._player.to_first()
        return Transitions.PLAY


class Finished(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rgbled: RGBLed) -> None:
        super().__init__(States.FINISHED, logger, owner)
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_blink_color(Color.BLUE, 1)
        player = self._player
        self._owner.save_position(player.current_track_id(), player.current_track_time(), True)
        self._owner.report(PlaybackStatus.STOPPED)
        self._owner.note("end", reason="album finished")
        return None


class Paused(_PlayerState):
    """Nothing plays, the position is stored; the tag stays on the box. LED: white/blue, slowly alternating."""

    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rgbled: RGBLed) -> None:
        super().__init__(States.PAUSED, logger, owner)
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_alternate_colors(Color.WHITE, Color.BLUE, 1)
        player = self._player
        self._owner.save_position(player.current_track_id(), player.current_track_time(), False)
        self._owner.report(PlaybackStatus.PAUSED)
        return None


class Wait(_PlayerState):
    """The playback is halted until the file of the current track has arrived from the library. LED: green, aqua,
    blue flowing back and forth."""

    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum", rgbled: RGBLed) -> None:
        super().__init__(States.WAIT, logger, owner)
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_sequence(WAITING)
        player = self._player
        self._owner.save_position(player.current_track_id(), player.current_track_time(), False)
        # "next" on a missing track pulls this track forward in the download
        self._owner.want_track(player.current_track_id())
        return Transitions.ARRIVED if player.current_track_ready() else None


class Resume(_PlayerState):
    """The headset has come back: a pause caused by its loss is continued (within the resume window)."""

    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.RESUME, logger, owner)

    def _on_entry(self) -> Enum | None:
        return Transitions.PLAY if self._owner.pause.resumes_on_output() else Transitions.STAY


class End(_PlayerState):
    def __init__(self, logger: logging.Logger, owner: "STMPlayAlbum") -> None:
        super().__init__(States.END, logger, owner)

    def _on_entry(self) -> Enum | None:
        if self._owner.player is not None:
            self._owner.player.stop_player()
        self._owner.report(PlaybackStatus.STOPPED)
        return None


class Delay(State):
    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(States.DELAY, logger)


class STMPlayAlbum:
    """Plays ``album`` from the position stored in ``activity`` and keeps the position up to date.

    The next/prev/play/pause buttons arrive on :data:`~tinyjukebox.messages.BUTTON_PRESSED`. 'prev' held
    (:data:`~tinyjukebox.messages.BUTTON_HELD`, after the ``hold_timeout`` of its input driver) plays the album from the
    beginning - unless 'next' is pressed as well (pairing a headset). All events - buttons, end of
    track, web page, headset - are queued (``asyncoperate``); handlers never block. The playback status is published
    to :data:`~tinyjukebox.messages.PLAYBACK` (for the headset).

    Pause: play and next continue at the same position, prev goes back at once (as while playing). A pause caused by
    the loss of the headset (:meth:`cmd_output_lost`) is continued when the headset comes back within
    :data:`~tinyjukebox.stm.base.RESUME_WINDOW` (:meth:`cmd_output_changed`).
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
        rgbled: RGBLed,
        album: TargetAlbum,
        activity: ActivityAlbum,
        player_factory: PlayerFactory,
        files: "AlbumFiles | None" = None,
    ) -> None:
        """``files``: fetches the album from the library while it plays (:mod:`tinyjukebox.sync`)."""
        self._logger = get_child(logger, type(self).__name__)
        self._files = files
        self._pubsub_client = pubsub_client
        self._player_factory = player_factory
        self._pause_between_tracks: float = config["pause-between-tracks"]
        self.album = album
        self.activity = activity
        self.player: PlayerInterface | None = None
        self.pause = OutputPause()
        self._reported: PlaybackStatus | None = None
        self._saving_failed = False  # the position could not be written last time
        self.started = False  # the machine has started: a track that cannot be played pauses the album from then on
        self._both_buttons = BothButtons()

        self._machine = create_machine(self._logger)
        for state in [
            End(self._logger, self),
            Finished(self._logger, self, rgbled),
            Init(self._logger, self, rgbled),
            ToNext(self._logger, self),
            Play(self._logger, self, rgbled, config["update-schedule"]),
            Prev(self._logger, self, config["rewind-max-time"]),
            ToPrev(self._logger, self),
            Rewind(self._logger, self),
            First(self._logger, self),
            Delay(self._logger),
            Paused(self._logger, self, rgbled),
            Resume(self._logger, self),
            Wait(self._logger, self, rgbled),
        ]:
            self._machine.addstate(state)
        self._add_transitions()

    def _add_transitions(self) -> None:
        machine = self._machine
        machine.setstartstate(States.INIT)
        machine.addtransition(States.INIT, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.PLAY, Transitions.FIRST, States.FIRST)
        machine.addtransition(States.PLAY, Transitions.NEXT, States.TONEXT)
        machine.addtransition(States.PLAY, Transitions.DELAY, States.DELAY)
        machine.addtransition(States.PLAY, Transitions.PREV, States.PREV)
        machine.addtransition(States.PLAY, Transitions.FINISHED, States.FINISHED)
        machine.addtransition(States.PREV, Transitions.TOPREV, States.TOPREV)
        machine.addtransition(States.PREV, Transitions.REWIND, States.REWIND)
        machine.addtransition(States.TONEXT, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.TONEXT, Transitions.FINISHED, States.FINISHED)
        machine.addtransition(States.TOPREV, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.DELAY, Transitions.NEXT, States.TONEXT)
        machine.addtransition(States.DELAY, Transitions.PREV, States.TOPREV)
        machine.addtransition(States.REWIND, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.FIRST, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.FINISHED, Transitions.PREV, States.REWIND)  # the last track once more
        machine.addtransition(States.FINISHED, Transitions.FIRST, States.FIRST)
        # pause - the actions run between on_exit of the old state (the player is paused) and on_entry of PAUSED
        machine.addtransition(States.PLAY, Transitions.PAUSE, States.PAUSED, self._paused, (PauseReason.USER, False))
        machine.addtransition(
            States.PLAY, Transitions.OUTPUT_LOST, States.PAUSED, self._paused, (PauseReason.OUTPUT, False)
        )
        # between two tracks the next track is selected - it starts when the pause ends
        machine.addtransition(States.DELAY, Transitions.PAUSE, States.PAUSED, self._paused, (PauseReason.USER, True))
        machine.addtransition(
            States.DELAY, Transitions.OUTPUT_LOST, States.PAUSED, self._paused, (PauseReason.OUTPUT, True)
        )
        machine.addtransition(States.PAUSED, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.PAUSED, Transitions.NEXT, States.PLAY)  # the 1st press continues
        machine.addtransition(States.PAUSED, Transitions.PREV, States.PREV)
        machine.addtransition(States.PAUSED, Transitions.FIRST, States.FIRST)
        machine.addtransition(States.PAUSED, Transitions.OUTPUT_CHANGED, States.RESUME)
        machine.addtransition(States.RESUME, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.RESUME, Transitions.STAY, States.PAUSED)
        # the file of the track that is due has not arrived yet: PLAY sends on to WAIT, a file that arrives back
        machine.addtransition(States.PLAY, Transitions.WAIT, States.WAIT)
        machine.addtransition(States.WAIT, Transitions.ARRIVED, States.PLAY)
        machine.addtransition(States.WAIT, Transitions.NEXT, States.TONEXT)
        machine.addtransition(States.WAIT, Transitions.PREV, States.TOPREV)
        machine.addtransition(States.WAIT, Transitions.FIRST, States.FIRST)
        # the headset goes while the box waits for a file: paused, so that the file that arrives does not start the
        # track on the speaker
        machine.addtransition(
            States.WAIT, Transitions.OUTPUT_LOST, States.PAUSED, self._paused, (PauseReason.OUTPUT, False, False)
        )
        # the track continues on the new output - PLAY is left (player paused) and entered again
        machine.addtransition(States.PLAY, Transitions.OUTPUT_CHANGED, States.PLAY, self._rewind_output)
        for state in States:
            if state != States.END:
                machine.addtransition(state, Transitions.END, States.END)
        machine.addtimeoutevent(States.DELAY, Transitions.NEXT, self._pause_between_tracks)

    @property
    def machine(self) -> object:
        """The tantamount machine (diagram export, tests)."""
        return self._machine

    def create_player(self) -> PlayerInterface:
        player = self._player_factory(self._logger, self.album, self._end_of_track, self._album_finished)
        player.set_output_lost_hook(self.cmd_output_lost)
        player.set_track_failed_hook(self._track_failed)
        return player

    def _track_failed(self, track: int, error: str) -> None:
        """The decoder found the file of a track damaged: noted for the library, which collects the broken
        files."""
        name = os.path.relpath(self.album.get_tracks()[track], str(self.album.full_album_folder))
        self.note("broken", track=track, file=name.replace(os.sep, "/"), error=error[-MAX_ERROR_LENGTH:])

    def save_position(self, track_id: int, track_time: float, finished: bool) -> None:
        """Store the position. A card that cannot be written - full, or read-only after a fault - stops nothing: the
        album plays on, and the error is logged once."""
        try:
            self.activity.update(track_id, track_time, finished)
        except OSError as error:
            if not self._saving_failed:
                self._saving_failed = True
                self._logger.error("the position cannot be saved: %s - the album plays on", error)
            return
        if self._saving_failed:
            self._saving_failed = False
            self._logger.info("the position is saved again")

    def report(self, status: PlaybackStatus) -> None:
        """Publish the playback status (for the headset) if it changed."""
        if status != self._reported:
            self._reported = status
            self._pubsub_client.publish(PLAYBACK, status)

    def note(self, event: str, **fields: object) -> None:
        """Something for the journal of the box about this album."""
        self._pubsub_client.publish(JOURNAL, JournalEvent.of(event, album=str(self.album.target_id), **fields))

    def _control(self, action: str) -> None:
        """A command changed the playback - by a button (of the box or the headset) or by a user of the web page."""
        who = actor()
        self.note("control", action=action, by="button" if who == SOFTWARE else who)

    def _paused(self, reason: PauseReason, next_track: bool, rewind: bool = True) -> None:
        """Transition action into PAUSED: remember why; ``next_track``: paused between two tracks; ``rewind``: the
        end of the playback was not heard (not while waiting for a file - nothing played)."""
        self.pause.paused(reason)
        player = self.player
        if player is None:
            return
        if next_track:
            player.to_next()
        elif reason is PauseReason.OUTPUT and rewind:
            self._rewind_output()

    def _rewind_output(self) -> None:
        """Transition action: back :data:`OUTPUT_REWIND` seconds - the end of the playback has not been heard."""
        player = self.player
        if player is not None:
            player.set_track(player.current_track_id(), max(0.0, player.current_track_time() - OUTPUT_REWIND))

    def want_track(self, track_id: int) -> None:
        """Ask for the file of the track ``track_id`` first."""
        if self._files is not None:
            self._files.need(str(self.album.target_id), track_id)

    def _file_arrived(self, album: str) -> None:
        """A file of an album arrived (thread of the download) - only WAIT reacts."""
        if album == str(self.album.target_id):
            self._machine.asyncoperate(Transitions.ARRIVED)

    def _end_of_track(self) -> None:
        """Called by the player when a track has been played to its end."""
        self._machine.asyncoperate(Transitions.NEXT if self._pause_between_tracks == 0 else Transitions.DELAY)

    def _album_finished(self) -> None:
        """Called by the player when the last track has been played to its end."""
        self._machine.asyncoperate(Transitions.FINISHED)

    def cmd_next_handler(self) -> None:
        self._control("next")
        self._machine.asyncoperate(Transitions.NEXT)

    def cmd_prev_handler(self) -> None:
        self._control("prev")
        self._machine.asyncoperate(Transitions.PREV)

    def cmd_restart_album(self) -> None:
        self._control("restart")
        self._machine.asyncoperate(Transitions.FIRST)

    def cmd_pause(self) -> None:
        self._control("pause")
        self._machine.asyncoperate(Transitions.PAUSE)

    def cmd_play(self) -> None:
        self._control("play")
        self._machine.asyncoperate(Transitions.PLAY)

    def cmd_output_lost(self, then: Callable[[], None] | None = None) -> None:
        """The headset has gone: pause - unless the album is paused or finished already. ``then`` is called in the
        worker once that has been handled."""
        self._machine.asyncoperate(Transitions.OUTPUT_LOST)
        if then is not None:
            self._machine.call_soon(then)

    def cmd_output_changed(self) -> None:
        """The output has changed (headset connected): a playing track continues on it, a pause caused by the loss
        of the headset ends."""
        self._machine.asyncoperate(Transitions.OUTPUT_CHANGED)

    def get_state(self) -> States:
        return self._machine.get_active_state().id  # type: ignore[no-any-return]

    def wait_until_idle(self) -> None:
        """Block until all queued events have been processed (tests)."""
        self._machine.wait_until_idle()

    def get_info(self) -> dict[str, Any]:
        player = self.player
        info: dict[str, Any] = {
            "target_id": self.album.target_id,
            "target_type": "album",
            "album_folder": self.album.album_folder,
            "playlist": self.album.playlist,
            "state": self.get_state().value,
            "tracks": self.album.get_tracks(),
            "track": {"track_id": None, "filename": None, "pos": None, "length": None},
        }
        if player is not None:
            info["track"] = {
                "track_id": player.current_track_id(),
                "filename": player.current_track_filename(),
                "pos": player.current_track_time(),
                "length": player.current_track_length(),
            }
        return info

    def dump_to_dot(self, filename: str | Path) -> None:
        write_dot_file(self._machine, filename)

    def _button_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_PRESSED` - reacts on next/prev/play/pause, ignores the other
        buttons."""
        self._both_buttons.pressed(message)
        command = {
            Button.NEXT: self.cmd_next_handler,
            Button.PREV: self.cmd_prev_handler,
            Button.PLAY: self.cmd_play,
            Button.PAUSE: self.cmd_pause,
        }.get(message)  # type: ignore[call-overload]  # any payload - unknown ones are ignored
        if command is not None:
            self._logger.info("_button_handler - received: %s", message)
            command()

    def _released_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_RELEASED`."""
        self._both_buttons.released(message)

    def _held_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_HELD` - 'prev' held alone: the album from the beginning."""
        if message != Button.PREV:
            return
        if self._both_buttons.next_is_pressed():
            self._logger.info("_held_handler - prev held together with next: no restart")
            return
        self._logger.info("_held_handler - prev held: restarting the album")
        self.cmd_restart_album()

    def start(self) -> None:
        """Load the album and start playing. :raises Exception: if the album cannot be loaded (machine stopped)."""
        try:
            self._machine.start()
        except Exception:
            # e.g. album cannot be loaded in INIT - stop the machine, otherwise its worker thread is left running
            self._machine.stop()
            raise
        self.started = True
        self._pubsub_client.subscribe(BUTTON_PRESSED, self._button_handler)
        self._pubsub_client.subscribe(BUTTON_HELD, self._held_handler)
        self._pubsub_client.subscribe(BUTTON_RELEASED, self._released_handler)
        if self._files is not None:
            self._files.subscribe(self._file_arrived)

    def stop(self) -> None:
        if self._files is not None:
            self._files.unsubscribe(self._file_arrived)
        self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._button_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_HELD, self._held_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_RELEASED, self._released_handler, ignore_not_found=True)
        try:
            self._machine.operate(Transitions.END)
        except Exception as error:
            # what ended in an exception is logged; the machine and its threads end all the same, and so does the sound
            self._logger.exception("the album does not end cleanly")
            unexpected.report(error, "end of an album")
            if self.player is not None:
                with contextlib.suppress(Exception):
                    self.player.stop_player()
        finally:
            self._machine.stop()
