"""One running Soloist: the process, its commands (``soloist ctl``) and its events (``soloist ctl trace``).

Soloist is started with its WebSocket interface on a free port of 127.0.0.1; ``soloist ctl`` finds it through the files
``ws.addr`` and ``ws.port`` in its data directory. Every event is one line ``<unix_epoch_ms> <json>``.
"""

import contextlib
import json
import logging
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any

from tinyjukebox.core.logging import get_child

__all__ = ["CACHE_MB", "CTL_TIMEOUT", "Soloist", "SoloistError", "parse_event"]

CACHE_MB = 100
"""--cache-size: the smallest value Soloist takes. It does not keep to it (open report no. 8): the box empties the
cache whenever it ends Soloist."""
CTL_TIMEOUT = 10.0

EventHook = Callable[[dict[str, Any]], None]


class SoloistError(Exception):
    """A command of ``soloist ctl`` failed."""


def parse_event(line: str) -> dict[str, Any] | None:
    """The event of a line of ``soloist ctl trace`` - None for anything else."""
    _, _, text = line.strip().partition(" ")
    try:
        event = json.loads(text)
    except ValueError:
        return None
    return event if isinstance(event, dict) and isinstance(event.get("type"), str) else None


class Soloist:
    """Soloist of the box, started by :meth:`start` and ended by :meth:`stop`. ``on_event`` is called with every
    event (from a thread of its own), ``on_exit`` with the exit code when Soloist ends without being stopped."""

    def __init__(
        self,
        program: str,
        key: str,
        name: str,
        data_dir: str,
        cache_dir: str,
        logger: logging.Logger,
        env: Mapping[str, str] | None = None,
        device: str | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._program = program
        self._key = key
        self._name = name
        self._data_dir = data_dir
        self._cache_dir = cache_dir
        self._logger = get_child(logger, type(self).__name__)
        self._env = dict(env) if env is not None else None
        self._device = device
        self._popen = popen
        self._run = run
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._trace: subprocess.Popen[bytes] | None = None
        self._threads: list[threading.Thread] = []
        self._stopped = threading.Event()
        self.on_event: EventHook = lambda event: None
        self.on_exit: Callable[[int], None] = lambda code: None

    def command(self) -> list[str]:
        command = [self._program, "-n", self._name, "-k", self._key, "-D", self._data_dir, "-C", self._cache_dir,
                   "-z", str(CACHE_MB), "-i", "100", "-w", "127.0.0.1:0"]  # fmt: skip
        if self._device is not None:
            command += ["-d", self._device]
        return command

    def start(self) -> None:
        """Start Soloist and follow its events. :raises OSError: if it cannot be started."""
        self._stopped.clear()
        process = self._popen(self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self._env)
        with self._lock:
            self._process = process
        self._logger.info("Soloist started (pid %s)", process.pid)
        for target, name in ((self._follow, "Soloist.trace"), (self._watch, "Soloist.watch")):
            thread = threading.Thread(target=target, args=(process,), name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        """Wait for the end of Soloist; what it said last goes to the log."""
        said: list[bytes] = []
        if process.stderr is not None:
            with contextlib.suppress(OSError, ValueError), process.stderr:
                for line in process.stderr:
                    said = [*said[-9:], line]
        code = process.wait()
        if self._stopped.is_set():
            return
        self._logger.warning(
            "Soloist ended (%s): %s", code, b"".join(said).decode("utf-8", errors="replace").strip()[-500:]
        )
        self.on_exit(code)

    def _follow(self, process: subprocess.Popen[bytes]) -> None:
        """``soloist ctl trace`` - started again while Soloist runs (it needs a moment until its port is known)."""
        while not self._stopped.is_set() and process.poll() is None:
            try:
                trace = self._popen(
                    self._ctl("trace"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self._env
                )
            except OSError as e:
                self._logger.error("cannot follow the events of Soloist: %s", e)
                return
            with self._lock:
                self._trace = trace
            self._read(trace.stdout)
            if trace.stdout is not None:
                trace.stdout.close()
            trace.wait()
            if not self._stopped.wait(0.5):
                self._logger.debug("soloist ctl trace ended (%s) - following again", trace.returncode)

    def _read(self, stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        with contextlib.suppress(OSError, ValueError):
            for raw in stream:
                event = parse_event(raw.decode("utf-8", errors="replace"))
                if event is not None:
                    try:
                        self.on_event(event)
                    except Exception:
                        self._logger.exception("handling the event %s failed", event.get("type"))

    def _ctl(self, *args: str) -> list[str]:
        return [self._program, "ctl", "-D", self._data_dir, *args]

    def ctl(self, *args: str) -> str:
        """A command of ``soloist ctl``; returns what it printed. :raises SoloistError: if it fails."""
        try:
            done = self._run(self._ctl(*args), capture_output=True, text=True, timeout=CTL_TIMEOUT, env=self._env)
        except (OSError, subprocess.SubprocessError) as e:
            raise SoloistError(f"soloist ctl {' '.join(args)}: {e}") from e
        if done.returncode != 0:
            raise SoloistError(f"soloist ctl {' '.join(args)}: {(done.stderr or done.stdout).strip()[-300:]}")
        return done.stdout

    def json(self, *args: str) -> dict[str, Any]:
        """A command of ``soloist ctl`` that prints json (``now --json``, ``queue N --json``)."""
        text = self.ctl(*args)
        try:
            value = json.loads(text)
        except ValueError as e:
            raise SoloistError(f"soloist ctl {' '.join(args)}: no json") from e
        return value if isinstance(value, dict) else {}

    def stop(self, timeout: float = 5.0) -> None:
        self._stopped.set()
        with self._lock:
            processes: Sequence[subprocess.Popen[bytes] | None] = (self._trace, self._process)
            self._trace = self._process = None
        for process in processes:
            if process is not None and process.poll() is None:
                process.terminate()
        for process in processes:
            if process is None:
                continue
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout)
        self._threads = []
        self._logger.info("Soloist stopped")
