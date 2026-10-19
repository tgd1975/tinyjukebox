"""PlayRadio - plays the radio station linked to a tag (the counterpart of STMPlayAlbum for radio stations)."""

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

from tinyjukebox.albumplayer.radioplayer import RadioPlayerInterface, RadioState, StateHook
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.data.target import TargetRadio
from tinyjukebox.messages import BUTTON_PRESSED, PLAYBACK, Button, PlaybackStatus
from tinyjukebox.rgbled import WAITING, Color, RGBLed
from tinyjukebox.stm.base import OutputPause, PauseReason, State, create_machine

__all__ = ["PlayRadio", "RadioPlayerFactory", "States", "Transitions"]

RadioPlayerFactory = Callable[[logging.Logger, str, StateHook], RadioPlayerInterface]
"""(logger, url, on_state) -> radio player"""


class States(Enum):
    PLAY = "play"  # the stream plays (or connects)
    PAUSED = "paused"  # stream ended by the pause button or the loss of the headset
    RESUME = "resume"  # deciding after the headset came back: connect again or stay paused
    END = "end"


class Transitions(Enum):
    PLAY = "play"
    PAUSE = "pause"
    OUTPUT_LOST = "output_lost"
    OUTPUT_CHANGED = "output_changed"
    STAY = "stay"
    END = "end"


class _Play(State):
    def __init__(self, logger: logging.Logger, owner: "PlayRadio") -> None:
        super().__init__(States.PLAY, logger)
        self._owner = owner

    def _on_entry(self) -> Enum | None:
        self._owner.start_player()
        return None

    def _on_exit(self) -> None:
        self._owner.stop_player()


class _Paused(State):
    def __init__(self, logger: logging.Logger, owner: "PlayRadio", rgbled: RGBLed) -> None:
        super().__init__(States.PAUSED, logger)
        self._owner = owner
        self._rgbled = rgbled

    def _on_entry(self) -> Enum | None:
        self._rgbled.set_alternate_colors(Color.WHITE, Color.BLUE, 1)
        self._owner.report(PlaybackStatus.PAUSED)
        return None


class _Resume(State):
    def __init__(self, logger: logging.Logger, owner: "PlayRadio") -> None:
        super().__init__(States.RESUME, logger)
        self._owner = owner

    def _on_entry(self) -> Enum | None:
        return Transitions.PLAY if self._owner.pause.resumes_on_output() else Transitions.STAY


class _End(State):
    def __init__(self, logger: logging.Logger, owner: "PlayRadio") -> None:
        super().__init__(States.END, logger)
        self._owner = owner

    def _on_entry(self) -> Enum | None:
        self._owner.report(PlaybackStatus.STOPPED)
        return None


class PlayRadio:
    """Plays ``station`` until :meth:`stop`. LED: green, aqua, blue flowing back and forth from the start of a
    connection until the sound is heard (as while an album waits for a file: it plays as soon as it is there), blue
    while it plays, aqua while it waits to connect again.

    There is no position: pause ends the stream, play/next/prev connect again (live). Otherwise next/prev do nothing
    (radio stations are changed with the tags). A pause caused by the loss of the headset ends when it comes back
    within :data:`~tinyjukebox.stm.base.RESUME_WINDOW`; a new output while playing connects again on it.

    All commands are queued (state machine) - stopping the stream may take a moment and never blocks the caller.
    Without ``pubsub_client`` the buttons are not handled and no playback status is published.
    """

    def __init__(
        self,
        logger: logging.Logger,
        rgbled: RGBLed,
        station: TargetRadio,
        player_factory: RadioPlayerFactory,
        pubsub_client: PubSubClient | None = None,
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._rgbled = rgbled
        self._pubsub_client = pubsub_client
        self.station = station
        self._url = station.url  # the station may be changed while it plays - get_info shows what plays
        self._player_factory = player_factory
        self._player: RadioPlayerInterface | None = None
        self.pause = OutputPause()
        self._reported: PlaybackStatus | None = None

        self._machine = create_machine(self._logger)
        for state in [_Play(self._logger, self), _Paused(self._logger, self, rgbled), _Resume(self._logger, self)]:
            self._machine.addstate(state)
        self._machine.addstate(_End(self._logger, self))
        machine = self._machine
        machine.setstartstate(States.PLAY)
        machine.addtransition(States.PLAY, Transitions.PAUSE, States.PAUSED, self.pause.paused, (PauseReason.USER,))
        machine.addtransition(
            States.PLAY, Transitions.OUTPUT_LOST, States.PAUSED, self.pause.paused, (PauseReason.OUTPUT,)
        )
        machine.addtransition(States.PLAY, Transitions.OUTPUT_CHANGED, States.PLAY)  # connect again on the new output
        machine.addtransition(States.PAUSED, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.PAUSED, Transitions.OUTPUT_CHANGED, States.RESUME)
        machine.addtransition(States.RESUME, Transitions.PLAY, States.PLAY)
        machine.addtransition(States.RESUME, Transitions.STAY, States.PAUSED)
        for state_id in (States.PLAY, States.PAUSED, States.RESUME):
            machine.addtransition(state_id, Transitions.END, States.END)

    @property
    def machine(self) -> object:
        """The tantamount machine (diagram export, tests)."""
        return self._machine

    def _state_changed(self, state: RadioState) -> None:
        if state == RadioState.CONNECTING:
            self._rgbled.set_sequence(WAITING)
        elif state == RadioState.PLAYING:
            self._rgbled.set_color(Color.BLUE)
        elif state == RadioState.RETRYING:
            self._rgbled.set_color(Color.AQUA)

    def start_player(self) -> None:
        """PLAY: a new player connects to the stream (returns immediately)."""
        self._logger.info("playing radio station %s", self.station.to_text())
        player = self._player_factory(self._logger, self.station.url, self._state_changed)
        self._player = player
        self._url = self.station.url
        player.start()
        self.report(PlaybackStatus.PLAYING)

    def stop_player(self) -> None:
        player, self._player = self._player, None
        if player is not None:
            player.stop()

    def report(self, status: PlaybackStatus) -> None:
        """Publish the playback status (for the headset) if it changed."""
        if status != self._reported and self._pubsub_client is not None:
            self._reported = status
            self._pubsub_client.publish(PLAYBACK, status)

    def get_state(self) -> States:
        return self._machine.get_active_state().id  # type: ignore[no-any-return]

    def wait_until_idle(self) -> None:
        """Block until all queued commands have been processed (tests)."""
        self._machine.wait_until_idle()

    def start(self) -> None:
        """Connect to the stream. :raises Exception: if the player cannot be started (machine stopped)."""
        try:
            self._machine.start()
        except Exception:
            self._machine.stop()
            raise
        if self._pubsub_client is not None:
            self._pubsub_client.subscribe(BUTTON_PRESSED, self._button_handler)

    def stop(self) -> None:
        if self._pubsub_client is not None:
            self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._button_handler, ignore_not_found=True)
        self._machine.operate(Transitions.END)
        self._machine.stop()

    def _button_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_PRESSED` - play/pause; next/prev only end a pause."""
        if message == Button.PAUSE:
            self.cmd_pause()
        elif message in (Button.PLAY, Button.NEXT, Button.PREV):
            self._machine.asyncoperate(Transitions.PLAY)

    def cmd_pause(self) -> None:
        self._machine.asyncoperate(Transitions.PAUSE)

    def cmd_play(self) -> None:
        self._machine.asyncoperate(Transitions.PLAY)

    def cmd_next_handler(self) -> None:
        self._logger.info("next - only ends a pause for radio stations")
        self._machine.asyncoperate(Transitions.PLAY)

    def cmd_prev_handler(self) -> None:
        self._logger.info("prev - only ends a pause for radio stations")
        self._machine.asyncoperate(Transitions.PLAY)

    def cmd_restart_album(self) -> None:
        self._logger.info("restart - ignored for radio stations")

    def cmd_output_lost(self) -> None:
        self._machine.asyncoperate(Transitions.OUTPUT_LOST)

    def cmd_output_changed(self) -> None:
        self._machine.asyncoperate(Transitions.OUTPUT_CHANGED)

    def get_info(self) -> dict[str, Any]:
        player = self._player
        if self.get_state() == States.END:
            state, error = RadioState.STOPPED.value, None
        elif self.get_state() != States.PLAY:
            state, error = "paused", None
        elif player is None:
            state, error = RadioState.CONNECTING.value, None
        else:
            state, error = player.state.value, player.error
        return {
            "target_id": self.station.target_id,
            "target_type": "radio",
            "name": self.station.name,
            "url": self._url,
            "state": state,
            "error": error,
        }
