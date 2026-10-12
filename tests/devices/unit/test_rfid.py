"""RFID: the lines of rfid-reader become RfidState messages - message format as expected by Tiny Jukebox."""

import datetime
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.devices.fakes import FakeRfidReader
from tests.devices.support import RFID as RFID_CONFIG
from tests.devices.support import driver_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.drivers.rfid import RFID
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

    rfid_reader.ready()  # the next process
    rfid_reader.place(UID_7)
    assert wait_for(lambda: uids(pubsub) == [UID_7, None, UID_7])
    assert states(pubsub)[2].error is None


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
    assert uids(pubsub) == [UID_7]  # stopping is no "tag removed"


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
