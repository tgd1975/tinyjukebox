"""The buttons of the headset as linux input device: without a registered media player BlueZ passes the AVRCP buttons
to an input device named '<headset> (AVRCP)'. Read with the standard library only (no python-evdev)."""

import contextlib
import glob
import logging
import os
import select
import struct
import threading
from collections.abc import Callable

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.threads import LoggerThread

__all__ = ["KEYS", "AvrcpInput"]

_EVENT = struct.Struct("llHHi")  # struct input_event: timeval, type, code, value (native sizes)
_EV_KEY = 1
_PRESSED = 1
KEYS = {
    200: "Play",  # KEY_PLAYCD
    201: "Pause",  # KEY_PAUSECD
    164: "PlayPause",  # KEY_PLAYPAUSE
    166: "Stop",  # KEY_STOPCD
    163: "Next",  # KEY_NEXTSONG
    165: "Previous",  # KEY_PREVIOUSSONG
    115: "VolumeUp",  # KEY_VOLUMEUP
    114: "VolumeDown",  # KEY_VOLUMEDOWN
}
"""key code -> command of the headset (as the methods of the media player, plus the volume buttons)"""
SCAN_INTERVAL = 2.0  # seconds between two looks for the input device while there is none
POLL_MS = 500  # the thread checks for stop at least this often


class AvrcpInput:
    """Reads the input device whose name ends with ``(AVRCP)`` and calls ``on_command`` with the command of every
    pressed button (in the reader thread). The device comes and goes with the connection of the headset - the reader
    looks for it every :data:`SCAN_INTERVAL` seconds. The user of the service needs read access (group ``input``)."""

    def __init__(
        self,
        on_command: Callable[[str], object],
        logger: logging.Logger,
        sys_folder: str = "/sys/class/input",
        dev_folder: str = "/dev/input",
    ) -> None:
        self._on_command = on_command
        self._logger = get_child(logger, type(self).__name__)
        self._sys_folder = sys_folder
        self._dev_folder = dev_folder
        self._stop = threading.Event()
        self._thread: LoggerThread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = LoggerThread(target=self._run, name="AvrcpInput", logger=self._logger, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def find_device(self) -> str | None:
        """Path of the AVRCP input device (``/dev/input/eventN``) or None."""
        for name_file in sorted(glob.glob(os.path.join(self._sys_folder, "event*", "device", "name"))):
            try:
                with open(name_file, encoding="utf-8", errors="replace") as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name.endswith("(AVRCP)"):
                event = os.path.basename(os.path.dirname(os.path.dirname(name_file)))
                return os.path.join(self._dev_folder, event)
        return None

    def _run(self) -> None:
        failed: str | None = None
        while not self._stop.is_set():
            device = self.find_device()
            if device is not None:
                try:
                    self._read(device)
                    failed = None
                except OSError as e:  # the device has gone with the headset - or may not be read
                    if failed != str(e):
                        failed = str(e)
                        self._logger.warning("reading %s ended: %s", device, e)
            self._stop.wait(SCAN_INTERVAL)

    def _read(self, device: str) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        self._logger.info("reading the buttons of the headset from %s", device)
        try:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            buffer = b""
            while not self._stop.is_set():
                events = poller.poll(POLL_MS)
                if not events:
                    continue
                if events[0][1] & (select.POLLHUP | select.POLLERR) and not events[0][1] & select.POLLIN:
                    raise OSError("device closed")
                with contextlib.suppress(BlockingIOError):
                    data = os.read(fd, _EVENT.size * 64)
                    if not data:
                        raise OSError("device closed")
                    buffer += data
                while len(buffer) >= _EVENT.size:
                    _, _, kind, code, value = _EVENT.unpack_from(buffer)
                    buffer = buffer[_EVENT.size :]
                    if kind == _EV_KEY and value == _PRESSED and code in KEYS:
                        self._logger.info("button %s (%s)", KEYS[code], code)
                        self._on_command(KEYS[code])
        finally:
            os.close(fd)
