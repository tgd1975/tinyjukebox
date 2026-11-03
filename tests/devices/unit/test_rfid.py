"""RFID: the lines of rfid-reader become RfidState messages - message format as expected by Tiny Jukebox."""

import contextlib
import datetime
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.devices.fakes import FakeRfidReader
from tests.devices.support import RFID as RFID_CONFIG
from tests.devices.support import driver_config, load_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.core.config import validate_config
from tinyjukebox.core.pubsub import NotConnectedError
from tinyjukebox.core.schema.microservice import get_schema as microservice_schema
from tinyjukebox.devices.drivers.rfid import RFID
from tinyjukebox.devices.schema.drivermanager import get_driver_schema
from tinyjukebox.messages import RFID_POLL, RFID_STATE, RfidState

Factory = Callable[..., RFID]

UID_7 = "04:1A:2B:3C:4D:5E:6F"  # a tag of the box
UID_4 = "DE:AD:BE:EF"


@pytest.fixture
def create(pubsub: SyncPubSub, logger: logging.Logger, log_file: str, rfid_reader: FakeRfidReader) -> Iterator[Factory]:
    drivers: list[RFID] = []

    def factory(**driver: Any) -> RFID:
        config = driver_config({**RFID_CONFIG, "command": rfid_reader.command, **driver}, log_file)
        rfid = RFID(config, pubsub, logger)
        rfid.restart_interval = 0.05
        drivers.append(rfid)
        return rfid

    yield factory
    for rfid in drivers:
        if not rfid.is_stopped:
            rfid.stop()


def states(pubsub: SyncPubSub) -> list[RfidState]:
    published = pubsub.messages(RFID_STATE)
    assert all(isinstance(state, RfidState) for state in published)
    return published  # type: ignore[return-value]


def uids(pubsub: SyncPubSub) -> list[str | None]:
    return [state.uid for state in states(pubsub)]


def messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level and r.getMessage().startswith("RFID - ")]


def test_default_command_and_pins(create: Factory, pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    config = driver_config({"type": "rfid", "pins": {"pin_rst": 26, "pin_cs": 8}}, log_file)
    rfid = RFID(config, pubsub, logger)
    assert rfid._command == ["sudo", "-n", "/usr/local/bin/rfid-reader", "--pin-rst", "26", "--pin-cs", "8"]


def test_pins_are_passed_to_the_reader(
    create: Factory, rfid_reader: FakeRfidReader, caplog: pytest.LogCaptureFixture
) -> None:
    create(pins={"pin_rst": 1, "pin_cs": 3}).start()
    rfid_reader.write("!args")
    assert wait_for(lambda: "RFID - rfid-reader: --pin-rst 1 --pin-cs 3" in messages(caplog, logging.WARNING))


@pytest.mark.parametrize(
    "pins",
    [
        {"pin_rst": 8, "pin_cs": 8},
        {"pin_rst": 26, "pin_cs": 10},
        {"pin_rst": 9, "pin_cs": 8},
        {"pin_rst": 11, "pin_cs": 7},
    ],
)
def test_wrong_pins_are_rejected(create: Factory, pins: dict[str, int]) -> None:
    """Two different pins; not MISO, MOSI, SCLK of SPI0 - rfid-reader would make them outputs and SPI would not work."""
    with pytest.raises(ValueError, match=r"^rfid: "):
        create(pins=pins)


@pytest.mark.parametrize(
    ("pin", "value", "valid"),
    [("pin_cs", 7, True), ("pin_cs", 25, True), ("pin_cs", 9, False), ("pin_cs", 10, False), ("pin_rst", 11, False)],
)
def test_schema_rejects_the_pins_of_spi(pin: str, value: int, valid: bool) -> None:
    """CS is driven by rfid-reader itself (the library switches off the chip select of the SPI hardware): any free
    pin will do."""
    config = load_config("config_rfid.yaml")
    config["driver"]["pins"][pin] = value
    assert (validate_config(config, microservice_schema(get_driver_schema("rfid"))) is None) == valid


def test_tag_placed_and_removed(create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub) -> None:
    create().start()
    rfid_reader.ready()
    rfid_reader.place(UID_7)
    rfid_reader.remove()
    rfid_reader.place(UID_4)
    assert wait_for(lambda: len(states(pubsub)) == 4)

    published = states(pubsub)
    assert [(s.uid, s.present, s.error) for s in published] == [
        (None, False, None),
        (UID_7, True, None),
        (None, False, None),
        (UID_4, True, None),
    ]
    assert abs(published[1].timestamp - datetime.datetime.now(datetime.UTC)).total_seconds() < 60


def test_tag_replaced_without_removal(create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub) -> None:
    create().start()
    rfid_reader.place(UID_7)
    rfid_reader.place(UID_4)
    assert wait_for(lambda: uids(pubsub) == [UID_7, UID_4])


def test_unchanged_state_is_published_once(create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub) -> None:
    create().start()
    rfid_reader.ready()
    rfid_reader.remove()
    rfid_reader.place(UID_7)
    rfid_reader.place(UID_7)
    rfid_reader.remove()
    assert wait_for(lambda: uids(pubsub) == [None, UID_7, None])


def test_alive_is_neither_published_nor_logged(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    create().start()
    rfid_reader.write("alive")
    rfid_reader.ready()
    assert wait_for(lambda: uids(pubsub) == [None])
    assert messages(caplog, logging.WARNING) == []


def test_poll_request_publishes_the_current_state(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub
) -> None:
    create().start()
    pubsub.publish(RFID_POLL, None)  # the reader has not reported anything yet
    (state,) = states(pubsub)
    assert (state.uid, state.error) == (None, "rfid-reader has not been started")

    rfid_reader.place(UID_7)
    assert wait_for(lambda: len(states(pubsub)) == 2)
    pubsub.publish(RFID_POLL, None)
    assert uids(pubsub) == [None, UID_7, UID_7]
    assert states(pubsub)[2].timestamp > states(pubsub)[1].timestamp


@pytest.mark.parametrize(
    "line", ["bcm2835_init failed", "tag", "tag 4:86", "tag 04:86:zz", "tag de:ad:be:ef", "TAG 01"]
)
def test_other_lines_are_logged_and_not_published(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture, line: str
) -> None:
    create().start()
    rfid_reader.write(line)
    rfid_reader.write("")
    assert wait_for(lambda: f"RFID - rfid-reader: {line}" in messages(caplog, logging.WARNING))
    assert states(pubsub) == []
    assert len(messages(caplog, logging.WARNING)) == 1  # the empty line is ignored


def test_ended_reader_is_no_tag_and_started_again(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    create().start()
    rfid_reader.place(UID_7)
    rfid_reader.write("!exit 3")
    assert wait_for(lambda: len(states(pubsub)) == 2)
    assert uids(pubsub) == [UID_7, None]
    assert states(pubsub)[1].error == "rfid-reader has ended (exit code 3)"
    (error,) = messages(caplog, logging.ERROR)
    assert error.startswith("RFID - rfid-reader has ended (exit code 3) (")
    assert error.endswith("- trying again every 0.05 s")

    rfid_reader.ready()  # the next process: the error has gone - that is published too
    rfid_reader.place(UID_7)
    assert wait_for(lambda: len(states(pubsub)) == 4)
    assert [(s.uid, s.error) for s in states(pubsub)[2:]] == [(None, None), (UID_7, None)]


def test_the_same_reason_is_logged_again_after_the_reader_has_worked(
    create: Factory, rfid_reader: FakeRfidReader, caplog: pytest.LogCaptureFixture
) -> None:
    create().start()
    rfid_reader.write("!exit 3")
    assert wait_for(lambda: len(messages(caplog, logging.ERROR)) == 1)
    rfid_reader.write("!exit 3")  # at once again: logged once only
    assert wait_for(lambda: len(messages(caplog, logging.DEBUG)) >= 1)
    rfid_reader.ready()  # it works - the same end later is news
    rfid_reader.write("!exit 3")
    assert wait_for(lambda: len(messages(caplog, logging.ERROR)) == 2)


def test_reader_that_cannot_be_started(
    create: Factory, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture, tmp_path: Any
) -> None:
    caplog.set_level(logging.DEBUG)
    missing = str(tmp_path / "rfid-reader")
    create(command=[missing]).start()
    assert wait_for(lambda: len(messages(caplog, logging.DEBUG)) >= 2)  # tried again and again
    (state,) = states(pubsub)
    assert state.uid is None
    assert (state.error or "").startswith(f"cannot start {missing}: ")
    assert len(messages(caplog, logging.ERROR)) == 1  # the same reason is reported once


def test_stop_ends_the_reader(create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub) -> None:
    rfid = create()
    rfid.start()
    rfid_reader.place(UID_7)
    assert wait_for(lambda: uids(pubsub) == [UID_7])
    rfid.stop()
    assert not pubsub.subscribed(RFID_POLL)
    assert wait_for(lambda: not rfid_reader.running())
    assert rfid._process is None
    assert rfid._thread is None
    assert uids(pubsub) == [UID_7]  # stopping is no "tag removed"


def test_stop_does_not_wait_for_a_reader_left_behind(
    create: Factory, rfid_reader: FakeRfidReader, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """sudo passes SIGTERM on, SIGKILL reaches only sudo: rfid-reader (root) may stay behind with the pipe. The thread
    ends nevertheless and closes its end - and rfid-reader ends by itself, as nobody reads its output any more."""
    rfid = create()
    rfid.stop_timeout = 0.1
    rfid.start()
    gone = tmp_path / "gone"
    rfid_reader.write(f"!left-behind {gone}")
    pid_file = tmp_path / "gone.pid"
    pid = int(wait_for(lambda: pid_file.exists() and pid_file.read_text()))
    try:
        rfid.stop()
        assert rfid._thread is None  # it has ended
        assert wait_for(gone.exists)  # and so has the reader left behind
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_no_second_reader_while_the_last_one_ends(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread that does not end in time is kept: the next start waits for it - two readers disturb each other."""
    rfid = create()
    rfid.stop_timeout = 0.05
    release = threading.Event()
    end = rfid._end

    def slow_end(process: Any, ended: bool) -> int:
        release.wait(5)
        return end(process, ended)

    monkeypatch.setattr(rfid, "_end", slow_end)
    rfid.start()
    rfid_reader.ready()
    assert wait_for(lambda: uids(pubsub) == [None])
    first = rfid._process
    rfid.stop()
    thread = rfid._thread
    assert thread is not None
    assert thread.is_alive()
    rfid.start()
    time.sleep(0.3)
    assert rfid._process is first  # the last reader is still being ended, no new one has been started
    release.set()
    assert wait_for(lambda: rfid._process is not None and rfid._process is not first)
    assert not thread.is_alive()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_error_in_the_thread_ends_the_reader(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.g. a publication after the queue was disconnected: rfid-reader is ended (no zombie), healthy() is false."""
    rfid = create()
    rfid.start()
    rfid_reader.ready()
    assert wait_for(lambda: uids(pubsub) == [None] and rfid.healthy())
    process = rfid._process
    assert process is not None

    def disconnected(topic: str, message: object) -> None:
        raise NotConnectedError("publish - trying to publish while not being connected.")

    monkeypatch.setattr(pubsub, "publish", disconnected)
    rfid_reader.place(UID_7)
    assert wait_for(lambda: not rfid.healthy())
    assert wait_for(lambda: process.returncode is not None)  # ended and collected
    assert rfid._process is None
    assert not rfid.state()["reader"]


def test_stuck_reader_is_ended_and_started_again(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    """A reader that hangs (in SPI, or stopped) cannot tell: once it has written "alive", three silent intervals end
    it - and it is started again as after a crash."""
    rfid = create()
    rfid.alive_interval = 0.1
    rfid.start()
    rfid_reader.write("alive")
    rfid_reader.place(UID_7)
    assert wait_for(lambda: len(states(pubsub)) == 2)  # and then nothing more
    stuck = "rfid-reader is stuck: no output for 0.3 s"
    assert [(s.uid, s.error) for s in states(pubsub)] == [(UID_7, None), (None, stuck)]
    (error,) = messages(caplog, logging.ERROR)
    assert error.startswith(f"RFID - {stuck} (")

    rfid_reader.ready()  # the next process
    rfid_reader.place(UID_7)
    assert wait_for(lambda: uids(pubsub)[2:] == [None, UID_7])


def test_any_line_keeps_the_reader(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    rfid = create()
    rfid.alive_interval = 0.2
    rfid.start()
    rfid_reader.write("alive")
    for _ in range(8):  # 0.8 s, more than three intervals - but never 0.6 s without a line
        rfid_reader.place(UID_7)
        time.sleep(0.1)
    assert messages(caplog, logging.ERROR) == []
    assert uids(pubsub) == [UID_7]


def test_reader_that_never_writes_alive_is_never_ended(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    """Another reader program that does not know "alive" keeps working."""
    rfid = create()
    rfid.alive_interval = 0.05
    rfid.start()
    rfid_reader.ready()
    assert wait_for(lambda: uids(pubsub) == [None])
    time.sleep(0.5)  # ten silent intervals
    assert messages(caplog, logging.ERROR) == []
    assert rfid.healthy()


def test_started_again_publishes_the_state_again(
    create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub
) -> None:
    rfid = create()
    rfid.start()
    rfid_reader.place(UID_7)
    assert wait_for(lambda: uids(pubsub) == [UID_7])
    rfid.stop()
    rfid.start()
    rfid_reader.place(UID_7)
    assert wait_for(lambda: uids(pubsub) == [UID_7, UID_7])


def test_state_tells_the_tag_and_the_reader(create: Factory, rfid_reader: FakeRfidReader, pubsub: SyncPubSub) -> None:
    """The technical view of the web page: the uid, whether rfid-reader runs, and why it does not."""
    rfid = create()
    state = rfid.state()
    assert state == {
        "name": "rfid", "type": "rfid", "running": False, "pins": {"pin_rst": 26, "pin_cs": 8}, "reader": False,
        "uid": None, "error": "rfid-reader has not been started", "changed_at": None,
    }  # fmt: skip
    rfid.start()
    rfid_reader.ready()
    assert wait_for(lambda: rfid.state()["error"] is None)
    state = rfid.state()
    assert (state["running"], state["reader"], state["uid"]) == (True, True, None)
    ready_at = state["changed_at"]
    rfid_reader.place(UID_7)
    assert wait_for(lambda: rfid.state()["uid"] == UID_7)
    assert rfid.state()["changed_at"] >= ready_at
    rfid_reader.end()
    assert wait_for(lambda: rfid.state()["error"] is not None)
    state = rfid.state()
    assert state["uid"] is None
    assert state["error"].startswith("rfid-reader has ended")
