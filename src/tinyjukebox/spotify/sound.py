"""The sound of Soloist on the box: a PipeWire of its own whose only output is a named pipe, and the reader of that pipe
that hands the sound to ``aplay`` (docs/developer/architecture.md, section 4.1).

Soloist plays only into PipeWire or PulseAudio; the box plays with ``aplay``, to the speaker or through BlueALSA to the
headset. PipeWire and WirePlumber are started with a setup of their own in the folder ``spotify``: no sound card, no
Bluetooth, one sink that writes 16 bit stereo, 44.1 kHz into the pipe. The box reads the pipe itself. That way it sees
whether sound comes (a playback that stands still while Soloist says "playing", open report no. 5), it starts another
``aplay`` on another output without touching Soloist, and it drops the sound while it skips to a saved track.

A process is ended with SIGTERM first and SIGKILL after a while, and every wait is bounded: an ``aplay`` that hangs in
its driver never empties its pipe, and whoever ends it must not wait for that.
"""

import contextlib
import errno
import logging
import os
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import IO, Any

from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.core.logging import get_child

__all__ = ["NODE", "PipeReader", "PipeWire", "SoundServer", "is_silent"]

NODE = "tinyjukebox"
"""Name of the sink of PipeWire into which Soloist plays (``--pipewire-device``)."""
CHUNK = 4096
FORMAT = "S16LE"
END_TIMEOUT = 2.0
"""Seconds a process has to end after SIGTERM, and again after SIGKILL."""

PIPEWIRE_CONF = """# written by tinyjukebox: the only output of this PipeWire is the pipe that the box reads
context.modules = [
    {{ name = libpipewire-module-pipe-tunnel
        args = {{
            tunnel.mode = sink
            pipe.filename = "{pipe}"
            audio.format = {format}
            audio.rate = 44100
            audio.channels = 2
            audio.position = [ FL FR ]
            stream.props = {{
                node.name = "{node}"
                node.description = "Tiny Jukebox"
                priority.session = 2000
            }}
        }}
    }}
]
"""

WIREPLUMBER_CONF = """# written by tinyjukebox: no sound card and no Bluetooth - those belong to the box and to BlueALSA
wireplumber.profiles = {
    main = {
        monitor.alsa = disabled
        monitor.alsa-midi = disabled
        monitor.bluez = disabled
        monitor.bluez.midi = disabled
        monitor.v4l2 = disabled
        monitor.libcamera = disabled
        support.reserve-device = disabled
        support.logind = disabled
    }
}
"""


def is_silent(chunk: bytes) -> bool:
    """Digital silence: nothing but zeros."""
    return not chunk.strip(b"\x00")


class SoundServer:
    """What Soloist plays into - ``env`` for Soloist, ``device`` its sink, ``reader`` the pipe."""

    env: Mapping[str, str] | None = None
    device: str | None = None
    reader: "PipeReader"

    def start(self) -> None: ...

    def stop(self) -> None: ...


class PipeReader:
    """Reads the pipe and hands the sound to ``aplay`` on the current output. ``muted``: the sound is dropped.
    ``on_output_lost``: ``aplay`` failed on the headset (it has gone)."""

    def __init__(
        self,
        pipe: str,
        output: AudioOutput,
        logger: logging.Logger,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pipe = pipe
        self._output = output
        self._logger = get_child(logger, type(self).__name__)
        self._popen = popen
        self._clock = clock
        self._lock = threading.Lock()
        self._aplay: subprocess.Popen[bytes] | None = None
        self._headset = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._fds: tuple[int, int] | None = None
        self.muted = True
        self.last_sound = clock()
        """When the last chunk that was not silent arrived (while not muted)."""
        self.on_output_lost: Callable[[], None] = lambda: None

    @property
    def pipe(self) -> str:
        return self._pipe

    def open(self) -> None:
        """Create the pipe and open it - before PipeWire starts, which writes into it."""
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._pipe)
        os.mkfifo(self._pipe, 0o600)
        read = os.open(self._pipe, os.O_RDONLY | os.O_NONBLOCK)
        # a writer of our own: without one, every read returns at once while PipeWire has not opened the pipe
        write = os.open(self._pipe, os.O_WRONLY | os.O_NONBLOCK)
        self._fds = (read, write)
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, name="Spotify.pipe", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._fds is not None  # noqa: S101 - set by open()
        read = self._fds[0]
        while not self._stopped.is_set():
            ready, _, _ = select.select([read], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(read, CHUNK)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                time.sleep(0.05)
                continue
            if self.muted:
                continue
            if not is_silent(chunk):
                self.last_sound = self._clock()
            self._play(chunk)

    def _play(self, chunk: bytes) -> None:
        with self._lock:
            aplay = self._aplay
            if aplay is None or aplay.poll() is not None:
                if self.muted:  # silenced meanwhile: no aplay of its own any more
                    return
                command, self._headset = self._output.current()
                try:
                    # unbuffered: ending aplay never flushes into a pipe that a hung aplay does not empty
                    aplay = self._popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, bufsize=0)  # fmt: skip
                except OSError as e:
                    self._logger.error("cannot start %s: %s", command[0], e)
                    return
                self._aplay = aplay
        try:
            assert aplay.stdin is not None  # noqa: S101 - stdin=PIPE
            _write_all(aplay.stdin, chunk)
        except (BrokenPipeError, ValueError, OSError):
            with self._lock:
                if self._aplay is not aplay:
                    return  # ended by the box: another output, or silence
                headset, self._aplay = self._headset, None
            if headset:
                # never on to the speaker by itself: the box must not become loud unexpectedly
                self.muted = True
                self._logger.warning("the headset output failed - pause")
                self.on_output_lost()

    def restart_output(self) -> None:
        """End ``aplay``: the next sound starts another one, on the output that is current then. Waits at most
        twice :data:`END_TIMEOUT`."""
        with self._lock:
            aplay, self._aplay = self._aplay, None
        if not _end(aplay):
            self._logger.error("aplay does not end (pid %s) - left behind", getattr(aplay, "pid", None))

    def silence(self) -> None:
        """Drop the sound from now on, and end ``aplay``: what is in its buffer is not heard."""
        self.muted = True
        self.restart_output()

    def sound(self) -> None:
        """Hand the sound to ``aplay`` again."""
        self.last_sound = self._clock()
        self.muted = False

    def close(self) -> None:
        """End the reader and its ``aplay`` - aplay first: the reader may wait for it to take the sound."""
        self._stopped.set()
        self.silence()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)
        fds, self._fds = self._fds, None
        for fd in fds or ():
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._pipe)


def _write_all(stream: IO[bytes], chunk: bytes) -> None:
    """All of ``chunk`` into an unbuffered pipe: a write interrupted by a signal takes only a part of it."""
    written: int | None = stream.write(chunk)
    while written is not None and 0 < written < len(chunk):
        chunk = chunk[written:]
        written = stream.write(chunk)


def _end(process: subprocess.Popen[Any] | None, timeout: float | None = None) -> bool:
    """End ``process``: SIGTERM, SIGKILL after ``timeout`` (:data:`END_TIMEOUT`) - every wait is bounded. Its input is
    closed only then: closed first, it would be flushed into a pipe that a hung ``aplay`` never empties. Returns False
    if it does not end (it hangs in a driver) - it is left behind."""
    if process is None:
        return True
    timeout = END_TIMEOUT if timeout is None else timeout
    ended = True
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                ended = False
    if process.stdin is not None:
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()
    return ended


class PipeWire(SoundServer):
    """PipeWire and WirePlumber of the box, in ``folder`` (``spotify/sound``): config, runtime directory, pipe."""

    def __init__(
        self,
        folder: str,
        output: AudioOutput,
        logger: logging.Logger,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self._folder = folder
        self._logger = get_child(logger, type(self).__name__)
        self._popen = popen
        self._processes: list[subprocess.Popen[bytes]] = []
        runtime = os.path.join(folder, "run")
        self._env: dict[str, str] = {
            **os.environ,
            "XDG_RUNTIME_DIR": runtime,
            "PIPEWIRE_RUNTIME_DIR": runtime,
            "XDG_CONFIG_HOME": os.path.join(folder, "config"),
            "XDG_STATE_HOME": os.path.join(folder, "state"),
        }
        self._env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        self._env.pop("PULSE_SERVER", None)
        self.env = self._env
        self.device = NODE
        self.reader = PipeReader(os.path.join(folder, "sound.pipe"), output, logger, popen)

    def _write_config(self) -> None:
        for name, content in (
            (os.path.join("pipewire", "pipewire.conf.d", "tinyjukebox.conf"),
             PIPEWIRE_CONF.format(pipe=self.reader.pipe, format=FORMAT, node=NODE)),
            (os.path.join("wireplumber", "wireplumber.conf.d", "tinyjukebox.conf"), WIREPLUMBER_CONF),
        ):  # fmt: skip
            path = os.path.join(self._env["XDG_CONFIG_HOME"], name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def start(self) -> None:
        """:raises OSError: if PipeWire or WirePlumber cannot be started (e.g. not installed)."""
        for program in ("pipewire", "wireplumber"):
            if shutil.which(program) is None:
                raise OSError(errno.ENOENT, f"{program} is not installed")
        shutil.rmtree(self._env["XDG_RUNTIME_DIR"], ignore_errors=True)
        os.makedirs(self._env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
        self._write_config()
        self.reader.open()
        try:
            for program in ("pipewire", "wireplumber"):
                self._processes.append(
                    self._popen([program], env=self._env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                )
                time.sleep(0.3)  # WirePlumber connects to PipeWire
        except OSError:
            self.stop()
            raise
        self._logger.info("PipeWire for Spotify started")

    def stop(self) -> None:
        self.reader.close()
        for process in reversed(self._processes):
            if not _end(process):
                self._logger.error("%s does not end (pid %s) - left behind", process.args, process.pid)
        self._processes = []
