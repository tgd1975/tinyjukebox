"""Common base of the state machines: a robust tantamount machine, the state base class and diagram export."""

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from tantamount.fsm2dot import GetDotNotation
from tantamount.machine import Machine
from tantamount.simplestate import SimpleState

from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child

__all__ = [
    "RESUME_WINDOW",
    "OutputPause",
    "PauseReason",
    "PeriodicTask",
    "SafeMachine",
    "State",
    "create_machine",
    "mermaid_notation",
    "write_dot_file",
]

RESUME_WINDOW = 600.0
"""Seconds within which a headset that comes back continues a playback that its disconnection has paused."""
_CALL = object()  # in the queue of the worker in place of an event: a function to call (SafeMachine.call_soon)


class SafeMachine(Machine):  # type: ignore[misc]  # tantamount has no type information
    """tantamount machine that ignores undefined events and whose async worker survives exceptions of states.

    tantamount 0.3.2 has two problems that are worked around here:

    * The constructor stores ``ignore_undefined_events`` under the wrong attribute name. Without the flag an
      unexpected event (e.g. a button press while an album is finished) raises a ``KeyError`` in the worker thread.
    * An exception in ``on_entry``/``on_exit`` of a state reached via ``asyncoperate()`` terminates the worker
      thread - afterwards the machine silently ignores all events and ``stop()`` blocks forever.
    """

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(ignore_undefined_events=True)
        self._ignore_undefined_events = True
        self._logger = logger

    def call_soon(self, function: Callable[[], object]) -> None:
        """Call ``function`` in the worker, after the events queued before it - e.g. what has to happen once a queued
        event has been handled. A machine that is stopped before calls it no more."""
        self._async_queue.put((_CALL, function, None))

    def _async_worker(self) -> None:
        while True:
            params = self._async_queue.get()
            if params is None or self._stopping:
                self._async_queue.task_done()
                break
            eventid, source_stateid, source_activation_counter = params
            try:
                if eventid is _CALL:
                    source_stateid()
                else:
                    self.operate(eventid, source_stateid, source_activation_counter)
            except Exception as error:
                self._logger.exception("_async_worker - processing event '%s' failed", eventid)
                unexpected.report(error, f"state machine, event {eventid}")
            self._async_queue.task_done()

    def wait_until_idle(self) -> None:
        """Block until all events queued with ``asyncoperate`` have been processed (tests)."""
        self._async_queue.join()


def create_machine(logger: logging.Logger) -> SafeMachine:
    """A new :class:`SafeMachine` (a tantamount machine can be started only once)."""
    return SafeMachine(logger)


class State(SimpleState):  # type: ignore[misc]  # tantamount has no type information
    """Base class of all states.

    tantamount calls ``on_entry``/``on_exit`` with the machine's operate lock held - states are never entered or
    left concurrently. ``_on_entry`` may return a transition that is processed immediately.
    """

    def __init__(self, state_id: Enum, logger: logging.Logger) -> None:
        super().__init__(state_id, None)
        self._logger = get_child(logger, type(self).__name__)

    def on_entry(self) -> Enum | None:
        self._logger.info("on_entry")
        return self._on_entry()

    def on_exit(self) -> None:
        self._logger.info("on_exit")
        self._on_exit()

    def _on_entry(self) -> Enum | None:
        return None

    def _on_exit(self) -> None:
        pass


def write_dot_file(machine: Machine, filename: str | Path) -> None:
    """Write a Graphviz representation of the machine (``dot -Tpng file.dot > file.png``)."""
    Path(filename).write_text(GetDotNotation(machine).getdotnotation(), encoding="utf-8")


def mermaid_notation(machine: Machine) -> str:
    """Mermaid ``stateDiagram-v2`` of the machine - generates the diagrams in docs/developer/architecture.md."""
    lines = ["stateDiagram-v2", f"    [*] --> {machine._start_state.id.name}"]
    for source, transitions in machine._transitions.items():
        for event, transition in transitions.items():
            label = event.name
            timeout = machine._timeoutevents.get(source)
            if timeout is not None and timeout[0] == event:
                label += " (timeout)"
            lines.append(f"    {source.name} --> {transition.targetstateid.name}: {label}")
    return "\n".join(lines)


class PeriodicTask:
    """Calls ``action`` every ``interval`` seconds in its own thread until :meth:`stop` is called."""

    def __init__(self, interval: float, action: Callable[[], object], name: str, logger: logging.Logger) -> None:
        self._interval = interval
        self._action = action
        self._logger = logger
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._action()
            except Exception as error:
                self._logger.exception("periodic task '%s' failed", self._thread.name)
                unexpected.report(error, f"periodic task {self._thread.name}")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop and wait for a running action to finish (unless called by the action itself)."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join()


class PauseReason(Enum):
    USER = "user"  # play/pause button (headset, web page, command line)
    OUTPUT = "output"  # the headset has gone (out of range, switched off, empty battery)


class OutputPause:
    """Why and since when a playback is paused - only a pause caused by the loss of the headset is continued when the
    headset comes back, and only within :data:`RESUME_WINDOW` seconds."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reason: PauseReason | None = None
        self.since = 0.0

    def paused(self, reason: PauseReason) -> None:
        self.reason = reason
        self.since = self._clock()

    def resumes_on_output(self) -> bool:
        """True if the headset that has come back continues the playback."""
        return self.reason is PauseReason.OUTPUT and self._clock() - self.since <= RESUME_WINDOW
