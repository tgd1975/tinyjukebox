"""The driver rfid with the real rfid-reader (C++): built with a stub of the RC522 library, so the read loop and the
lines it prints are the ones of the box - only the reader hardware is replaced by files (tests/stub/MFRC522.h)."""

import logging
import os
import re
import shutil
import signal
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
STUB = SOURCES / "tests" / "stub"

TAG_A = "04:1A:2B:3C:4D:5E:6F"  # a tag of the box
TAG_B = "04:9C:2B:3C:4D:5E:6F"


def makefile_flags() -> list[str]:
    """The compiler flags of the Makefile: the default of CXXFLAGS, and WARNINGS."""
    text = (SOURCES / "Makefile").read_text()
    flags = dict(re.findall(r"^(CXXFLAGS|WARNINGS) \??= *(.*)$", text, re.MULTILINE))
    assert set(flags) == {"CXXFLAGS", "WARNINGS"}
    return [*flags["CXXFLAGS"].split(), *flags["WARNINGS"].split()]


def compile_with_stub(source: Path, binary: Path) -> subprocess.CompletedProcess[str]:
    """Built like the Makefile does it, warnings are errors - against the stub instead of the library."""
    command = ["g++", "-std=c++17", *makefile_flags(), "-Werror", "-DRFID_READER_STUB", f"-I{STUB}", str(source)]
    return subprocess.run([*command, "-o", str(binary)], capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def program(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """rfid-reader, built with ``tests/stub/MFRC522.h`` instead of the library (no hardware, no root)."""
    binary = tmp_path_factory.mktemp("rfid-reader") / "rfid-reader"
    result = compile_with_stub(SOURCES / "src" / "main.cpp", binary)
    assert result.returncode == 0, result.stderr
    return binary


class Reader:
    """The reader of the stub: the tags on it, what happens to it next, what it sent to the tags."""

    def __init__(self, folder: Path) -> None:
        self.file = folder / "tag"
        self.reset = folder / "reset"  # exists: the reader has been reset and sees no tag
        self.events = folder / "events"  # used up by the stub: miss, miss@UID, garble, noselect, reset
        self.log = folder / "log"  # WUPA ..., SELECT ..., HLTA, INIT

    def place(self, *uids: str | None) -> None:
        """These tags are on the reader now - several: stacked; none: no tag."""
        new = self.file.with_suffix(".new")
        new.write_text("\n".join(uid.replace(":", " ") for uid in uids if uid is not None))
        os.replace(new, self.file)  # the stub never sees a half written file

    def happen(self, *events: str) -> None:
        """These things happen to the reader next - returns when the stub has used up all of them."""
        assert not self.events.exists()
        new = self.events.with_suffix(".new")
        new.write_text(" ".join(events))
        os.replace(new, self.events)
        assert wait_for(lambda: not self.events.exists())

    def sent(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def looked(self, times: int = 2) -> None:
        """Wait until a tag has answered ``times`` more times: what rfid-reader decided before has been published."""
        answers = self.sent().count("WUPA answer")
        assert wait_for(lambda: self.sent().count("WUPA answer") >= answers + times)


@pytest.fixture
def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Reader:
    reader = Reader(tmp_path)
    monkeypatch.setenv("RFID_STUB_TAG", str(reader.file))
    monkeypatch.setenv("RFID_STUB_RESET", str(reader.reset))
    monkeypatch.setenv("RFID_STUB_EVENTS", str(reader.events))
    monkeypatch.setenv("RFID_STUB_LOG", str(reader.log))
    return reader


def create(program: Path, pubsub: SyncPubSub, logger: logging.Logger, log_file: str, *options: str) -> RFID:
    config = {"type": "rfid", "pins": {"pin_rst": 26, "pin_cs": 8}, "command": [str(program), *options]}
    driver = RFID(driver_config(config, log_file), pubsub, logger)
    driver.restart_interval = 0.05
    return driver


@pytest.fixture
def rfid(program: Path, pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> Iterator[RFID]:
    driver = create(program, pubsub, logger, log_file)  # without --alive: the default, as on the box
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


def test_tags_of_the_box(rfid: RFID, reader: Reader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture) -> None:
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None])  # "ready"
    reader.place("04:1A:2B:3C:4D:5E:6F")
    assert wait_for(lambda: uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F"])
    reader.place(None)
    assert wait_for(lambda: uids(pubsub) == [None, "04:1A:2B:3C:4D:5E:6F", None])
    reader.place("DE:AD:BE:EF")
    assert wait_for(lambda: uids(pubsub)[3:] == ["DE:AD:BE:EF"])
    assert all(state.error is None for state in states(pubsub))
    assert not [message for message in messages(caplog) if message.startswith("RFID - rfid-reader:")]  # nor "alive"


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


def test_a_single_miss_changes_nothing(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    """A tag counts as removed only if it does not answer a second time either."""
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.happen("miss")
    reader.looked()
    assert uids(pubsub) == [None, TAG_A]


def test_two_misses_are_a_removal(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.happen("miss", "miss")
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A, None, TAG_A])  # the tag is still there: placed again


def test_tag_whose_answer_was_garbled_is_halted_before_it_is_checked_again(
    rfid: RFID, reader: Reader, pubsub: SyncPubSub
) -> None:
    """A garbled answer to WUPA leaves the tag READY - and a READY tag ignores the next WUPA (ISO 14443-3). Without the
    HLTA in between the second check fails as well: the tag would be removed and placed again."""
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.happen("garble")
    reader.looked()
    assert uids(pubsub) == [None, TAG_A]
    sent = reader.sent()
    garbled = sent.index("WUPA garbled")
    assert sent[garbled + 1] == "HLTA"


def test_reset_while_waiting_is_no_removal(
    rfid: RFID, reader: Reader, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
) -> None:
    """The reader is reset after the first check has missed, while rfid-reader waits for the second one: it is
    initialized again before the second check, and a check around a reset never makes a removed tag."""
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.happen("miss", "reset")
    assert wait_for(lambda: not reader.reset.exists())  # initialized again
    reader.looked()
    assert uids(pubsub) == [None, TAG_A]
    assert "RFID - rfid-reader: rfid-reader: the reader had been reset - initialized again" in messages(caplog)


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


def test_quick_swap_is_reported(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    """One tag taken away and another one placed between two checks: no "removed" - but the new tag."""
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.place(TAG_B)
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A, TAG_B])


def test_stacked_tags_the_one_left_is_reported(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    """Of two tags the select picks one, always the same. If that one is taken away, the other one is reported."""
    reader.place(TAG_A, TAG_B)
    rfid.start()
    assert wait_for(lambda: len(uids(pubsub)) == 2)
    picked = uids(pubsub)[1]
    other = TAG_B if picked == TAG_A else TAG_A
    reader.looked()
    assert uids(pubsub) == [None, picked]  # both answer, the same one is picked every time
    reader.place(other)
    assert wait_for(lambda: uids(pubsub) == [None, picked, other])
    reader.place(None)
    assert wait_for(lambda: uids(pubsub) == [None, picked, other, None])


def test_another_uid_once_changes_nothing(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    """Stacked tags, the picked one misses a single answer - the other one answers alone once: that changes nothing,
    like a single miss of a single tag. Missed twice: the other one is the tag now."""
    reader.place(TAG_A, TAG_B)
    rfid.start()
    assert wait_for(lambda: len(uids(pubsub)) == 2)
    picked = uids(pubsub)[1]
    assert picked is not None
    reader.happen(f"miss@{picked}")
    reader.looked()
    assert uids(pubsub) == [None, picked]
    reader.happen(f"miss@{picked}", f"miss@{picked}")
    assert wait_for(lambda: len(uids(pubsub)) >= 3)
    assert uids(pubsub)[2] == (TAG_B if picked == TAG_A else TAG_A)


def test_failing_select_while_present_changes_nothing(rfid: RFID, reader: Reader, pubsub: SyncPubSub) -> None:
    reader.place(TAG_A)
    rfid.start()
    assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
    reader.happen("noselect")
    reader.looked()
    assert uids(pubsub) == [None, TAG_A]


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
    assert process.stdout.readline() == b"alive\n"  # at the start
    assert process.stdout.readline() == b"ready\n"
    process.stdout.close()
    assert process.wait(timeout=5) == 0


def test_alive_every_interval(program: Path) -> None:
    command = [str(program), "--alive", "0.3", "--pin-rst", "26", "--pin-cs", "8"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    try:
        assert process.stdout.readline() == b"alive\n"
        assert process.stdout.readline() == b"ready\n"
        started = time.monotonic()
        assert process.stdout.readline() == b"alive\n"
        assert process.stdout.readline() == b"alive\n"
        assert 0.4 < time.monotonic() - started < 2  # twice 0.3 s, measured from one pass (0.2 s) to another
    finally:
        process.stdout.close()
        process.wait(timeout=5)


def test_stuck_program_is_ended_and_started_again(
    program: Path,
    reader: Reader,
    pubsub: SyncPubSub,
    logger: logging.Logger,
    log_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A program that hangs (here: stopped, on the box also a stall in SPI) is ended by the driver after three silent
    intervals - SIGTERM is not enough for a stopped one, SIGKILL is - and started again."""
    rfid = create(program, pubsub, logger, log_file, "--alive", "0.2")
    rfid.alive_interval = 0.2
    rfid.stop_timeout = 0.2
    reader.place(TAG_A)
    try:
        rfid.start()
        assert wait_for(lambda: uids(pubsub) == [None, TAG_A])
        stuck = rfid._process
        assert stuck is not None
        os.kill(stuck.pid, signal.SIGSTOP)
        reason = "rfid-reader is stuck: no output for 0.6 s"
        assert wait_for(lambda: any(m.startswith(f"RFID - {reason} (") for m in messages(caplog)))
        assert stuck.poll() is not None  # ended
        assert wait_for(lambda: len(uids(pubsub)) == 4)
        assert [(s.uid, s.error) for s in states(pubsub)][2:] == [(None, reason), (TAG_A, None)]
    finally:
        rfid.stop()


def test_failing_reader_is_logged_once(
    rfid: RFID, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The library prints the error to stdout as well before it throws - the message is logged once nevertheless."""
    monkeypatch.setenv("RFID_STUB_FAIL", "1")  # the library throws if bcm2835_init fails (not root)
    rfid.restart_interval = 60  # one run
    rfid.start()
    assert wait_for(lambda: len(states(pubsub)) == 1)
    (state,) = states(pubsub)
    assert (state.uid, state.error) == (None, "rfid-reader has ended (exit code 1)")
    logged = [message for message in messages(caplog) if "bcm2835_init failed" in message]
    assert logged == ["RFID - rfid-reader: rfid-reader: bcm2835_init failed. Are you running as root?"]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--pin-rst", "26"],
        ["--pin-rst", "x", "--pin-cs", "8"],
        ["--pin-rst", "26", "--pin-cs", "8", "9"],
        ["--pin-rst", "8", "--pin-cs", "8"],  # the same pin twice
        ["--pin-rst", "26", "--pin-cs", "10"],  # MOSI of SPI0
        ["--pin-rst", "9", "--pin-cs", "8"],  # MISO
        ["--pin-rst", "26", "--pin-cs", "11"],  # SCLK
        ["--pin-rst", "26", "--pin-cs", "54"],
        ["--pin-rst", "26", "--pin-cs", "8", "--alive", "0"],
        ["--pin-rst", "26", "--pin-cs", "8", "--alive", "x"],
        ["--pin-rst", "26", "--pin-cs", "8", "--alive", "nan"],
        ["--pin-rst", "26", "--pin-cs", "8", "--beat", "1"],
    ],
)
def test_usage(program: Path, arguments: list[str]) -> None:
    result = subprocess.run([str(program), *arguments], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr


def test_stub_has_the_macros_of_the_library(tmp_path: Path) -> None:
    """MFRC522.h is included last in main.cpp: its Arduino.h defines round(x), abs(x), ... as macros. The stub has them
    as well, so a standard header included after it breaks the build of the tests - as it would break the one of the
    box."""
    source = tmp_path / "order.cpp"
    source.write_text('#include "MFRC522.h"\n#include <chrono>\nint main() { return 0; }\n')
    assert compile_with_stub(source, tmp_path / "order").returncode != 0
    source.write_text('#include <chrono>\n#include "MFRC522.h"\nint main() { digitalWrite(26, HIGH); return 0; }\n')
    assert compile_with_stub(source, tmp_path / "order").returncode == 0
