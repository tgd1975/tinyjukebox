import logging
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support import NULL_OUTPUT, FakePubSub, FakeRGBLed, ManualClock, create_library, wait_for
from tinyjukebox import control
from tinyjukebox.albumplayer import simpleplayer
from tinyjukebox.core.pubsub import LocalQueue
from tinyjukebox.spotify.oembed import Found


@pytest.fixture(autouse=True)
def _reset_root_handlers() -> Iterator[None]:
    """create_logger attaches handlers to the root logger once per process - remove them after each test."""
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _no_leaked_threads() -> Iterator[None]:
    """Every test must stop the threads it started - a leaked non-daemon thread blocks the shutdown of the service."""
    before = set(threading.enumerate())

    def leaked() -> list[str]:
        return [t.name for t in threading.enumerate() if t not in before and t.is_alive() and not t.daemon]

    yield
    if not wait_for(lambda: not leaked(), timeout=3):
        pytest.fail(f"leaked non-daemon threads: {leaked()}")


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """A leaked non-daemon thread would keep the test run alive forever (Python waits for it at exit, CI until its
    timeout) - the leaking test has failed already (``_no_leaked_threads``), the run ends here."""
    leaked = [
        t.name for t in threading.enumerate() if t is not threading.main_thread() and t.is_alive() and not t.daemon
    ]
    if leaked:
        sys.stdout.flush()
        print(f"non-daemon threads still running - ending the test run: {leaked}", file=sys.stderr, flush=True)
        os._exit(1)


@pytest.fixture(autouse=True)
def _silent_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """aplay is replaced by a real time null sink - tests never play sound and run without a sound card."""
    monkeypatch.setattr(
        simpleplayer, "output_command", lambda null_output=False, buffer_time=None, device=None: list(NULL_OUTPUT)
    )


@pytest.fixture(autouse=True)
def _no_spotify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spotify is never asked for name and cover: tests run without the internet."""
    monkeypatch.setattr(control, "look_up", lambda uri, logger: Found())


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("tests")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def pubsub(logger: logging.Logger) -> FakePubSub:
    return FakePubSub(logger)


@pytest.fixture
def local_queue(logger: logging.Logger) -> Iterator[LocalQueue]:
    client = LocalQueue(logger)
    client.connect()
    yield client
    client.disconnect()


@pytest.fixture
def rgbled() -> FakeRGBLed:
    return FakeRGBLed()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def library(tmp_path: Path, logger: logging.Logger) -> SimpleNamespace:
    """Tags, targets and activities in ``tmp_path`` (see :func:`tests.support.create_library`)."""
    return create_library(tmp_path, logger)
