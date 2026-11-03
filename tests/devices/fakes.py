"""Fake hardware - ``RPi.GPIO`` is replaced in ``sys.modules`` for all tests, ``rfid-reader`` by a process the test
controls.

The fakes are installed unconditionally (also on a Raspberry Pi), so tests never touch real hardware.
"""

import os
import sys
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PinState:
    direction: int | None = None
    pull: int | None = None
    level: int = 1


class FakeGPIO(types.ModuleType):
    """Subset of ``RPi.GPIO`` used by the drivers, plus helpers to simulate inputs."""

    BCM = 11
    BOARD = 10
    OUT = 0
    IN = 1
    HIGH = 1
    LOW = 0
    PUD_OFF = 20
    PUD_DOWN = 21
    PUD_UP = 22
    RISING = 31
    FALLING = 32
    BOTH = 33

    def __init__(self) -> None:
        super().__init__("RPi.GPIO")
        self.reset()

    def reset(self) -> None:
        self.mode: int | None = None
        self.pins: dict[int, PinState] = {}
        self.events: dict[int, tuple[Callable[[int], None], int | None]] = {}
        self.cleaned: list[int | None] = []
        self.outputs: list[tuple[int, int]] = []  # history of output() calls

    # --- RPi.GPIO API ---------------------------------------------------------------------------------------------

    def setmode(self, mode: int) -> None:
        self.mode = mode

    def setwarnings(self, flag: bool) -> None:
        pass

    def setup(self, pin: int, direction: int, pull_up_down: int | None = None, initial: int | None = None) -> None:
        state = self.pins.setdefault(pin, PinState())
        state.direction = direction
        state.pull = pull_up_down
        if initial is not None:
            state.level = initial

    def output(self, pin: int, level: int | bool) -> None:
        state = self.pins.get(pin)
        if state is None or state.direction != self.OUT:
            raise RuntimeError(f"pin {pin} is not set up as output")
        state.level = int(level)
        self.outputs.append((pin, int(level)))

    def input(self, pin: int) -> int:
        state = self.pins.get(pin)
        if state is None:
            raise RuntimeError(f"pin {pin} is not set up")
        return state.level

    def add_event_detect(
        self, pin: int, edge: int, callback: Callable[[int], None] | None = None, bouncetime: int | None = None
    ) -> None:
        if pin in self.events:
            raise RuntimeError(f"conflicting edge detection already enabled for pin {pin}")
        if callback is None:
            raise ValueError("callback missing")
        self.events[pin] = (callback, bouncetime)

    def remove_event_detect(self, pin: int) -> None:
        self.events.pop(pin, None)

    def cleanup(self, pin: int | None = None) -> None:
        self.cleaned.append(pin)
        if pin is None:
            self.pins.clear()
            self.events.clear()
        else:
            self.pins.pop(pin, None)

    # --- helpers for tests ----------------------------------------------------------------------------------------

    def set_input(self, pin: int, level: int) -> None:
        """Change the level of an input pin and call its edge callback (if registered and the level changed)."""
        state = self.pins.setdefault(pin, PinState())
        changed = state.level != level
        state.level = level
        if changed and pin in self.events:
            self.events[pin][0](pin)

    def level(self, pin: int) -> int:
        return self.pins[pin].level


_LEFT_BEHIND_SCRIPT = """
import os
import select
import signal
import sys

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1] + ".pid", "w") as file:
    file.write(str(os.getpid()))
poller = select.poll()
poller.register(sys.stdout.fileno(), 0)  # POLLERR once nobody reads the pipe any more
while not poller.poll(50):
    pass
open(sys.argv[1], "w").close()
"""

_READER_SCRIPT = f"""
import subprocess
import sys

LEFT_BEHIND = {_LEFT_BEHIND_SCRIPT!r}

for line in open(sys.argv[1]):
    line = line.strip()
    if line == "!args":
        print(" ".join(sys.argv[2:]), flush=True)
    elif line.startswith("!exit"):
        sys.exit(int(line.split()[1]))
    elif line.startswith("!left-behind"):
        subprocess.Popen([sys.executable, "-c", LEFT_BEHIND, line.split()[1]])
    else:
        print(line, flush=True)
"""


class FakeRfidReader:
    """``rfid-reader``: :attr:`command` starts a process that prints the lines the test writes (via a fifo).

    ``!args`` makes it print its arguments, ``!exit N`` ends it with exit code N; :meth:`end` ends it with 0.
    ``!left-behind FILE`` starts a child that keeps the output open and that SIGTERM does not end - like rfid-reader
    behind a ``sudo`` that got SIGKILL. Like rfid-reader it ends once nobody reads the output any more; it writes its
    pid to ``FILE.pid`` and creates ``FILE`` when it ends.
    A line written while no process is running (e.g. the driver starts the next one) waits for the process."""

    def __init__(self, folder: Path) -> None:
        self.fifo = folder / "rfid-reader.fifo"
        os.mkfifo(self.fifo)
        self.command = [sys.executable, "-c", _READER_SCRIPT, str(self.fifo)]
        self._fd: int | None = None

    def _open(self) -> int | None:
        """The writing end of the fifo - ``None`` as long as no process reads it."""
        try:
            return os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return None

    def running(self) -> bool:
        """True if a reader process has opened the fifo."""
        if self._fd is None:
            self._fd = self._open()  # kept: closing it again would end the process
            return self._fd is not None
        probe = self._open()
        if probe is not None:
            os.close(probe)
        return probe is not None

    def write(self, line: str) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._fd is None:
                self._fd = self._open()
            if self._fd is not None:
                try:
                    os.write(self._fd, f"{line}\n".encode())
                except BrokenPipeError:
                    pass  # the process has ended - the driver starts the next one
                else:
                    return
            time.sleep(0.005)
        raise TimeoutError("no reader process is running")

    def ready(self) -> None:
        self.write("ready")

    def place(self, uid: str) -> None:
        self.write(f"tag {uid}")

    def remove(self) -> None:
        self.write("removed")

    def end(self) -> None:
        """End the reader process: it reads the end of the fifo."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


GPIO = FakeGPIO()


def install() -> None:
    """Install the fakes in ``sys.modules`` (before any driver module is imported)."""
    rpi = types.ModuleType("RPi")
    rpi.GPIO = GPIO  # type: ignore[attr-defined]
    sys.modules["RPi"] = rpi
    sys.modules["RPi.GPIO"] = GPIO


# --- bluetooth ----------------------------------------------------------------------------------------------------


class FakeBluez:
    """:class:`tinyjukebox.devices.bluetooth.bluez.Bluez` without D-Bus: the devices are set by the test
    (:meth:`set_device`, thread safe - applied in the event loop of the driver); calls are recorded in ``calls``.

    ``in_range``: devices (paths) that answer - connecting the others fails; ``pair_error``: pairing fails with it.
    """

    def __init__(self) -> None:
        from tinyjukebox.devices.bluetooth.bluez import Device

        self._device_class = Device
        self.on_change: Callable[[], None] | None = None
        self.items: dict[str, Any] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.in_range: set[str] = set()
        self.pair_error: Exception | None = None
        self.connect_error: Exception | None = None
        self.unavailable = 0  # this many connects to BlueZ fail
        self.connect_exception: Exception | None = None  # raised by the next connect, once
        self.busy = 0  # this many starts of the search are refused
        self.discovering = False
        self.playback_status: str | None = None
        self.policy: Any = None
        self.player: Any = None
        self.loop: Any = None
        self.closed = False

    # --- helpers for tests ----------------------------------------------------------------------------------------

    @staticmethod
    def path(address: str) -> str:
        return "/org/bluez/hci0/dev_" + address.replace(":", "_")

    def device(self, address: str, name: str = "WH-CH530", **fields: Any) -> Any:
        fields.setdefault("device_class", 0x240404)  # audio/video, wearable headset
        return self._device_class(path=self.path(address), address=address, name=name, **fields)

    def _apply(self, action: Callable[[], None]) -> None:
        def run() -> None:
            action()
            if self.on_change is not None:
                self.on_change()

        if self.loop is None:
            action()
        else:
            self.loop.call_soon_threadsafe(run)

    def set_device(self, device: Any) -> None:
        """Add or replace a device (as if BlueZ had signalled the change)."""
        self._apply(lambda: self.items.__setitem__(device.path, device))

    def update(self, address: str, **fields: Any) -> None:
        import dataclasses

        path = self.path(address)
        self._apply(lambda: self.items.__setitem__(path, dataclasses.replace(self.items[path], **fields)))

    def remove(self, address: str) -> None:
        self._apply(lambda: self.items.pop(self.path(address), None))

    def press(self, command: str) -> None:
        """A button of the headset (AVRCP) - BlueZ calls the registered media player."""
        self.loop.call_soon_threadsafe(self.player.player_command, command)

    def called(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]

    # --- Bluez ----------------------------------------------------------------------------------------------------

    async def connect(self, policy: Any, player: Any) -> None:
        import asyncio

        from tinyjukebox.devices.bluetooth.bluez import BluezError

        self.calls.append(("connect",))
        if self.connect_exception is not None:
            error, self.connect_exception = self.connect_exception, None
            raise error
        if self.unavailable:
            self.unavailable -= 1
            raise BluezError("org.freedesktop.DBus.Error.ServiceUnknown", "The name org.bluez was not provided")
        self.loop = asyncio.get_running_loop()
        self.policy, self.player = policy, player
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.loop = None

    def devices(self) -> list[Any]:
        return list(self.items.values())

    def _replace(self, path: str, **fields: Any) -> None:
        import dataclasses

        self.items[path] = dataclasses.replace(self.items[path], **fields)
        if self.on_change is not None:
            self.on_change()

    async def start_discovery(self) -> None:
        from tinyjukebox.devices.bluetooth.bluez import BluezError

        self.calls.append(("start_discovery",))
        if self.busy:
            self.busy -= 1
            raise BluezError("org.bluez.Error.InProgress", "Operation already in progress")
        self.discovering = True

    async def stop_discovery(self) -> None:
        if self.discovering:
            self.calls.append(("stop_discovery",))
        self.discovering = False

    async def pair(self, path: str) -> None:
        self.calls.append(("pair", path, self.discovering, self.policy.allows_pairing(path)))
        if self.pair_error is not None:
            raise self.pair_error
        self._replace(path, paired=True)

    async def set_trusted(self, path: str) -> None:
        self.calls.append(("set_trusted", path))
        self._replace(path, trusted=True)

    async def connect_device(self, path: str) -> None:
        from tinyjukebox.devices.bluetooth.bluez import BluezError

        self.calls.append(("connect_device", path))
        if self.connect_error is not None:
            raise self.connect_error
        if path not in self.in_range:
            raise BluezError("org.bluez.Error.Failed", "br-connection-page-timeout")
        self._replace(path, connected=True, audio=True)

    async def remove_device(self, path: str) -> None:
        self.calls.append(("remove_device", path))
        self.items.pop(path, None)
        if self.on_change is not None:
            self.on_change()

    def set_playback_status(self, status: str) -> None:
        self.playback_status = status


class FakeMixer:
    """The volume control of the headset: ``volumes`` set by the driver; :meth:`report`: changed on the headset."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.volumes: list[float] = []
        self.callback: Callable[[float], None] | None = None
        self.watching = False
        self.failures = 0  # this many calls of set_volume fail (the mixer of BlueALSA is not there yet)

    def set_volume(self, volume: float) -> bool:
        if self.failures:
            self.failures -= 1
            return False
        self.volumes.append(volume)
        self.report(volume)  # like alsa: the own change is reported as well
        return True

    def watch(self, callback: Callable[[float], None]) -> None:
        self.callback = callback
        self.watching = True

    def stop_watching(self) -> None:
        self.watching = False

    def report(self, volume: float) -> None:
        if self.watching and self.callback is not None:
            self.callback(volume)
