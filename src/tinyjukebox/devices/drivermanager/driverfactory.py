"""Creates driver instances from the config section ``drivers``."""

import logging
from collections.abc import Mapping
from typing import Any

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.drivers import get_driver_class

__all__ = ["DriverFactory"]


class DriverFactory:
    """Creates drivers that share one pubsub client and one logger."""

    def __init__(self, pubsub_client: PubSubClient, logger: logging.Logger) -> None:
        self._pubsub_client = pubsub_client
        self._logger = logger
        self._factory_logger = get_child(logger, type(self).__name__)
        self._config: Mapping[str, Any] = {}

    def create_drivers(self, config: Mapping[str, Any]) -> dict[str, ADriver]:
        """Create all active drivers of ``config["drivers"]``: ``{unique id: driver}``.

        :param config: the complete config.
        """
        self._config = config
        drivers: dict[str, ADriver] = {}
        for driver_config in config["drivers"]:
            if not driver_config["active"]:
                continue
            driver = self.create_driver(driver_config)
            driver_id = self.get_unique_name(drivers, driver.name)
            drivers[driver_id] = driver
            self._factory_logger.info(" - added driver '[%s]: %s.%s'", driver_id, driver.driver_type, driver.name)
        return drivers

    @staticmethod
    def get_unique_name(drivers: Mapping[str, Any], name: str) -> str:
        """``name`` in lower case without spaces; ``_0``, ``_1`` ... is appended if it is already taken."""
        prefix = name.replace(" ", "").lower()
        unique = prefix
        counter = 0
        while unique in drivers:
            unique = f"{prefix}_{counter}"
            counter += 1
        return unique

    def create_driver(self, driver_config: Mapping[str, Any]) -> ADriver:
        """Create the driver for one entry of the ``drivers`` list.

        :raises ValueError: for unknown driver types.
        """
        driver_type = driver_config["type"]
        try:
            driver_class = get_driver_class(driver_type)
        except ValueError:
            self._factory_logger.error("unknown driver type '%s'.", driver_type)
            raise
        config = {**self._config, "driver": driver_config}
        driver: ADriver = driver_class(config, self._pubsub_client, self._logger)
        return driver

    def reload_driver(self, drivers: dict[str, ADriver], driver_id: str) -> None:
        """Stop the driver, replace it with a new instance created from its config and start the new one."""
        driver = drivers[driver_id]
        self._factory_logger.info("reloading driver '%s' - %s", driver_id, driver.get_short_info())
        if not driver.is_stopped:
            driver.stop()
        new_driver = self.create_driver(driver.config)
        drivers[driver_id] = new_driver
        new_driver.start()
        self._factory_logger.info("reloading driver '%s' - done", driver_id)
