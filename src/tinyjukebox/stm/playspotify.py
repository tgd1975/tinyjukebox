"""PlaySpotify - plays the entry of Spotify linked to a tag (the counterpart of STMPlayAlbum for Spotify).

Like an album it has a position, which is saved every ``update-schedule`` seconds and when it is left, and the buttons
work as with an album: play/pause, "next", "back" (Soloist decides: the track before within the first seconds,
otherwise the start of the track), "back" held: from the beginning. The last track ended: finished. The playback
itself is :class:`~tinyjukebox.albumplayer.spotifyplayer.SpotifyPlayer`.
"""

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol

from tinyjukebox.albumplayer.spotifyplayer import SpotifyState, Track
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.target import TargetSpotify
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
from tinyjukebox.rgbled import Color, RGBLed
from tinyjukebox.stm.base import OutputPause, PauseReason, PeriodicTask, State, create_machine
from tinyjukebox.stm.stmplayalbum import BothButtons
from tinyjukebox.sync import SOFTWARE, actor

__all__ = ["PlaySpotify", "SpotifyPlayerFactory", "SpotifyPlayerInterface", "States", "Transitions"]

_LED = {
    SpotifyState.CONNECTING: Color.AQUA,
    SpotifyState.RETRYING: Color.AQUA,
    SpotifyState.PLAYING: Color.BLUE,
}


class SpotifyPlayerInterface(Protocol):
    """What PlaySpotify needs of :class:`~tinyjukebox.albumplayer.spotifyplayer.SpotifyPlayer`."""

    on_state: Callable[[SpotifyState], None]
    on_track: Callable[[Track, float, bool], None]
    on_finished: Callable[[], None]
    error: str | None

    @property
    def state(self) -> SpotifyState: ...

    def start(self, track: Track, seconds: float) -> None: ...

    def pause(self) -> None: ...

    def play(self) -> None: ...

    def next(self) -> None: ...

    def prev(self) -> None: ...

    def restart(self) -> None: ...

    def output_changed(self) -> None: ...

    def stop(self) -> None: ...

    def position(self) -> tuple[Track, float]: ...


SpotifyPlayerFactory = Callable[[logging.Logger, str], SpotifyPlayerInterface]
"""(logger, uri) -> player"""


class States(Enum):
    PLAY = "play"  # plays (or connects, or goes to the saved position)
    PAUSED = "paused"  # pause button, the headset lost, or the account plays elsewhere
    RESUME = "resume"  # deciding after the headset came back: continue or stay paused
    FINISHED = "finished"
    END = "end"


class Transitions(Enum):
    PLAY = "play"
    PAUSE = "pause"
    ELSEWHERE = "elsewhere"
    OUTPUT_LOST = "output_lost"
    OUTPUT_CHANGED = "output_changed"
    STAY = "stay"
    FINISHED = "finished"
    RESTART = "restart"
    END = "end"


class _Play(State):
    def __init__(self, logger: logging.Logger, owner: "PlaySpotify", update_schedule: float) -> None:
        super().__init__(States.PLAY, logger)
        self._owner = owner
        self._update_schedule = update_schedule
        self._task: PeriodicTask | None = None

    def _on_entry(self) -> Enum | None:
        self._owner.playing()
        self._task = PeriodicTask(self._update_schedule, self._owner.save, "PlaySpotify.update", self._logger)
        self._task.start()
        return None

    def _on_exit(self) -> None:
        if self._task is not None:
            self._task.stop()
            self._task = None
        self._owner.save()


class _Paused(State):
    def __init__(self, logger: logging.Logger, owner: "PlaySpotify", rgbled: RGBLed) -> None:
        super().__init__(States.PAUSED, logger)
        self._owner = owner
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._owner.player.pause()
        self._rgbled.set_alternate_colors(Color.WHITE, Color.BLUE, 1)
        self._owner.report(PlaybackStatus.PAUSED)
        return None


class _Resume(State):
    def __init__(self, logger: logging.Logger, owner: "PlaySpotify") -> None:
        super().__init__(States.RESUME, logger)
        self._owner = owner

    def _on_entry(self) -> Enum | None:
        return Transitions.PLAY if self._owner.pause.resumes_on_output() else Transitions.STAY


class _Finished(State):
    def __init__(self, logger: logging.Logger, owner: "PlaySpotify", rgbled: RGBLed) -> None:
        super().__init__(States.FINISHED, logger)
        self._owner = owner
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_blink_color(Color.BLUE, 1)
        self._owner.save(finished=True)
        self._owner.note("end", reason="album finished")
        self._owner.report(PlaybackStatus.STOPPED)
        return None


class _End(State):
    def __init__(self, logger: logging.Logger, owner: "PlaySpotify") -> None:
        super().__init__(States.END, logger)
        self._owner = owner

    def _on_entry(self) -> Enum | None:
        self._owner.report(PlaybackStatus.STOPPED)
        return None


class PlaySpotify:
    """Plays ``entry`` from the position of ``activity`` until :meth:`stop` (``activity`` None: a test - from the
    beginning, nothing is saved). LED: aqua while it connects and goes to the position, blue while it plays.

    ``check()`` raises if Spotify cannot play on this box (no key, not connected, no player): :meth:`start` raises
    then, and the tag ends as "cannot be played". ``lost()`` is called when Soloist says that the box has no login.
    """

    def __init__(
        self,
        logger: logging.Logger,
        rgbled: RGBLed,
        entry: TargetSpotify,
        activity: ActivityAlbum | None,
        player_factory: SpotifyPlayerFactory,
        pubsub_client: PubSubClient | None = None,
        update_schedule: float = 5.0,
        check: Callable[[], object] = lambda: None,
        lost: Callable[[], None] = lambda: None,
        test: bool = False,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._rgbled = rgbled
        self._pubsub_client = pubsub_client
        self.entry = entry
        self._uri = entry.uri
        self.activity = activity
        self._check = check
        self.test = test
        self.player = player_factory(self._logger, entry.uri)
        self.player.on_state = self._state_changed
        self.player.on_track = self._track_left
        self.player.on_finished = lambda: self._machine.asyncoperate(Transitions.FINISHED)
        if hasattr(self.player, "on_lost"):
            self.player.on_lost = lost
        if hasattr(self.player, "on_output_lost"):
            self.player.on_output_lost = self.cmd_output_lost
        self.pause = OutputPause()
        self._both_buttons = BothButtons()
        self._reported: PlaybackStatus | None = None
        self._started = False

        self._machine = create_machine(self._logger)
        for state in (
            _Play(self._logger, self, update_schedule),
            _Paused(self._logger, self, rgbled),
            _Resume(self._logger, self),
            _Finished(self._logger, self, rgbled),
            _End(self._logger, self),
        ):
            self._machine.addstate(state)
        machine = self._machine
        machine.setstartstate(States.PLAY)
        machine.addtransition(States.PLAY, Transitions.PAUSE, States.PAUSED, self.pause.paused, (PauseReason.USER,))
        machine.addtransition(States.PLAY, Transitions.ELSEWHERE, States.PAUSED, self.pause.paused, (PauseReason.USER,))
        machine.addtransition(
            States.PLAY, Transitions.OUTPUT_LOST, States.PAUSED, self.pause.paused, (PauseReason.OUTPUT,)
        )
        machine.addtransition(States.PAUSED, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.PAUSED, Transitions.OUTPUT_CHANGED, States.RESUME)
        machine.addtransition(States.RESUME, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.RESUME, Transitions.STAY, States.PAUSED)
        for state_id in (States.PLAY, States.PAUSED):
            machine.addtransition(state_id, Transitions.FINISHED, States.FINISHED)
        for state_id in (States.PAUSED, States.FINISHED):
            machine.addtransition(state_id, Transitions.RESTART, States.PLAY, self.player.restart, ())
        machine.addtransition(States.PLAY, Transitions.RESTART, States.PLAY, self.player.restart, ())
        for state_id in (States.PLAY, States.PAUSED, States.RESUME, States.FINISHED):
            machine.addtransition(state_id, Transitions.END, States.END)

    @property
    def machine(self) -> object:
        return self._machine

    # --- for the states ----------------------------------------------------------------------------------------------

    def playing(self) -> None:
        """PLAY: the first time the playback starts at the saved position, later it continues."""
        if self._started:
            self.player.play()
        else:
            self._started = True
            activity = self.activity
            if activity is None or activity.finished:
                track, seconds = Track(), 0.0
            else:
                track, seconds = Track(activity.track_id, activity.track_uri), activity.track_time
            self._logger.info("playing %s from track %d, %.0f s", self.entry.to_text(), track.index, seconds)
            self.player.start(track, seconds)
        self._state_changed(self.player.state)
        self.report(PlaybackStatus.PLAYING)

    def save(self, finished: bool = False) -> None:
        """The position into the activity of the tag."""
        if self.activity is None or self.player.state in (SpotifyState.CONNECTING, SpotifyState.RETRYING):
            return
        track, seconds = self.player.position()
        if track.uri is None:
            return
        self.activity.update(track.index, round(seconds, 1), finished, track.uri)

    def report(self, status: PlaybackStatus) -> None:
        if status != self._reported and self._pubsub_client is not None:
            self._reported = status
            self._pubsub_client.publish(PLAYBACK, status)

    def note(self, event: str, **fields: object) -> None:
        """Something for the journal of the box - not for a test."""
        if self._pubsub_client is not None and not self.test:
            self._pubsub_client.publish(JOURNAL, JournalEvent.of(event, album=str(self.entry.target_id), **fields))

    def _control(self, action: str) -> None:
        who = actor()
        self.note("control", action=action, by="button" if who == SOFTWARE else who)

    def _state_changed(self, state: SpotifyState) -> None:
        """From the player: the LED; the account plays elsewhere: pause."""
        color = _LED.get(state)
        if self.get_state() != States.PLAY:
            return
        if color is not None:
            self._rgbled.set_color(color)
        elif state == SpotifyState.ERROR:
            self._rgbled.set_alternate_colors(Color.RED, Color.BLACK, 1)
        elif state == SpotifyState.ELSEWHERE:
            self._machine.asyncoperate(Transitions.ELSEWHERE)

    def _track_left(self, track: Track, seconds: float, to_end: bool) -> None:
        self.note("track", track=track.index, seconds=round(seconds, 1), to_end=to_end, uri=track.uri,
                  title=track.title)  # fmt: skip

    # --- commands ----------------------------------------------------------------------------------------------------

    def get_state(self) -> States:
        return self._machine.get_active_state().id  # type: ignore[no-any-return]

    def wait_until_idle(self) -> None:
        self._machine.wait_until_idle()

    def start(self) -> None:
        """Start the playback. :raises Exception: if Spotify cannot play on this box."""
        self._check()
        try:
            self._machine.start()
        except Exception:
            self._machine.stop()
            raise
        if self._pubsub_client is not None:
            self._pubsub_client.subscribe(BUTTON_PRESSED, self._button_handler)
            self._pubsub_client.subscribe(BUTTON_HELD, self._held_handler)
            self._pubsub_client.subscribe(BUTTON_RELEASED, self._released_handler)

    def stop(self) -> None:
        if self._pubsub_client is not None:
            self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._button_handler, ignore_not_found=True)
            self._pubsub_client.unsubscribe(BUTTON_HELD, self._held_handler, ignore_not_found=True)
            self._pubsub_client.unsubscribe(BUTTON_RELEASED, self._released_handler, ignore_not_found=True)
        self._machine.operate(Transitions.END)
        self._machine.stop()
        self.player.stop()

    def _button_handler(self, message: object) -> None:
        self._both_buttons.pressed(message)
        command = {
            Button.NEXT: self.cmd_next_handler,
            Button.PREV: self.cmd_prev_handler,
            Button.PLAY: self.cmd_play,
            Button.PAUSE: self.cmd_pause,
        }.get(message)  # type: ignore[call-overload]
        if command is not None:
            command()

    def _released_handler(self, message: object) -> None:
        self._both_buttons.released(message)

    def _held_handler(self, message: object) -> None:
        if message == Button.PREV and not self._both_buttons.next_is_pressed():
            self.cmd_restart_album()

    def cmd_pause(self) -> None:
        self._control("pause")
        self._machine.asyncoperate(Transitions.PAUSE)

    def cmd_play(self) -> None:
        self._control("play")
        self._machine.asyncoperate(Transitions.PLAY)

    def cmd_next_handler(self) -> None:
        """ "Next": the next track; in a pause it continues (as an album)."""
        self._control("next")
        if self.get_state() == States.PLAY:
            self.player.next()
        else:
            self._machine.asyncoperate(Transitions.PLAY)

    def cmd_prev_handler(self) -> None:
        """ "Back": Soloist goes to the start of the track or to the one before; finished: from the beginning."""
        self._control("prev")
        state = self.get_state()
        if state == States.FINISHED:
            self._machine.asyncoperate(Transitions.RESTART)
            return
        self.player.prev()
        if state == States.PAUSED:
            self._machine.asyncoperate(Transitions.PLAY)

    def cmd_restart_album(self) -> None:
        self._control("restart")
        self._machine.asyncoperate(Transitions.RESTART)

    def cmd_output_lost(self, then: Callable[[], None] | None = None) -> None:
        """The headset has gone: pause. ``then`` is called in the worker once that has been handled."""
        self._machine.asyncoperate(Transitions.OUTPUT_LOST)
        if then is not None:
            self._machine.call_soon(then)

    def cmd_output_changed(self) -> None:
        if self.get_state() == States.PLAY:
            self.player.output_changed()
        else:
            self._machine.asyncoperate(Transitions.OUTPUT_CHANGED)

    def get_info(self) -> dict[str, Any]:
        track, seconds = self.player.position()
        state = self.get_state()
        if state == States.PLAY:
            shown = self.player.state.value
        elif state == States.FINISHED:
            shown = "finished"
        elif state == States.END:
            shown = "stopped"
        else:
            shown = "paused"
        return {
            "target_id": self.entry.target_id,
            "target_type": "spotify",
            "name": self.entry.name,
            "uri": self._uri,
            "kind": self.entry.kind,
            "state": shown,
            "error": self.player.error,
            "test": self.test,
            "track": {
                "track_id": track.index,
                "uri": track.uri,
                "title": track.title,
                "pos": seconds,
                "length": track.length,
            },
        }
