import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.devices import fakes
from tests.support import SyncPubSub

fakes.install()  # before any driver module is imported


@pytest.fixture(autouse=True)
def gpio() -> Iterator[fakes.FakeGPIO]:
    fakes.GPIO.reset()
    yield fakes.GPIO
    fakes.GPIO.reset()


@pytest.fixture
def rfid_reader(tmp_path: Path) -> Iterator[fakes.FakeRfidReader]:
    reader = fakes.FakeRfidReader(tmp_path)
    yield reader
    reader.end()


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("tests.devices")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def pubsub(logger: logging.Logger) -> SyncPubSub:
    client = SyncPubSub(logger)
    client.connect()
    return client


@pytest.fixture
def log_file(tmp_path: Path) -> str:
    return str(tmp_path / "devices.log")
