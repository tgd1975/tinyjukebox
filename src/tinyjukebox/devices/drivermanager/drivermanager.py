"""DriverManager - runs several drivers in one process."""

import logging
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

import tinyjukebox
from tinyjukebox.core.microservice import AbstractMicroservice
from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.ui.tools import get_yes_no, more, parse
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.drivermanager.driverfactory import DriverFactory
from tinyjukebox.devices.schema.drivermanager import get_schema

__all__ = ["DriverManager"]


class DriverManager(AbstractMicroservice):
    """Creates all active drivers of the config section ``drivers`` and starts/stops them together.

    * All drivers share the pubsub client and the logger of the manager.
    * The command line offers ``list_drivers``, ``start_driver``, ``stop_driver``, ``restart_driver``, ``reload_driver``
      and the driver specific commands with the driver id as prefix (e.g. ``rfid_gpio_state``).
    """

    _version: ClassVar[str] = tinyjukebox.__version__

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
    ) -> None:
        """The command line of the manager is offered by the owner (see :attr:`ui_commands`)."""
        super().__init__(config, "drivers", pubsub_client, logger, logger_name=type(self).__name__, no_gui=True)
        self._driver_factory = DriverFactory(self._pubsub_client, self._logger)
        self._drivers = self._driver_factory.create_drivers(config)

        for driver_id, driver in self._drivers.items():
            for name, command in driver.driver_ui_commands.items():
                self._add_ui_command(f"{driver_id}_{name}", command)
        self._add_ui_command("list_drivers", self._cmd_list_drivers)
        self._add_ui_command("reload_driver", self._cmd_reload_driver)
        self._add_ui_command("stop_driver", self._cmd_stop_driver)
        self._add_ui_command("start_driver", self._cmd_start_driver)
        self._add_ui_command("restart_driver", self._cmd_restart_driver)

    @property
    def drivers(self) -> dict[str, ADriver]:
        """``{driver id: driver}`` of all active drivers."""
        return self._drivers

    # --- life cycle ---------------------------------------------------------------------------------------------

    def _start(self) -> None:
        for driver in self._drivers.values():
            driver.start()
        self._logger.warning("DriverManager started")

    def _stop(self) -> None:
        # also after a failed start: only the started drivers, and a failing driver does not keep the others running
        for driver in reversed(self._drivers.values()):
            if driver.is_stopped:
                continue
            try:
                driver.stop()
            except Exception:
                self._logger.exception("DriverManager - stopping %s failed", driver.get_short_info())
        self._logger.warning("DriverManager stopped")

    def restart_driver(self, driver_id: str) -> None:
        driver = self._drivers[driver_id]
        driver.stop()
        driver.start()

    def reload_driver(self, driver_id: str) -> None:
        """Replace the driver with a new instance (e.g. after the hardware has been reconnected)."""
        self._driver_factory.reload_driver(self._drivers, driver_id)

    # --- console ------------------------------------------------------------------------------------------------

    def _get_id_from_arg(self, arg: str) -> str | None:
        args = parse(arg)
        if len(args) != 1:
            print("missing mandatory parameter id (see 'list_drivers').")
            return None
        if args[0] not in self._drivers:
            print(f"unknown id '{args[0]}'")
            return None
        return args[0]

    def _confirmed_driver_action(
        self, arg: str, verb: str, must_run: bool | None, action: Callable[[str], None]
    ) -> None:
        """Ask for confirmation and call ``action(driver_id)``; ``must_run``: required state (None = any)."""
        driver_id = self._get_id_from_arg(arg)
        if driver_id is None:
            return
        driver = self._drivers[driver_id]
        if must_run is not None and driver.is_stopped == must_run:
            print(f"[{driver_id}] is {'not' if must_run else 'already'} running")
            return
        print(f"{verb} [{driver_id}]: {driver.get_short_info()}")
        if get_yes_no() == "y":
            action(driver_id)

    def _cmd_stop_driver(self, arg: str) -> None:
        """stop_driver id - stops the driver (id from 'list_drivers'): STOP_DRIVER id"""
        self._confirmed_driver_action(arg, "stopping", True, lambda driver_id: self._drivers[driver_id].stop())

    def _cmd_start_driver(self, arg: str) -> None:
        """start_driver id - starts the driver (id from 'list_drivers'): START_DRIVER id"""
        self._confirmed_driver_action(arg, "starting", False, lambda driver_id: self._drivers[driver_id].start())

    def _cmd_restart_driver(self, arg: str) -> None:
        """restart_driver id - stops and starts the driver (id from 'list_drivers'): RESTART_DRIVER id"""
        self._confirmed_driver_action(arg, "restarting", True, self.restart_driver)

    def _cmd_reload_driver(self, arg: str) -> None:
        """reload_driver id - replaces the driver with a new instance (id from 'list_drivers'): RELOAD_DRIVER id"""
        self._confirmed_driver_action(arg, "reloading", None, self.reload_driver)

    def _cmd_list_drivers(self, arg: str) -> None:
        """list_drivers - list all drivers: LIST_DRIVERS"""
        more("".join(f"{driver_id}: {driver.get_short_info()}\n" for driver_id, driver in self._drivers.items()))

    # --- schema -------------------------------------------------------------------------------------------------------

    @classmethod
    def _get_description(cls) -> str:
        return "Device drivers of the box."

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
