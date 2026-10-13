"""RFID reader RC522 - publishes the tag that the helper program ``rfid-reader`` reports."""

import logging
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from typing import Any

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.schema.rfid import get_schema
from tinyjukebox.messages import RFID_POLL, RFID_STATE, RfidState

__all__ = ["RFID"]

DEFAULT_COMMAND = ["sudo", "-n", "/usr/local/bin/rfid-reader"]
"""``rfid-reader`` needs root (bcm2835 maps the SPI registers via /dev/mem) - tinyjukebox itself does not."""

RESTART_INTERVAL = 5.0  # seconds until rfid-reader is started again after it has ended
STOP_TIMEOUT = 2.0  # seconds rfid-reader gets to end after SIGTERM

_READY = "ready"
_REMOVED = "removed"
_TAG = re.compile(r"tag ([0-9A-F]{2}(?::[0-9A-F]{2})*)")


class RFID(ADriver):
    """Driver for the RC522 RFID reader.

    The reader is not read by this process: the driver starts the helper program ``rfid-reader`` (C++,
    ``cpp/rfid-reader`` of the repository) and reads its output - one line per change::

        ready                      the reader is initialized, no tag has been seen yet
        tag 04:1A:2B:3C:4D:5E:6F   a tag has been placed (its complete uid, 4 or 7 bytes)
        removed                    the tag has been removed

    ``rfid-reader`` is the read loop of the old box (rfid-2-mqtt): only there the presence detection of the RC522
    has proven itself. Every change is published as :class:`~tinyjukebox.messages.RfidState` to
    :data:`~tinyjukebox.messages.RFID_STATE`; a message on :data:`~tinyjukebox.messages.RFID_POLL` publishes the current
    state in any case.

    If ``rfid-reader`` cannot be started or ends, "no tag" is published and it is started again every
    :data:`RESTART_INTERVAL` seconds.

    Example::

        driver:
            type: rfid
            pins:               # BCM numbers
                pin_rst: 26
                pin_cs: 8       # SPI0 CE0
            command: [sudo, -n, /usr/local/bin/rfid-reader]   # optional - this is the default
    """

    restart_interval = RESTART_INTERVAL

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
    ) -> None:
        ADriver.__init__(self, config, pubsub_client, logger)

        pins = self._config["pins"]
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
        self._process: subprocess.Popen[str] | None = None
        self._stop_reader = threading.Event()
        self._thread: LoggerThread | None = None

    # --- state ----------------------------------------------------------------------------------------------------

    def _set_state(self, uid: str | None, error: str | None = None) -> None:
        """Publish the state if the uid has changed (the first state is always published)."""
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
        elif line:
            # everything else is a message of rfid-reader or of sudo (stderr is read as well)
            self._logger.warning("RFID - rfid-reader: %s", line)

    def _driver_state(self) -> dict[str, Any]:
        """``reader``: rfid-reader runs; ``error``: why it does not (``None`` while it runs)."""
        with self._lock:
            return {
                "pins": dict(self._config["pins"]),
                "reader": self._process is not None,
                "uid": self._uid,
                "error": self._error,
                "changed_at": self._changed_at,
            }

    # --- rfid-reader ----------------------------------------------------------------------------------------------

    def _read(self) -> str:
        """Start rfid-reader and process its lines until it ends - returns the reason of the end."""
        with self._lock:
            if self._stop_reader.is_set():
                return "stopped"
            try:
                process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    bufsize=1,
                )
            except OSError as e:
                return f"cannot start {self._command[0]}: {e}"
            self._process = process
        assert process.stdout is not None  # noqa: S101 - stdout=PIPE
        with process.stdout:
            for line in process.stdout:
                self._process_line(line.strip())
        returncode = process.wait()
        with self._lock:
            self._process = None
        return f"rfid-reader has ended (exit code {returncode})"

    def _run(self) -> None:
        failure = None
        while not self._stop_reader.is_set():
            reason = self._read()
            if self._stop_reader.is_set():
                break
            level = logging.DEBUG if reason == failure else logging.ERROR  # the same reason only once
            failure = reason
            self._logger.log(
                level,
                "RFID - %s (%s) - trying again every %s s",
                reason,
                " ".join(self._command),
                self.restart_interval,
            )
            self._set_state(None, reason)
            self._stop_reader.wait(self.restart_interval)

    # --- life cycle -----------------------------------------------------------------------------------------------

    def healthy(self) -> bool:
        """The driver runs, and so does rfid-reader, the program that reads the tags."""
        with self._lock:
            return bool(self.is_started) and self._process is not None

    def _driver_start(self) -> None:
        self._stop_reader.clear()
        self._published = False
        self._thread = LoggerThread(target=self._run, name="rfid", logger=self._logger, daemon=True)
        self._thread.start()
        self._pubsub_client.subscribe(RFID_POLL, self._poll_handler)

    def _driver_stop(self) -> None:
        self._pubsub_client.unsubscribe(RFID_POLL, self._poll_handler)
        with self._lock:
            self._stop_reader.set()
            process = self._process
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            # rfid-reader ends on SIGTERM at once; a reader that had to be killed (sudo does not pass SIGKILL on) may
            # keep the pipe open - it ends by itself as soon as this process has ended
            self._thread.join(STOP_TIMEOUT)
            self._thread = None

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
