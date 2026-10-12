"""Base class of the components of the box (TinyJukebox, DriverManager, drivers): config, logger, life cycle."""

import argparse
import logging
import pprint
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import tinyjukebox.core.schema.microservice
from tinyjukebox.core import config as core_config
from tinyjukebox.core.logging import add_stdout_handler, create_logger
from tinyjukebox.core.pubsub import LocalQueue, PubSubClient
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.core.ui import UI, Command
from tinyjukebox.core.ui.tools import more

__all__ = ["AbstractMicroservice"]


class AbstractMicroservice:
    """Base class of the components of the box.

    Takes care of the config, the pubsub client, the logger and the interactive command line, and provides the life
    cycle (:meth:`start`, :meth:`stop`, :meth:`run`).

    Subclasses implement the hooks

    * :meth:`_start` / :meth:`_stop` - start/stop the service specific parts,
    * :meth:`_get_schema` (and optionally :meth:`_get_schema_definitions`) - JSON schema of their config section,
    * :meth:`_get_description` - one line description for ``--help`` and the command line,

    and set the class attribute ``_version``. Internal loops must end as soon as ``_stop_service`` is set.

    ``_is_started`` / ``_is_stopped`` reflect the state; both are cleared while starting/stopping.
    """

    _version: ClassVar[str] = "0.0.0"

    def __init__(
        self,
        config: Mapping[str, Any],
        config_class_root_node_name: str,
        pubsub_client: PubSubClient | None = None,
        logger: logging.Logger | None = None,
        logger_name: str | None = None,
        stdout_log_level: str | None = "WARNING",
        no_gui: bool | None = False,
    ) -> None:
        """
        :param config: complete config (all sections).
        :param config_class_root_node_name: name of the config section of this service.
        :param pubsub_client: shared pubsub client; if ``None``, a :class:`LocalQueue` is created and
            connected/disconnected by this service.
        :param logger: parent logger; if ``None``, a logger is created from section ``logger``.
        :param logger_name: name of the (child) logger (default: module name).
        :param stdout_log_level: level of the console log handler (only if ``logger`` is ``None``); empty = quiet.
        :param no_gui: if true, no interactive command line is started.
        """
        self._config: Any = config[config_class_root_node_name]
        self._full_config = config
        logger_name = logger_name or __name__

        if logger is None:
            self._logger = create_logger(config["logger"], logger_name)
            add_stdout_handler("WARNING" if stdout_log_level is None else stdout_log_level)
        else:
            self._logger = logger.getChild(logger_name)

        self._logger.info("%s.__init__ - initializing", type(self).__name__)
        self._logger.debug("%s.__init__ - config: %s", type(self).__name__, self._config)

        self._is_stopped = threading.Event()
        self._is_stopped.set()
        self._is_started = threading.Event()
        self._stop_service = threading.Event()
        self._startstop_lock = threading.Lock()

        self._manage_ui = not no_gui
        self._UI: UI | None = None
        self._ui_commands: dict[str, Command] = {}

        self._pubsub_client: PubSubClient
        if pubsub_client is None:
            self._pubsub_client = LocalQueue(self._logger)
            self._stop_pubsub_client = True
            self._add_ui_command("pubsub_stats", self._cmd_pubsub_stats)
            self._add_ui_command("show_config", self._cmd_show_config)
        else:
            self._pubsub_client = pubsub_client
            self._stop_pubsub_client = False

        self._add_ui_command("debug_threads", self._cmd_debug_threads)
        self._logger.info("%s.__init__ - done", type(self).__name__)

    # --- public properties --------------------------------------------------------------------------------------

    @property
    def config(self) -> Any:
        """The config section of this service."""
        return self._config

    @property
    def ui_commands(self) -> dict[str, Command]:
        """Commands for the interactive command line - another service can offer them in its own command line."""
        return dict(self._ui_commands)

    @property
    def version(self) -> str:
        return str(self._version)

    @property
    def is_started(self) -> bool:
        return self._is_started.is_set()

    def healthy(self) -> bool:
        """Whether the service does what it was started for - asked by the health check of a new version
        (:mod:`tinyjukebox.slots`)."""
        return bool(self.is_started)

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped.is_set()

    # --- life cycle ---------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Connect the pubsub client (if necessary), start the command line, call :meth:`_start`.

        If starting fails, the parts started so far are stopped (:meth:`_stop` must therefore cope with a partial
        start) before the exception is re-raised - threads left running would keep the process alive.
        """
        self._logger.info("%s - starting", type(self).__name__)
        with self._startstop_lock:
            self._is_stopped.clear()
            self._stop_service.clear()
            try:
                if not self._pubsub_client.is_connected.is_set():
                    self._pubsub_client.connect()
                self._start_ui()
                self._start()
            except BaseException:
                self._logger.error("%s - start failed - stopping the parts started so far", type(self).__name__)
                self._stop_after_failed_start()
                raise
            self._is_started.set()
        self._logger.info("%s - started", type(self).__name__)

    def _stop_after_failed_start(self) -> None:
        self._stop_service.set()
        for step in (self._stop_ui, self._stop, self._disconnect_own_client):
            try:
                step()
            except Exception:
                self._logger.exception("%s - %s after failed start failed", type(self).__name__, step.__name__)
        self._is_stopped.set()

    def _disconnect_own_client(self) -> None:
        if self._stop_pubsub_client:
            self._pubsub_client.disconnect()

    def stop(self) -> None:
        """Set ``_stop_service``, stop the command line, call :meth:`_stop` and - if owned - stop the pubsub client."""
        self._logger.info("%s - stopping", type(self).__name__)
        with self._startstop_lock:
            self._is_started.clear()
            self._stop_service.set()
            self._stop_ui()
            self._stop()
            self._disconnect_own_client()
            self._is_stopped.set()
        self._logger.info("%s - stopped", type(self).__name__)

    def asyncstart(self) -> None:
        """Start the service in a separate thread and return immediately."""
        LoggerThread(target=self.start, name=f"{type(self).__name__}.asyncstart", logger=self._logger).start()

    def asyncstop(self) -> None:
        """Stop the service in a separate thread and return immediately (e.g. from within a message handler)."""
        LoggerThread(target=self.stop, name=f"{type(self).__name__}.asyncstop", logger=self._logger).start()

    def run(self) -> None:
        """Start the service and block until it has been stopped (``Ctrl-C`` stops it).

        The interrupt may arrive at any moment, also while the service is still starting: a start that it breaks
        off has cleaned up already (:meth:`start`); one that it hits at its very end has not, and is stopped here.
        Otherwise the threads of the service would keep the process alive."""
        try:
            self.start()
            while not self._is_stopped.wait(0.25):  # timeout keeps the main thread responsive for Ctrl-C
                pass
        except KeyboardInterrupt:
            self._logger.info("KeyboardInterrupt")
            if not self._is_stopped.is_set():
                self.stop()

    # --- hooks for subclasses -----------------------------------------------------------------------------------

    def _start(self) -> None:
        """Start the service specific parts - to be implemented by subclasses."""
        raise NotImplementedError

    def _stop(self) -> None:
        """Stop the service specific parts - to be implemented by subclasses."""
        raise NotImplementedError

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        """JSON schema(s) of the service specific config section(s): ``{section_name: schema}``."""
        raise NotImplementedError

    @classmethod
    def _get_schema_definitions(cls) -> dict[str, Any]:
        """Shared schema definitions (referenced via ``#/definitions/<name>``); none by default."""
        return {}

    @classmethod
    def _get_description(cls) -> str:
        """Short description of the service (command line help)."""
        raise NotImplementedError

    # --- schema / config / command line -------------------------------------------------------------------------

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Complete JSON schema of a config file for this service."""
        return tinyjukebox.core.schema.microservice.get_schema(cls._get_schema(), cls._get_schema_definitions())

    @classmethod
    def dump_schema(cls, filename: str | Path) -> None:
        """Write :meth:`get_schema` to ``filename`` (only if the content changed)."""
        if tinyjukebox.core.schema.microservice.dump_schema(cls.get_schema(), filename):
            print(f"updated {filename} to latest schema.")

    @classmethod
    def _argument_parser(cls) -> argparse.ArgumentParser:
        """Command line arguments; subclasses may extend the parser."""
        parser = argparse.ArgumentParser(description=cls._get_description())
        parser.add_argument("-c", "--config", type=str, help="yaml config file", required=True)
        verbosity = parser.add_mutually_exclusive_group()
        verbosity.add_argument("-q", "--quiet", help="suppress all console output", action="store_true")
        verbosity.add_argument("-v", "--verbose", help="more console output", action="store_true")
        parser.add_argument("--no_gui", help="do not start the interactive command line", action="store_true")
        parser.add_argument("--version", action="version", version=f"%(prog)s {cls._version}")
        return parser

    @classmethod
    def _read_config(cls, config_filename: str | Path) -> dict[str, Any]:
        """Read the config file and validate it against :meth:`get_schema`.

        :raises ValueError: if the config is invalid.
        """
        config = core_config.read_config(config_filename)
        validation_result = core_config.validate_config(config, cls.get_schema())
        if validation_result:
            raise ValueError(f"Validation of config file failed: {validation_result}")
        return config

    # --- interactive command line -------------------------------------------------------------------------------

    def _add_ui_command(self, name: str, function: Command) -> None:
        """Register a command for the interactive command line (``do_<name>``); the docstring is its help text."""
        if name in self._ui_commands:
            self._logger.info("_add_ui_command - command '%s' already defined", name)
            return
        self._ui_commands[name] = function
        if self._UI is not None:
            self._UI.add_command(name, function)

    def _start_ui(self) -> None:
        if not self._manage_ui:
            return
        name = type(self).__name__
        intro = f"{name} v{self._version}\n{self._get_description()}\n   Type help or ? to list commands.\n"
        self._UI = UI(intro, f"({name.lower()}) ", self.stop, self._stop_service, self._logger, self._ui_commands)
        self._UI.start()

    def _stop_ui(self) -> None:
        if self._UI is not None:
            self._UI.stop()
            self._UI = None

    def _cmd_debug_threads(self, args: str) -> None:
        """debug_threads - list all active threads: DEBUG_THREADS"""
        text = f"active threads ({threading.active_count()}):\n"
        for thread in threading.enumerate():
            text += f" - {thread.name} (alive: {thread.is_alive()}, daemon: {thread.daemon})\n"
        more(text)

    def _cmd_pubsub_stats(self, args: str) -> None:
        """pubsub_stats - show the pubsub client statistics: PUBSUB_STATS"""
        more(pprint.pformat(self._pubsub_client.get_stats(), indent=2))

    def _cmd_show_config(self, args: str) -> None:
        """show_config - show the current config (credentials masked): SHOW_CONFIG"""
        masked = core_config.lowercase_keys(self._full_config)
        core_config.mask_entries(masked)
        more(pprint.pformat(masked, indent=2))
