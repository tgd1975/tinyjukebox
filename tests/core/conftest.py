import logging
from pathlib import Path
from typing import Any

import pytest

from tests.core.support import service_config
from tests.support import SyncPubSub


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("tests.core")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def sync_pubsub(logger: logging.Logger) -> SyncPubSub:
    client = SyncPubSub(logger)
    client.connect()
    return client


@pytest.fixture
def log_file(tmp_path: Path) -> str:
    return str(tmp_path / "core.log")


@pytest.fixture
def config(log_file: str) -> dict[str, Any]:
    return service_config(log_file)
