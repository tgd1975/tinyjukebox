"""The driver rfid with the real rfid-reader (C++): built with a stub of the RC522 library, so the read loop and the
lines it prints are the ones of the box - only the reader hardware is replaced by a file."""

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.devices.support import driver_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.drivers.rfid import RFID
from tinyjukebox.messages import RFID_STATE, RfidState

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")

SOURCES = Path(__file__).parents[3] / "cpp" / "rfid-reader"


@pytest.fixture(scope="module")
def program(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """rfid-reader, built with ``tests/stub/MFRC522.h`` instead of the library (no hardware, no root)."""
    binary = tmp_path_factory.mktemp("rfid-reader") / "rfid-reader"
    stub = SOURCES / "tests" / "stub"
    command = ["g++", "-std=c++17", "-DRFID_READER_STUB", f"-I{stub}", str(SOURCES / "src" / "main.cpp")]
    subprocess.run([*command, "-o", str(binary)], check=True)
    return binary


class Reader:
    """The "tag on the reader": the file read by the stub."""

    def __init__(self, folder: Path) -> None:
        self.file = folder / "tag"
        self.reset = folder / "reset"  # exists: the reader has been reset and sees no tag

    def place(self, uid: str | None) -> None:
        new = self.file.with_suffix(".new")
        new.write_text("" if uid is None else uid.replace(":", " "))
        os.replace(new, self.file)  # the stub never sees a half written uid


@pytest.fixture
def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Reader:
    reader = Reader(tmp_path)
    monkeypatch.setenv("RFID_STUB_TAG", str(reader.file))
    monkeypatch.setenv("RFID_STUB_RESET", str(reader.reset))
    return reader


@pytest.fixture
def rfid(program: Path, pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> Iterator[RFID]:
    config = driver_config({"type": "rfid", "pins": {"pin_rst": 26, "pin_cs": 8}, "command": [str(program)]}, log_file)
    driver = RFID(config, pubsub, logger)
    driver.restart_interval = 0.05
    yield driver
    if not driver.is_stopped:
        driver.stop()


def states(pubsub: SyncPubSub) -> list[RfidState]:
    published = pubsub.messages(RFID_STATE)
    assert all(isinstance(state, RfidState) for state in published)
    return published  # type: ignore[return-value]


def uids(pubsub: SyncPubSub) -> list[str | None]:
    return [state.uid for state in states(pubsub)]


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def test_tags_of_the_box(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None])  # "ready"
    reader.place("04:1A:2B:3C:4D:5E:6F")
    assert wait_for(lambda: uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F"])
    reader.place(None)
    assert wait_for(lambda: uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F", None])
    reader.place("DE:AD:BE:EF")
    assert wait_for(lambda: uids(pubsub)[3:] == ["DE:AD:BE:EF"])
    assert all(state.error is None for state in states(pubsub))


def test_reset_line_is_driven_high(
    rfid: RFID, pubsub: SyncPubSub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library leaves the reset pin as an input: nothing drives the line, a dip resets the reader."""
    pins = tmp_path / "pins"
    monkeypatch.setenv("RFID_STUB_PINS", str(pins))
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None])
    # the level before the direction: an output that has not been set is low and would reset the reader
    assert pins.read_text().splitlines() == ["digitalWrite 26 1", "pinMode 26 1"]


def test_reader_that_has_been_reset_is_initialized_again(
    rfid: RFID, reader: Reader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    """Box, 2026-10-04: after a reset of the RC522 no tag was seen any more until tinyjukebox was started again."""
    reader.place("04:1A:2B:3C:4D:5E:6F")
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F"])
    reader.reset.touch()
    assert wait_for(lambda: not reader.reset.exists())
    restored = "RFID - rfid-reader: rfid-reader: the reader had been reset - initialized again"
    assert wait_for(lambda: restored in messages(caplog))
    assert uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F"]  # the tag has not been reported as removed
    reader.place(None)
    assert wait_for(lambda: uids(pubsub)[2:] == [None])
    reader.reset.touch()
    assert wait_for(lambda: not reader.reset.exists())
    reader.place("DE:AD:BE:EF")
    assert wait_for(lambda: uids(pubsub)[3:] == ["DE:AD:BE:EF"])


def test_reader_that_cannot_be_initialized_is_logged_once(
    rfid: RFID, reader: Reader, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    failure = "RFID - rfid-reader: rfid-reader: the reader has been reset and cannot be initialized - trying again"
    monkeypatch.setenv("RFID_STUB_DEAD", "1")
    reader.reset.touch()
    reader.place("0A:0B:0C:FF")
    rfid.start()
    assert wait_for(lambda: failure in messages(caplog))
    time.sleep(0.5)  # more than two polls
    assert messages(caplog).count(failure) == 1
    assert uids(pubsub) == [None]
    reader.reset.unlink()  # the reader answers again
    assert wait_for(lambda: uids(pubsub) == [None, "0A:0B:0C:FF"])


def test_tag_already_placed_at_start(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    reader.place("0A:0B:0C:FF")
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, "0A:0B:0C:FF"])


def test_stop_ends_the_program(rfid: RFID, pubsub: SyncPubSub) -> None:
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None])
    process = rfid._process
    assert process is not None
    rfid.stop()
    assert process.poll() is not None


def test_program_ends_if_nobody_reads_its_output(program: Path) -> None:
    """A reader left behind by a killed tinyjukebox would disturb the reader of the next one."""
    process = subprocess.Popen([str(program), "--pin-rst", "26", "--pin-cs", "8"], stdout=subprocess.PIPE)
    assert process.stdout is not None
    assert process.stdout.readline() == b"ready\n"
    process.stdout.close()
    assert process.wait(timeout=5) == 0


def test_failing_reader_is_logged(
    rfid: RFID, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RFID_STUB_FAIL", "1")  # the library throws if bcm2835_init fails (not root)
    rfid.start()
    assert wait_for(lambda: len(states(pubsub)) == 1)
    (state,) = states(pubsub)
    assert (state.uid, state.error) == (None, "rfid-reader has ended (exit code 1)")
    assert "RFID - rfid-reader: rfid-reader: bcm2835_init failed. Are you running as root?" in [
        record.getMessage() for record in caplog.records
    ]


@pytest.mark.parametrize(
    "arguments",
    [[], ["--pin-rst", "26"], ["--pin-rst", "x", "--pin-cs", "8"], ["--pin-rst", "26", "--pin-cs", "8", "9"]],
)
def test_usage(program: Path, arguments: list[str]) -> None:
    result = subprocess.run([str(program), *arguments], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr
