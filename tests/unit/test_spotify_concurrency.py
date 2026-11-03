"""Spotify and the threads of the box (docs/developer/architecture.md, sections 4.1 and 10): whoever ends an aplay
that hangs is not held up by it."""

import subprocess
import sys
import threading
import time
from typing import Any, cast

from tests.support import wait_for
from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.spotify import sound as sound_module
from tinyjukebox.spotify.soloist import Soloist
from tinyjukebox.spotify.sound import PipeReader

# --- an aplay or a Soloist that hangs ---------------------------------------------------------------------------------

HUNG_APLAY = [sys.executable, "-c", "import time; time.sleep(60)"]
"""An aplay that hangs in its driver: it never takes its input."""


def test_ending_an_aplay_that_hangs_is_not_held_up_by_its_full_pipe(tmp_path, logger):
    aplays: list[subprocess.Popen[bytes]] = []

    def popen(command, **kwargs):
        aplays.append(subprocess.Popen(HUNG_APLAY, **kwargs))
        return aplays[-1]

    reader = PipeReader(str(tmp_path / "pipe"), AudioOutput(), logger, popen=popen)
    reader.open()
    writing = threading.Event()

    def write():
        with open(reader.pipe, "wb", buffering=0) as writer:
            writing.set()
            while True:
                try:
                    writer.write(b"\x01" * 4096)
                except OSError:
                    return

    writer = threading.Thread(target=write, name="test.writer", daemon=True)
    try:
        reader.sound()
        writer.start()
        assert writing.wait(5)
        assert wait_for(lambda: aplays, timeout=5)
        time.sleep(0.5)  # the pipe to aplay is full: the reader waits in its write
        restart = threading.Thread(target=reader.restart_output, name="test.restart", daemon=True)
        started = time.monotonic()
        restart.start()
        restart.join(10)
        assert not restart.is_alive()
        assert time.monotonic() - started < 3
        assert aplays[0].poll() is not None
    finally:
        reader.close()
        for aplay in aplays:
            if aplay.poll() is None:
                aplay.kill()
                aplay.wait()
        writer.join(5)
    assert all(aplay.returncode is not None for aplay in aplays)


class Unending:
    """A process that ends neither on SIGTERM nor on SIGKILL - it hangs in a driver."""

    def __init__(self):
        self.calls: list[str] = []
        self.stdin = self
        self.stdout = self.stderr = None
        self.pid = 4711
        self.args = ["aplay"]
        self._released = threading.Event()
        self.written = 0

    def poll(self):
        return 0 if self._released.is_set() else None

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout=None):
        if timeout is None:  # the thread that watches it waits for its end
            self._released.wait()
            return 0
        raise subprocess.TimeoutExpired("aplay", timeout)

    def write(self, chunk):
        self.written += len(chunk)
        return len(chunk)

    def close(self):
        self.calls.append("close")

    def release(self):
        self._released.set()


def test_an_aplay_that_never_ends_is_left_behind(tmp_path, logger, monkeypatch, caplog):
    monkeypatch.setattr(sound_module, "END_TIMEOUT", 0.05)
    aplay = Unending()
    reader = PipeReader(str(tmp_path / "pipe"), AudioOutput(), logger, popen=lambda *a, **k: cast(Any, aplay))
    reader.open()
    try:
        reader.sound()
        with open(reader.pipe, "wb", buffering=0) as writer:
            writer.write(b"\x01" * 100)
            assert wait_for(lambda: aplay.written, timeout=3)
        started = time.monotonic()
        reader.restart_output()
        assert time.monotonic() - started < 1
    finally:
        reader.close()
        aplay.release()
    assert aplay.calls[:3] == ["terminate", "kill", "close"]  # its input is closed only after the signals
    assert "aplay does not end" in caplog.text


def test_a_soloist_that_never_ends_is_left_behind(logger, caplog):
    process = Unending()
    soloist = Soloist("/s/bin/soloist", "KEY", "box", "/s/data", "/s/cache", logger,
                      popen=lambda *a, **k: cast(Any, process))  # fmt: skip
    soloist.start()
    try:
        started = time.monotonic()
        soloist.stop(timeout=0.05)
        assert time.monotonic() - started < 3
    finally:
        process.release()
    assert "kill" in process.calls
    assert "Soloist does not end" in caplog.text
