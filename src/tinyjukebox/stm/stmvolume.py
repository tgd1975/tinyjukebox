"""STMVolume - the state machine of the volume buttons."""

import logging
from enum import Enum
from pathlib import Path
from typing import Protocol

from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.latency import ReactionTimer
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, Button
from tinyjukebox.stm.base import State, create_machine, write_dot_file

__all__ = ["STMVolume", "States", "Transitions", "VolumeButtons"]


class VolumeButtons(Protocol):
    """What the buttons change: a :class:`~tinyjukebox.volumemanager.VolumeManager` or - speaker and headset - the
    :class:`~tinyjukebox.volumemanager.Volumes`."""

    def update_volume(self) -> None: ...

    def volume_up(self, held: bool = False) -> None: ...

    def volume_down(self, held: bool = False) -> None: ...

    def volume_off(self) -> None: ...


class States(Enum):
    IDLE = "idle"
    INIT = "init"
    VOLUME_UP = "up"
    VOLUME_DOWN = "down"
    VOLUME_UP_HELD = "up_held"  # the small step of a button that is kept pressed
    VOLUME_DOWN_HELD = "down_held"
    END = "end"


class Transitions(Enum):
    START = "start"
    TO_IDLE = "to_idle"
    TO_VOLUME_UP = "to_volume_up"
    TO_VOLUME_DOWN = "to_volume_down"
    TO_VOLUME_UP_HELD = "to_volume_up_held"
    TO_VOLUME_DOWN_HELD = "to_volume_down_held"
    END = "to_end"


_TRANSITIONS: dict[str, dict[object, Transitions]] = {
    BUTTON_PRESSED: {
        Button.VOLUME_UP: Transitions.TO_VOLUME_UP,
        Button.VOLUME_DOWN: Transitions.TO_VOLUME_DOWN,
    },
    BUTTON_HELD: {
        Button.VOLUME_UP: Transitions.TO_VOLUME_UP_HELD,
        Button.VOLUME_DOWN: Transitions.TO_VOLUME_DOWN_HELD,
    },
}


class Idle(State):
    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(States.IDLE, logger)


class End(State):
    def __init__(self, logger: logging.Logger, volumemanager: VolumeButtons) -> None:
        super().__init__(States.END, logger)
        self._volumemanager = volumemanager

    def _on_entry(self) -> Enum | None:
        self._volumemanager.volume_off()  # mute on shutdown
        return None


class Init(State):
    def __init__(self, logger: logging.Logger, volumemanager: VolumeButtons) -> None:
        super().__init__(States.INIT, logger)
        self._volumemanager = volumemanager

    def _on_exit(self) -> None:
        self._volumemanager.update_volume()


class _VolumeStep(State):
    """One step of the volume - ``held``: the small step of a button that is kept pressed.

    The machine goes back to ``IDLE`` in any case. Only there it takes the next press: a step that failed (an
    exception while the mixer is set) would otherwise leave the volume buttons without effect until the next start.
    The exception is logged and reported as any unexpected one (:mod:`tinyjukebox.core.unexpected`)."""

    def __init__(
        self,
        state_id: States,
        logger: logging.Logger,
        volumemanager: VolumeButtons,
        latency: ReactionTimer,
        held: bool,
    ) -> None:
        super().__init__(state_id, logger)
        self._volumemanager = volumemanager
        self._latency = latency
        self._held = held

    def _step(self) -> None:
        raise NotImplementedError

    def _on_entry(self) -> Enum | None:
        try:
            self._step()
        except Exception as error:
            self._logger.exception("_on_entry - the volume could not be set")
            unexpected.report(error, f"volume button ({self.id.value})")
        else:
            self._latency.end("volume set")
        return Transitions.TO_IDLE


class VolumeUp(_VolumeStep):
    def __init__(
        self, logger: logging.Logger, volumemanager: VolumeButtons, latency: ReactionTimer, held: bool = False
    ) -> None:
        super().__init__(States.VOLUME_UP_HELD if held else States.VOLUME_UP, logger, volumemanager, latency, held)

    def _step(self) -> None:
        self._volumemanager.volume_up(self._held)


class VolumeDown(_VolumeStep):
    def __init__(
        self, logger: logging.Logger, volumemanager: VolumeButtons, latency: ReactionTimer, held: bool = False
    ) -> None:
        super().__init__(States.VOLUME_DOWN_HELD if held else States.VOLUME_DOWN, logger, volumemanager, latency, held)

    def _step(self) -> None:
        self._volumemanager.volume_down(self._held)


class STMVolume:
    """Changes the volume on the volume buttons: one step per press (:data:`~tinyjukebox.messages.BUTTON_PRESSED`), a
    small step per hold event (:data:`~tinyjukebox.messages.BUTTON_HELD` - the input drivers of the volume buttons
    repeat it while the button is kept pressed). The handlers only queue the event - setting the mixer runs in the
    machine's thread, not in the pubsub dispatch thread."""

    def __init__(
        self,
        pubsub_client: PubSubClient,
        volumemanager: VolumeButtons,
        logger: logging.Logger,
        latency: ReactionTimer | None = None,
    ) -> None:
        """:param latency: measures button pressed → volume set (default: a timer of its own)."""
        self._logger = get_child(logger, type(self).__name__)
        self._pubsub_client = pubsub_client
        self._latency = latency or ReactionTimer("volume button", self._logger)
        self._machine = create_machine(self._logger)
        for state in [
            Init(self._logger, volumemanager),
            Idle(self._logger),
            VolumeUp(self._logger, volumemanager, self._latency),
            VolumeDown(self._logger, volumemanager, self._latency),
            VolumeUp(self._logger, volumemanager, self._latency, held=True),
            VolumeDown(self._logger, volumemanager, self._latency, held=True),
            End(self._logger, volumemanager),
        ]:
            self._machine.addstate(state)
        self._machine.setstartstate(States.INIT)
        self._machine.addtransition(States.INIT, Transitions.START, States.IDLE)
        self._machine.addtransition(States.IDLE, Transitions.TO_VOLUME_UP, States.VOLUME_UP)
        self._machine.addtransition(States.IDLE, Transitions.TO_VOLUME_DOWN, States.VOLUME_DOWN)
        self._machine.addtransition(States.IDLE, Transitions.TO_VOLUME_UP_HELD, States.VOLUME_UP_HELD)
        self._machine.addtransition(States.IDLE, Transitions.TO_VOLUME_DOWN_HELD, States.VOLUME_DOWN_HELD)
        changes = (States.VOLUME_UP, States.VOLUME_DOWN, States.VOLUME_UP_HELD, States.VOLUME_DOWN_HELD)
        for state_id in changes:
            self._machine.addtransition(state_id, Transitions.TO_IDLE, States.IDLE)
        for state_id in (States.IDLE, *changes):
            self._machine.addtransition(state_id, Transitions.END, States.END)

    @property
    def machine(self) -> object:
        """The tantamount machine (diagram export, tests)."""
        return self._machine

    def dump_to_dot(self, filename: str | Path) -> None:
        write_dot_file(self._machine, filename)

    def wait_until_idle(self) -> None:
        """Block until all queued button events have been processed (tests)."""
        self._machine.wait_until_idle()

    def _handle(self, topic: str, message: object) -> None:
        transition = _TRANSITIONS[topic].get(message)
        if transition is None:
            return
        self._logger.info("_handle - received: %s (%s)", message, topic)
        self._latency.begin(self._pubsub_client.published_at())
        self._latency.step("handler")
        self._machine.asyncoperate(transition)

    def _button_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_PRESSED` - reacts on the volume buttons only."""
        self._handle(BUTTON_PRESSED, message)

    def _held_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.BUTTON_HELD` - reacts on the volume buttons only."""
        self._handle(BUTTON_HELD, message)

    def start(self) -> None:
        self._machine.start()
        self._machine.operate(Transitions.START)
        self._pubsub_client.subscribe(BUTTON_PRESSED, self._button_handler)
        self._pubsub_client.subscribe(BUTTON_HELD, self._held_handler)

    def stop(self) -> None:
        self._pubsub_client.unsubscribe(BUTTON_PRESSED, self._button_handler, ignore_not_found=True)
        self._pubsub_client.unsubscribe(BUTTON_HELD, self._held_handler, ignore_not_found=True)
        if self._machine._startdone:  # not started if the start of the box failed before
            self._machine.operate(Transitions.END)
            self._machine.stop()
