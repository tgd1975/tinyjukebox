"""Which version of the box software runs, and whether it is healthy (docs/developer/library.md, section 6).

The box has slots with a version each; the start script and ``tinyjukebox-slot`` (deploy/slot, outside the slots)
install, switch and go back. They do the "how". The application does the "when": it asks the script to look for a
version, and it tells the script how the health check of a new version went:

- stage 1, seconds after the start: the software runs, everything the config asks for runs, and no unexpected
  exception arrived at the central place (:mod:`tinyjukebox.core.unexpected`),
- stage 2, in use: an album was played because of a tag, and a configured time passed after that without an
  unexpected exception.

A version on probation that fails ends at once; the start script starts the previous version. On a box without slots -
the PC, a box that was not set up with them - nothing of this happens.

A library announces a new version. A box without a library has nobody who does: the application lets the script
look after the start and once a day (section 6.16).
"""

import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child

__all__ = ["COMMANDS", "DEFECT", "LOOK", "Health", "Slots"]

DEFECT = 70  # exit status of a version that ends itself because its health check failed
NO_ANSWER = 69  # exit status of "tinyjukebox-slot update --auto" if the releases did not answer: ask again soon
LOOK = (60.0, 24 * 3600.0, 600.0)
"""Seconds until a box without a library looks for a version: after the start, between two looks, and after a look
that got no answer."""
COMMANDS = ("check", "update", "revert", "use", "fallback", "promote", "mode", "skip")
"""What the tab "System" of the web page may ask the slot script for."""


class Slots:
    """The slot script as the application uses it. ``state/`` is written by the scripts only."""

    def __init__(self, logger: logging.Logger, command: str = "tinyjukebox-slot") -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._command = shutil.which(command)
        self._lock = threading.Lock()  # held while a command of the page runs
        self.available = False
        if self._command is not None:
            # the script alone is not enough: the box must have been set up with slots, and one must be active
            self.available = bool(self.status().get("active"))

    def run(self, *arguments: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        """Call the script. What it prints goes to the log, except for the calls the application makes all the
        time."""
        if self._command is None:
            return subprocess.CompletedProcess(arguments, 127, "", "tinyjukebox-slot is not installed")
        try:
            result = subprocess.run(
                [self._command, *arguments], capture_output=True, text=True, check=False, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(arguments, 1, "", str(error))
        if not arguments[0].startswith("_") and arguments[:2] != ("status", "--json"):
            for line in (result.stdout + result.stderr).splitlines():
                self._logger.info("tinyjukebox-slot %s: %s", arguments[0], line)
        return result

    def status(self) -> dict[str, Any]:
        """Slots, active slot, update mode, states of the versions, switches - empty without slots."""
        result = self.run("status", "--json", timeout=30)
        try:
            status = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            status = {}
        return status if isinstance(status, dict) else {}

    def command(self, command: str, arguments: list[str], by: str) -> dict[str, Any]:
        """A command of the tab "System". The lock makes a restart that the command asks for wait until the page
        has its answer (:meth:`wait_for_command`)."""
        if command not in COMMANDS:
            raise ValueError(f"unknown command '{command}'")
        with self._lock:
            result = self.run(command, "--by", by, *arguments)
        return {"status": result.returncode, "output": result.stdout + result.stderr}

    def wait_for_command(self, timeout: float = 5.0) -> None:
        """Called when the service stops: a command of the page may have asked for this restart itself."""
        if self._lock.acquire(timeout=timeout):
            self._lock.release()


class Health:
    """The health check of the running version (section 6.4), and what the application decides about versions."""

    def __init__(
        self,
        slots: Slots,
        logger: logging.Logger,
        stage1_seconds: float = 10.0,
        stage2_seconds: float = 60.0,
        journal: Callable[..., None] | None = None,
        end: Callable[[int], None] = os._exit,
        look: tuple[float, float, float] = LOOK,
        alone: Callable[[], bool] = lambda: False,
    ) -> None:
        """``journal(event, **fields)`` notes what happened for the library; ``end(status)`` ends the process.
        ``alone()``: the box is not linked to a library, so it looks for versions by itself, at the times of
        ``look`` (:data:`LOOK`)."""
        self._slots = slots
        self._logger = get_child(logger, type(self).__name__)
        self._stage1_seconds = stage1_seconds
        self._stage2_seconds = stage2_seconds
        self._journal = journal or (lambda event, **fields: None)
        self._end = end
        self._look_first, self._look_every, self._look_again = look
        self._alone = alone
        self._lock = threading.Lock()
        self._timers: list[threading.Timer] = []
        self._failed = False
        self._proof = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._updater: threading.Thread | None = None
        self.on_change: Callable[[], None] | None = None  # the library hears of it at once
        active = slots.status() if slots.available else {}
        self.slot = str(active.get("active", ""))
        self.probation = active.get("slots", {}).get(self.slot, {}).get("state") == "probation"

    # --- start and stop ----------------------------------------------------------------------------------------------

    def start(self, missing: Callable[[], list[str]]) -> None:
        """The service has started. ``missing()`` names what the config asks for and does not run."""
        unexpected.subscribe(self.unexpected)
        if not self._slots.available:
            return
        self._stop.clear()
        self._timer(self._stage1_seconds, lambda: self._stage1(missing))
        self._updater = threading.Thread(target=self._look, name="Health.updater", daemon=True)
        self._updater.start()

    def stop(self) -> None:
        unexpected.unsubscribe(self.unexpected)
        self._stop.set()
        self._wake.set()
        with self._lock:
            timers, self._timers = self._timers, []
        for timer in timers:
            timer.cancel()
        if self._updater is not None:
            self._updater.join(timeout=5)
            self._updater = None

    def _timer(self, seconds: float, action: Callable[[], None]) -> None:
        timer = threading.Timer(seconds, action)
        timer.name = "Health.timer"
        timer.daemon = True
        with self._lock:
            self._timers.append(timer)
        timer.start()

    # --- the health check --------------------------------------------------------------------------------------------

    def unexpected(self, error: BaseException, where: str) -> None:
        """An exception that nobody expected. During the probation it ends this version; afterwards it is only
        reported to the library."""
        self._failed = True
        try:
            self._journal("unexpected", error=repr(error), where=where, probation=self.probation)
        except Exception:  # e.g. the card is full: a version on probation ends all the same
            self._logger.exception("the unexpected exception cannot be noted in the journal")
        if self.probation and self._slots.available:
            self._logger.error("unexpected exception during the probation (%s): this version ends", where)
            self._slots.run("_failure", f"unexpected exception in {where}: {error!r}")
            logging.shutdown()
            self._end(DEFECT)
        elif self.on_change is not None:
            self.on_change()

    def _stage1(self, missing: Callable[[], list[str]]) -> None:
        lacking = missing()
        if self._failed or lacking:
            # the start script ends a version on probation that does not pass in time
            self._logger.error("stage 1 of the health check not passed: %s", lacking or "unexpected exception")
            return
        self._slots.run("_passed", "1")
        self._logger.info("stage 1 of the health check passed")

    def played(self, kind: str) -> None:
        """Something is playing because of a tag. An album confirms a version; a radio station never does, and
        never condemns one: if a stream fails, the cause is mostly the station or the internet."""
        if kind != "album" or not self.probation or self._proof or not self._slots.available:
            return
        self._proof = True
        self._timer(self._stage2_seconds, self._stage2)

    def _stage2(self) -> None:
        if self._failed or not self.probation:
            return
        self._slots.run("_passed", "2")
        self.probation = False
        self._logger.info("stage 2 of the health check passed: this version is confirmed")
        self._journal("confirmed", slot=self.slot)
        if self.on_change is not None:
            self.on_change()

    # --- looking for a version ---------------------------------------------------------------------------------------

    def look_for_versions(self) -> None:
        """The library may have another version, or changed the state of one: let the script find out."""
        self._wake.set()

    def _look(self) -> None:
        """The script fetches and installs (section 6.11); it may take minutes, so it has a thread of its own.

        A box with a library looks when the library says so. A box without one looks by the clock: after the start,
        once a day, and soon again if nobody answered - no WLAN yet, or a clock that is not set."""
        wait = self._look_first
        while True:
            asked = self._wake.wait(wait)
            if self._stop.is_set():
                return
            self._wake.clear()
            wait = self._look_every
            if not asked and not self._alone():
                continue
            result = self._slots.run("update", "--auto")
            if result.returncode == NO_ANSWER:
                wait = self._look_again
            elif result.returncode != 0:
                self._logger.error("looking for a version failed (%d)", result.returncode)
            if self.on_change is not None:
                self.on_change()  # report what happened
