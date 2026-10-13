"""Base class of all drivers."""

import logging
from abc import ABCMeta, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

import tinyjukebox
from tinyjukebox.core.microservice import AbstractMicroservice
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.ui import Command

__all__ = ["ADriver"]


class ADriver(AbstractMicroservice, metaclass=ABCMeta):
    """Base class of all drivers: a component with the config section ``driver``.

    * applies the common driver config entries ``type`` and ``name``; topics and payloads are defined in
      :mod:`tinyjukebox.messages`,
    * start/stop via :meth:`_driver_start` / :meth:`_driver_stop`,
    * drivers are created and started by the :class:`~tinyjukebox.devices.drivermanager.drivermanager.DriverManager`
      (config section ``drivers``); they use its pubsub client and logger and have no command line of their own.

    Driver specific console commands are added to ``self._ui_commands`` (or via :meth:`_add_ui_command`) within
    ``__init__``; :attr:`driver_ui_commands` provides them to the DriverManager.
    """

    _version: ClassVar[str] = tinyjukebox.__version__

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
        logger_name: str | None = None,
    ) -> None:
        super().__init__(config, "driver", pubsub_client, logger, logger_name or type(self).__name__, no_gui=True)

        self._type = type(self).__name__
        if self._type.upper() != self._config["type"].upper():
            message = f"Type of class ({self._type.upper()}) is different than type of config "
            message += f"({self._config['type'].upper()})."
            self._logger.error("ADriver.__init__ - %s", message)
            raise TypeError(message)

        self._name: str = self._config.get("name", self._type)
        self._logger = self._logger.getChild(self._name)

        self._base_ui_commands = frozenset(self._ui_commands)

    # --- public -------------------------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def driver_type(self) -> str:
        return self._type

    @property
    def driver_ui_commands(self) -> dict[str, Command]:
        """The driver specific console commands (without the generic commands of the microservice)."""
        return {name: command for name, command in self._ui_commands.items() if name not in self._base_ui_commands}

    def get_name(self) -> str:
        return self._name

    def get_short_info(self) -> str:
        return f"[{self._name}] type:{self._type} is_stopped:{self.is_stopped}"

    def state(self) -> dict[str, Any]:
        """What the driver knows about its hardware right now - the technical view of the web page shows it.
        ``type`` is the one of the config (``input``, ``rgbled``, ...); drivers add theirs with
        :meth:`_driver_state`."""
        return {
            "name": self._name,
            "type": str(self._config["type"]).lower(),
            "running": bool(self.is_started),
            **self._driver_state(),
        }

    # --- helpers for drivers ------------------------------------------------------------------------------------

    def _publish_value(self, topic: str, value: Any) -> None:
        """Publish ``value`` to ``topic`` (see :mod:`tinyjukebox.messages`)."""
        self._pubsub_client.publish(topic, value)
        self._logger.info("ADriver._publish_value - %s: %s", topic, value)

    # --- life cycle ---------------------------------------------------------------------------------------------

    def _start(self) -> None:
        self._driver_start()
        self._logger.warning("Driver '%s.%s' started", self._type, self._name)

    def _stop(self) -> None:
        self._driver_stop()
        self._logger.warning("Driver '%s.%s' stopped", self._type, self._name)

    @abstractmethod
    def _driver_start(self) -> None:
        """Set up the hardware, subscribe to topics, start threads."""

    @abstractmethod
    def _driver_stop(self) -> None:
        """Reverse of :meth:`_driver_start`."""

    def _driver_state(self) -> dict[str, Any]:
        """The driver specific part of :meth:`state` - called by any thread, also while the driver is stopped."""
        return {}

    @classmethod
    def _get_description(cls) -> str:
        return f"Device driver '{cls.__name__}'."
