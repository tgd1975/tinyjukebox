"""Common parts of the state machines: diagram export (dot, mermaid) and PeriodicTask."""

import functools
import logging
import threading
import time
from enum import Enum
from typing import Any

import pytest

from tests.support import TAG_ALBUM, wait_for
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.core import unexpected
from tinyjukebox.stm.base import PeriodicTask, State, create_machine, mermaid_notation, write_dot_file
from tinyjukebox.stm.stmoperation import STMOperation
from tinyjukebox.stm.stmplayalbum import STMPlayAlbum
from tinyjukebox.stm.stmvolume import STMVolume
from tinyjukebox.volumemanager import VirtualVolumeControl, VolumeManager

# --- diagram export ---------------------------------------------------------------------------------------------------


def _no_album(target: Any, activity: Any) -> Any:
    raise AssertionError("no album is started in the diagram tests")


@pytest.fixture
def stmoperation(library, pubsub, rgbled, logger):
    return STMOperation(pubsub, logger, rgbled,
                        library.tags, library.targets, library.activities, _no_album)  # fmt: skip


@pytest.fixture
def stmplayalbum(library, pubsub, rgbled, logger, clock):
    config = {"rewind-max-time": 5, "update-schedule": 0.05, "pause-between-tracks": 2}
    return STMPlayAlbum(config, pubsub, logger, rgbled,
                        library.targets.get_target("1"), library.activities.get_activity(TAG_ALBUM),
                        functools.partial(DummyPlayer, clock=clock))  # fmt: skip


@pytest.fixture
def stmvolume(pubsub, logger):
    config = {"max-volume": 80, "default-volume": 50, "volume-step": 5}
    return STMVolume(pubsub, VolumeManager(VirtualVolumeControl(logger), config,
                                                                            logger), logger)  # fmt: skip


@pytest.fixture(params=["stmoperation", "stmplayalbum", "stmvolume"])
def stm(request):
    return request.getfixturevalue(request.param)


def transitions(machine):
    """(source, event, target) names of all transitions of a tantamount machine."""
    return [
        (source.name, event.name, transition.targetstateid.name)
        for source, events in machine._transitions.items()
        for event, transition in events.items()
    ]


def test_write_dot_file(stm, tmp_path):
    filename = tmp_path / "diagram.dot"
    write_dot_file(stm.machine, filename)
    dot = filename.read_text(encoding="utf-8")
    assert "digraph" in dot
    for source, _, target in transitions(stm.machine):
        assert source in dot
        assert target in dot


def test_dump_to_dot(stm, tmp_path):
    filename = str(tmp_path / "diagram.dot")
    stm.dump_to_dot(filename)
    assert "digraph" in (tmp_path / "diagram.dot").read_text(encoding="utf-8")


def test_mermaid_contains_every_transition(stm):
    lines = mermaid_notation(stm.machine).splitlines()
    assert lines[0] == "stateDiagram-v2"
    assert lines[1] == f"    [*] --> {stm.machine._start_state.id.name}"
    expected = transitions(stm.machine)
    assert len(expected) > 5
    assert len(lines) == 2 + len(expected)
    for source, event, target in expected:
        assert (
            f"    {source} --> {target}: {event}" in lines or f"    {source} --> {target}: {event} (timeout)" in lines
        )


def test_mermaid_labels_timeout(stmplayalbum, stmoperation, stmvolume):
    lines = mermaid_notation(stmplayalbum.machine).splitlines()
    assert lines[1] == "    [*] --> INIT"
    assert "    DELAY --> TONEXT: NEXT (timeout)" in lines  # the pause between two tracks
    assert "    DELAY --> TOPREV: PREV" in lines
    assert "    PLAY --> DELAY: DELAY" in lines
    assert [line for line in lines if "(timeout)" in line] == ["    DELAY --> TONEXT: NEXT (timeout)"]
    timeouts = [line for line in mermaid_notation(stmoperation.machine).splitlines() if "(timeout)" in line]
    assert timeouts == [
        "    WAIT_FOR_TAG --> IDLE_LONG: IDLE_TIMEOUT (timeout)",  # no tag for a long time: the led blinks
        "    TEST_RADIO --> WAIT_FOR_TAG: TEST_END (timeout)",  # the radio test ends by itself
    ]
    assert "(timeout)" not in mermaid_notation(stmvolume.machine)
    assert "    [*] --> WAIT_FOR_TAG" in mermaid_notation(stmoperation.machine)
    assert "    IDLE --> VOLUME_UP: TO_VOLUME_UP" in mermaid_notation(stmvolume.machine)


# --- PeriodicTask -----------------------------------------------------------------------------------------------------


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def task_threads(name):
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_periodic_task_runs_until_stopped(logger):
    counter = Counter()
    task = PeriodicTask(0.01, counter, "test-periodic", logger)
    assert counter.count == 0
    task.start()
    assert wait_for(lambda: counter.count >= 3)
    task.stop()
    assert not task_threads("test-periodic")
    count = counter.count
    time.sleep(0.05)
    assert counter.count == count


def test_periodic_task_stop_waits_for_running_action(logger):
    running, finished = threading.Event(), threading.Event()

    def action():
        running.set()
        time.sleep(0.1)
        finished.set()

    task = PeriodicTask(0.01, action, "test-periodic-slow", logger)
    task.start()
    assert running.wait(3)
    task.stop()
    assert finished.is_set()
    assert not task_threads("test-periodic-slow")


def test_periodic_task_stop_by_its_own_action(logger):
    stopped = threading.Event()

    def action():
        task.stop()  # must not join its own thread
        stopped.set()

    task = PeriodicTask(0.01, action, "test-periodic-self", logger)
    task.start()
    assert stopped.wait(3)
    assert wait_for(lambda: not task_threads("test-periodic-self"))
    task.stop()  # stopping again is fine


def test_periodic_task_survives_exceptions(logger, caplog):
    counter = Counter()

    def action():
        counter()
        if counter.count == 1:
            raise RuntimeError("first call fails")

    task = PeriodicTask(0.01, action, "test-periodic-broken", logger)
    with caplog.at_level(logging.ERROR):
        task.start()
        assert wait_for(lambda: counter.count >= 3)
        task.stop()
    assert "periodic task 'test-periodic-broken' failed" in caplog.text
    assert "first call fails" in caplog.text
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


class _S(Enum):
    A = "a"
    B = "b"


class _T(Enum):
    GO = "go"


class _Entered(State):
    def __init__(self, state_id, logger, entered):
        super().__init__(state_id, logger)
        self._entered = entered

    def _on_entry(self):
        self._entered.append(self.id)


def test_a_call_runs_in_the_worker_after_the_events_before_it(logger):
    entered: list[object] = []
    calls: list[object] = []
    reported: list[str] = []
    machine = create_machine(logger)
    machine.addstate(_Entered(_S.A, logger, entered))
    machine.addstate(_Entered(_S.B, logger, entered))
    machine.setstartstate(_S.A)
    machine.addtransition(_S.A, _T.GO, _S.B)
    machine.asyncstart()

    def report(error, where):
        reported.append(where)

    unexpected.subscribe(report)
    try:
        machine.asyncoperate(_T.GO)
        machine.call_soon(lambda: calls.append((list(entered), threading.current_thread() is machine._async_thread)))
        machine.call_soon(lambda: 1 / 0)  # contained like an exception of a state
        machine.call_soon(lambda: calls.append("still working"))
        machine.wait_until_idle()
    finally:
        unexpected.unsubscribe(report)
        machine.stop()
    assert calls == [([_S.A, _S.B], True), "still working"]
    assert len(reported) == 1
    assert reported[0].startswith("state machine")
