"""RFID reader RC522 - publishes the tag that the helper program ``rfid-reader`` reports."""

import logging
import os
import re
import selectors
import subprocess
import threading
import time
from collections.abc import Mapping
from typing import Any

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.schema.rfid import SPI_PINS, get_schema
from tinyjukebox.messages import RFID_POLL, RFID_STATE, RfidState

__all__ = ["RFID"]

DEFAULT_COMMAND = ["sudo", "-n", "/usr/local/bin/rfid-reader"]
"""``rfid-reader`` needs root (bcm2835 maps the SPI registers via /dev/mem) - tinyjukebox itself does not."""

RESTART_INTERVAL = 5.0  # seconds until rfid-reader is started again after it has ended
STOP_TIMEOUT = 2.0  # seconds rfid-reader gets to end after SIGTERM
ALIVE_INTERVAL = 10.0  # seconds between two "alive" of rfid-reader - its default (option --alive)
ALIVE_TIMEOUT = 3  # intervals without any line until rfid-reader counts as stuck
READ_TIMEOUT = 0.1  # seconds the thread waits for output before it looks at the stop and the watchdog again

_READY = "ready"
_REMOVED = "removed"
_ALIVE = "alive"
_TAG = re.compile(r"tag ([0-9A-F]{2}(?::[0-9A-F]{2})*)")


class RFID(ADriver):
    """Driver for the RC522 RFID reader.

    The reader is not read by this process: the driver starts the helper program ``rfid-reader`` (C++,
    ``cpp/rfid-reader`` of the repository) and reads its output - one line per change::

        ready                      the reader is initialized, no tag has been seen yet
        tag 04:1A:2B:3C:4D:5E:6F   a tag has been placed (its complete uid, 4 or 7 bytes)
        removed                    the tag has been removed
        alive                      the program still runs (at its start, then every 10 s)

    ``rfid-reader`` is the read loop of the old box (rfid-2-mqtt): only there the presence detection of the RC522
    has proven itself. Every change of the tag is published as :class:`~tinyjukebox.messages.RfidState` to
    :data:`~tinyjukebox.messages.RFID_STATE`; a message on :data:`~tinyjukebox.messages.RFID_POLL` publishes the current
    state in any case.

    If ``rfid-reader`` cannot be started or ends, the state is "no tag" (published if a tag was there) and it is
    started again every :data:`RESTART_INTERVAL` seconds. A program that has written ``alive`` once and then nothing
    for :data:`ALIVE_TIMEOUT` intervals is stuck: the driver ends it and starts it again the same way. A program that
    never writes ``alive`` is never ended.

    Example::

        driver:
            type: rfid
            pins:               # BCM numbers
                pin_rst: 26
                pin_cs: 8       # SPI0 CE0
            command: [sudo, -n, /usr/local/bin/rfid-reader]   # optional - this is the default
    """

    restart_interval = RESTART_INTERVAL
    stop_timeout = STOP_TIMEOUT
    alive_interval = ALIVE_INTERVAL

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
    ) -> None:
        ADriver.__init__(self, config, pubsub_client, logger)

        pins = self._config["pins"]
        _check_pins(pins["pin_rst"], pins["pin_cs"])
        self._command = [
            *self._config.get("command", DEFAULT_COMMAND),
            "--pin-rst",
            str(pins["pin_rst"]),
            "--pin-cs",
            str(pins["pin_cs"]),
        ]
        self._lock = threading.RLock()  # the reader thread and the handler of RFID_POLL publish the state
        self._uid: str | None = None
        self._error: str | None = "rfid-reader has not been started"
        self._published = False
        self._changed_at: float | None = None  # when the uid or the error changed last (time.time())
        self._process: subprocess.Popen[bytes] | None = None
        self._stop_reader = threading.Event()  # one per start: a thread of an earlier start sees its own
        self._thread: LoggerThread | None = None

    # --- state ----------------------------------------------------------------------------------------------------

    def _set_state(self, uid: str | None, error: str | None = None) -> None:
        """Publish the state if the uid has changed (the first state is always published), with the error of that
        moment. An error that comes or goes while the uid stays is in :meth:`state` only: ``STMOperation`` takes every
        ``None`` for "no tag", and a tag placed on the web page must not end because rfid-reader starts again."""
        with self._lock:
            changed = uid != self._uid or not self._published
            if changed or error != self._error:
                self._changed_at = time.time()
            self._uid, self._error = uid, error
            if changed:
                self._publish()

    def _publish(self) -> None:
        with self._lock:
            self._published = True
            self._publish_value(RFID_STATE, RfidState(self._uid, self._error))

    def _poll_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.RFID_POLL`: publish the current state in any case."""
        self._publish()

    def _process_line(self, line: str) -> None:
        tag = _TAG.fullmatch(line)
        if tag is not None:
            self._set_state(tag.group(1))
        elif line == _REMOVED:
            self._set_state(None)
        elif line == _READY:
            self._logger.info("RFID - rfid-reader is ready")
            self._set_state(None)
        elif line and line != _ALIVE:
            # everything else is a message of rfid-reader or of sudo (stderr is read as well)
            self._logger.warning("RFID - rfid-reader: %s", line)

    def _reader_runs(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _driver_state(self) -> dict[str, Any]:
        """``reader``: rfid-reader runs; ``error``: why it does not (``None`` while it runs)."""
        with self._lock:
            return {
                "pins": dict(self._config["pins"]),
                "reader": self._reader_runs(),
                "uid": self._uid,
                "error": self._error,
                "changed_at": self._changed_at,
            }

    # --- rfid-reader ----------------------------------------------------------------------------------------------

    def _read(self, stop: threading.Event) -> str:
        """Start rfid-reader and process its lines until it ends - returns the reason of the end. rfid-reader has
        ended when this returns, also if processing a line fails."""
        with self._lock:
            if stop.is_set():
                return "stopped"
            try:
                process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                )
            except OSError as e:
                return f"cannot start {self._command[0]}: {e}"
            self._process = process
        ended = False
        reason = None
        try:
            reason = self._read_lines(process, stop)
            ended = reason is None
        finally:
            returncode = self._end(process, ended)
            with self._lock:
                self._process = None
        return reason or f"rfid-reader has ended (exit code {returncode})"

    def _read_lines(self, process: subprocess.Popen[bytes], stop: threading.Event) -> str | None:
        """Process the lines of rfid-reader until its output ends (``None``), the driver stops (``"stopped"``) or the
        program is stuck (the reason). The pipe is read in pieces with a timeout, so the thread never blocks: a reader
        that outlives its ``sudo`` keeps the pipe open."""
        assert process.stdout is not None  # noqa: S101 - stdout=PIPE
        fd = process.stdout.fileno()
        rest = b""
        heard: float | None = None  # when rfid-reader wrote last - from its first "alive" on: the watchdog is armed
        timeout = ALIVE_TIMEOUT * self.alive_interval
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while not stop.is_set():
                if not selector.select(READ_TIMEOUT):
                    if heard is not None and time.monotonic() - heard > timeout:
                        return f"rfid-reader is stuck: no output for {timeout:g} s"
                    continue
                data = os.read(fd, 4096)
                if not data:
                    if rest:
                        self._process_line(rest.decode(errors="replace").strip())
                    return None
                *lines, rest = (rest + data).split(b"\n")
                for raw in lines:
                    line = raw.decode(errors="replace").strip()
                    if heard is not None or line == _ALIVE:
                        heard = time.monotonic()
                    self._process_line(line)
        return "stopped"

    def _end(self, process: subprocess.Popen[bytes], ended: bool) -> int:
        """Close the pipe and end rfid-reader - SIGTERM unless its output has ``ended`` already, SIGKILL if it has not
        ended after :attr:`stop_timeout` - and return its exit code.

        ``sudo`` passes SIGTERM on to rfid-reader, SIGKILL reaches only ``sudo``: a reader that is left behind then ends
        by itself once nobody reads its output - the pipe is therefore closed first."""
        assert process.stdout is not None  # noqa: S101 - stdout=PIPE
        process.stdout.close()
        steps = [process.terminate, process.kill]
        if ended:
            steps.insert(0, lambda: None)  # its output has ended: it is ending by itself - give it the time first
        for step in steps:
            step()
            try:
                return process.wait(self.stop_timeout)
            except subprocess.TimeoutExpired:
                pass
        return process.wait()

    def _run(self, stop: threading.Event, previous: threading.Thread | None) -> None:
        if previous is not None:
            previous.join()  # the thread of the last start ends its rfid-reader first - two would disturb each other
        failure = None
        while not stop.is_set():
            reason = self._read(stop)
            if stop.is_set():
                break
            with self._lock:
                worked = self._error is None  # rfid-reader has reported in between: the same reason is news again
            level = logging.DEBUG if reason == failure and not worked else logging.ERROR  # a repeated reason only once
            failure = reason
            self._logger.log(
                level,
                "RFID - %s (%s) - trying again every %s s",
                reason,
                " ".join(self._command),
                self.restart_interval,
            )
            self._set_state(None, reason)
            stop.wait(self.restart_interval)

    # --- life cycle -----------------------------------------------------------------------------------------------

    def healthy(self) -> bool:
        """The driver runs, and so does rfid-reader, the program that reads the tags."""
        return bool(self.is_started) and self._reader_runs()

    def _driver_start(self) -> None:
        previous = self._thread if self._thread is not None and self._thread.is_alive() else None
        self._stop_reader = threading.Event()
        self._published = False
        self._thread = LoggerThread(
            target=self._run, args=(self._stop_reader, previous), name="rfid", logger=self._logger, daemon=True
        )
        self._thread.start()
        self._pubsub_client.subscribe(RFID_POLL, self._poll_handler)

    def _driver_stop(self) -> None:
        # (ignore_not_found: also after a start that failed)
        self._pubsub_client.unsubscribe(RFID_POLL, self._poll_handler, ignore_not_found=True)
        self._stop_reader.set()
        thread = self._thread
        if thread is not None:
            # the thread ends rfid-reader (SIGTERM, SIGKILL after stop_timeout) and closes its end of the pipe. If it
            # takes longer, it is kept: the next start waits for it before it starts another rfid-reader
            thread.join(2 * self.stop_timeout + 2 * READ_TIMEOUT)
            if not thread.is_alive():
                self._thread = None

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()


def _check_pins(pin_rst: int, pin_cs: int) -> None:
    """The pins of rfid-reader: two different ones, none of the data and clock lines of SPI0 (the schema checks
    those too). :raises ValueError: otherwise."""
    if pin_rst == pin_cs:
        raise ValueError(f"rfid: pin_rst and pin_cs must be two different pins (both are {pin_rst}).")
    for name, pin in (("pin_rst", pin_rst), ("pin_cs", pin_cs)):
        if pin in SPI_PINS:
            raise ValueError(f"rfid: {name} must not be {pin} - GPIO 9, 10, 11 are MISO, MOSI and SCLK of SPI0.")
