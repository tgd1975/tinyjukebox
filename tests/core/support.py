"""Test doubles and helpers shared by unit and integration tests."""

import threading
from typing import Any

from tinyjukebox.core.microservice import AbstractMicroservice
from tinyjukebox.core.pubsub.base import PubSubClient

EXAMPLE_SCHEMA = {
    "test": {
        "description": "test configuration",
        "type": "object",
        "properties": {
            "start": {"type": "string"},
            "stop": {"type": "string"},
            "stop-me": {"type": "string"},
        },
        "required": ["start", "stop", "stop-me"],
    }
}


class ExampleService(AbstractMicroservice):
    """Publishes 'start'/'stop' on start/stop and stops itself on a message to ``stop-me``."""

    _version = "2.0"

    def __init__(
        self,
        config: dict[str, Any],
        pubsub_client: PubSubClient | None = None,
        logger: Any = None,
        stdout_log_level: str | None = None,
        no_gui: bool | None = True,
    ) -> None:
        super().__init__(
            config,
            "test",
            pubsub_client,
            logger,
            logger_name="service",
            stdout_log_level=stdout_log_level,
            no_gui=no_gui,
        )
        self.start_topic = self._config["start"]
        self.stop_topic = self._config["stop"]
        self.stop_me_topic = self._config["stop-me"]
        self.stopped_by_message = threading.Event()

    def _stop_me_handler(self, message: bytes) -> None:
        self.stopped_by_message.set()
        self.asyncstop()

    def _start(self) -> None:
        self._pubsub_client.subscribe(self.stop_me_topic, self._stop_me_handler)
        self._pubsub_client.publish(self.start_topic, "start")

    def _stop(self) -> None:
        self._pubsub_client.unsubscribe(self.stop_me_topic, self._stop_me_handler)
        self._pubsub_client.publish(self.stop_topic, "stop")

    @classmethod
    def _get_description(cls) -> str:
        return "TestService"

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return EXAMPLE_SCHEMA


def service_config(log_file: str) -> dict[str, Any]:
    """A complete, valid config for :class:`ExampleService`."""
    return {
        "logger": {"log-level": "DEBUG", "log-file": log_file},
        "test": {"start": "/test/start", "stop": "/test/stop", "stop-me": "/test/stop-me"},
    }
