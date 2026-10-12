"""AvrcpInput: the buttons of the headset read from the linux input device '<headset> (AVRCP)'."""

import logging
import os
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support import wait_for
from tinyjukebox.devices.bluetooth import avrcp_input
from tinyjukebox.devices.bluetooth.avrcp_input import AvrcpInput

EVENT = struct.Struct("llHHi")
EV_SYN, EV_KEY = 0, 1


def key(code: int, value: int = 1) -> bytes:
    return EVENT.pack(0, 0, EV_KEY, code, value)


class Devices:
    """Fake /sys/class/input and /dev/input: the event devices are fifos the test writes to."""

    def __init__(self, folder: Path) -> None:
        self.sys = folder / "sys"
        self.dev = folder / "dev"
        self.sys.mkdir()
        self.dev.mkdir()
        self._writers: dict[str, int] = {}

    def add(self, event: str, name: str) -> None:
        (self.sys / event / "device").mkdir(parents=True)
        (self.sys / event / "device" / "name").write_text(name + "\n")
        os.mkfifo(self.dev / event)
        # read/write: opening a fifo for writing only would block until the reader opens it
        self._writers[event] = os.open(self.dev / event, os.O_RDWR | os.O_NONBLOCK)

    def write(self, event: str, data: bytes) -> None:
        os.write(self._writers[event], data)

    def remove(self, event: str) -> None:
        os.close(self._writers.pop(event))
        os.remove(self.dev / event)
        os.remove(self.sys / event / "device" / "name")

    def close(self) -> None:
        for fd in self._writers.values():
            os.close(fd)


@pytest.fixture
def devices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Devices]:
    monkeypatch.setattr(avrcp_input, "SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(avrcp_input, "POLL_MS", 20)
    devices = Devices(tmp_path)
    yield devices
    devices.close()


@pytest.fixture
def reader(devices: Devices, logger: logging.Logger) -> Iterator[tuple[AvrcpInput, list[str]]]:
    commands: list[str] = []
    reader = AvrcpInput(commands.append, logger, str(devices.sys), str(devices.dev))
    yield reader, commands
    reader.stop()


def test_find_device(devices: Devices, reader: tuple[AvrcpInput, list[str]]) -> None:
    assert reader[0].find_device() is None
    devices.add("event0", "gpio-keys")
    devices.add("event3", "WH-CH530 (AVRCP)")
    (devices.sys / "event9" / "device").mkdir(parents=True)  # no name
    assert reader[0].find_device() == str(devices.dev / "event3")


def test_pressed_buttons_become_commands(devices: Devices, reader: tuple[AvrcpInput, list[str]]) -> None:
    devices.add("event3", "WH-CH530 (AVRCP)")
    reader[0].start()
    devices.write("event3", key(200) + EVENT.pack(0, 0, EV_SYN, 0, 0) + key(200, 0))  # play: pressed, released
    assert wait_for(lambda: reader[1] == ["Play"])
    data = b"".join(key(code) for code in (201, 164, 166, 163, 165, 115, 114, 30))  # 30: KEY_A - no button
    devices.write("event3", data[:10])  # events may arrive in pieces
    devices.write("event3", data[10:])
    assert wait_for(lambda: len(reader[1]) == 8)
    assert reader[1][1:] == ["Pause", "PlayPause", "Stop", "Next", "Previous", "VolumeUp", "VolumeDown"]


def test_device_comes_and_goes_with_the_headset(
    devices: Devices, reader: tuple[AvrcpInput, list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    reader[0].start()  # no headset connected yet
    devices.add("event3", "WH-CH530 (AVRCP)")
    devices.write("event3", key(163))
    assert wait_for(lambda: reader[1] == ["Next"])
    devices.remove("event3")
    devices.add("event4", "WH-CH530 (AVRCP)")  # connected again
    devices.write("event4", key(165))
    assert wait_for(lambda: reader[1] == ["Next", "Previous"])


def test_device_that_cannot_be_read(
    devices: Devices, reader: tuple[AvrcpInput, list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    (devices.sys / "event5" / "device").mkdir(parents=True)
    (devices.sys / "event5" / "device" / "name").write_text("WH-CH530 (AVRCP)")  # no /dev/input/event5
    reader[0].start()
    assert wait_for(lambda: "reading" in caplog.text and "ended" in caplog.text)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert wait_for(lambda: reader[0].find_device() is not None)
    assert len(warnings) == 1  # reported once, not every two seconds
