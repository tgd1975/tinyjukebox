"""ADriver: common config entries, life cycle, console commands."""

import logging
from typing import Any

import pytest

from tests.devices.support import driver_config
from tests.support import SyncPubSub
from tinyjukebox.devices.baseclasses.adriver import ADriver


class Dummy(ADriver):
    def __init__(self, config: dict[str, Any], pubsub_client: Any, logger: Any) -> None:
        super().__init__(config, pubsub_client, logger)
        self.calls: list[str] = []
        self._add_ui_command("hello", self._cmd_hello)

    def _cmd_hello(self, args: str) -> None:
        """hello - says hello: HELLO"""

    def _driver_start(self) -> None:
        self.calls.append("start")

    def _driver_stop(self) -> None:
        self.calls.append("stop")

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return {"driver": {"type": "object"}}


DUMMY = {"type": "dummy", "name": "dummy1"}


def create(pubsub: SyncPubSub, logger: logging.Logger, log_file: str, **driver: Any) -> Dummy:
    config = driver_config({**DUMMY, **driver}, log_file)
    return Dummy(config, pubsub, logger)


def test_common_entries(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    driver = create(pubsub, logger, log_file)
    assert driver.name == driver.get_name() == "dummy1"
    assert driver.driver_type == "Dummy"
    assert driver.config["name"] == "dummy1"


def test_optional_entries(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    driver = Dummy(driver_config({"type": "DUMMY"}, log_file), pubsub, logger)
    assert driver.name == "Dummy"


def test_type_mismatch(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    with pytest.raises(TypeError, match="DUMMY"):
        create(pubsub, logger, log_file, type="input")


def test_life_cycle(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    driver = create(pubsub, logger, log_file)
    assert driver.is_stopped
    assert "is_stopped:True" in driver.get_short_info()
    driver.start()
    assert not driver.is_stopped
    assert driver.is_started
    assert "is_stopped:False" in driver.get_short_info()
    driver.stop()
    driver.start()  # restartable
    driver.stop()
    assert driver.calls == ["start", "stop", "start", "stop"]
    assert pubsub.is_connected.is_set()  # shared client is not disconnected by the driver


def test_driver_ui_commands(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    driver = create(pubsub, logger, log_file)
    assert set(driver.driver_ui_commands) == {"hello"}
    assert "debug_threads" in driver._ui_commands  # generic commands of the component


def test_publish_value(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    driver = create(pubsub, logger, log_file)
    payload = ("a", 1)
    driver._publish_value("/out", payload)
    driver._publish_value("/out", 1.5)
    assert pubsub.messages("/out") == [payload, 1.5]
    assert pubsub.messages("/out")[0] is payload  # passed on unchanged


def test_description_names_the_driver() -> None:
    assert "'Dummy'" in Dummy._get_description()


def test_abstract_methods_must_be_implemented(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    class Incomplete(ADriver):
        def _driver_start(self) -> None:
            pass

    with pytest.raises(TypeError, match="_driver_stop"):
        Incomplete(driver_config({"type": "incomplete"}, log_file), pubsub, logger)  # type: ignore[abstract]
