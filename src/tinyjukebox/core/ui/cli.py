"""Interactive command line interface running in its own thread."""

import cmd
import contextlib
import logging
import threading
from collections.abc import Callable, Mapping

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.threads import LoggerThread

__all__ = ["UI", "Command"]

Command = Callable[[str], object]
"""A command receives the rest of the input line as argument. Its docstring is shown by ``help``."""


class UI(cmd.Cmd):
    """Command line of a service (``help`` lists all commands, ``exit``/``e``/``q`` stops the service).

    The end of the input (``Ctrl-D``) stops the service only on a terminal - without one (e.g. ``< /dev/null``) it
    ends only the command line.

    :param intro: text shown at start.
    :param prompt: prompt string.
    :param stop_service: called (in a separate thread) if the user leaves the command line.
    :param service_is_stopping: event that is set while the service is stopping (then ``stop_service`` is not
        called again).
    :param logger: parent logger.
    :param commands: additional commands - name to callable.
    """

    def __init__(
        self,
        intro: str,
        prompt: str,
        stop_service: Callable[[], object],
        service_is_stopping: threading.Event,
        logger: logging.Logger,
        commands: Mapping[str, Command] | None = None,
    ) -> None:
        super().__init__()
        self._logger = get_child(logger, type(self).__name__)
        self.intro = intro
        self.prompt = prompt
        self._stop_service = stop_service
        self._service_is_stopping = service_is_stopping
        self._closed = False  # stopped by the service: a line typed afterwards is not executed
        self._end_of_input = False
        self._thread = LoggerThread(target=self._loop, name="UI", logger=self._logger, daemon=True)
        for name, function in (commands or {}).items():
            self.add_command(name, function)

    def add_command(self, name: str, function: Command) -> None:
        """Add the command ``name``.

        :raises ValueError: if a command with this name exists already.
        :raises TypeError: if ``function`` is not callable.
        """
        if hasattr(self, "do_" + name):
            raise ValueError(f"command '{name}' already defined")
        if not callable(function):
            raise TypeError("parameter function must be callable")
        setattr(self, "do_" + name, function)

    def get_names(self) -> list[str]:
        # cmd.Cmd only inspects the class - include the commands added to this instance
        return dir(self)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """End the command line (called by the service when it stops)."""
        self._closed = True
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            # cmdloop blocks in input() until the next line; the thread is a daemon and must not block the shutdown
            # of the service
            self._thread.join(timeout=0.5)

    def _loop(self) -> None:
        with contextlib.suppress(EOFError):
            self.cmdloop()
        if self._closed or self._service_is_stopping.is_set():
            return
        if self._end_of_input and not self._interactive():
            self._logger.warning("the input has ended and is no terminal - the console ends, the service goes on")
            return
        LoggerThread(target=self._stop_service, name="UI.stop_service", logger=self._logger, daemon=True).start()

    def _interactive(self) -> bool:
        try:
            return bool(self.stdin.isatty())
        except (AttributeError, OSError, ValueError):  # no stdin, or a closed one
            return False

    def onecmd(self, line: str) -> bool:
        if self._closed:
            return True  # the service has stopped the command line meanwhile
        try:
            return bool(super().onecmd(line))
        except Exception as e:
            self._logger.exception("command '%s' failed", line)
            print(f"Caught exception: {e}")
            return False

    def emptyline(self) -> bool:
        return False  # do not repeat the last command

    def do_exit(self, arg: str) -> bool:
        """exit - stop the service: EXIT"""
        return True

    def do_EOF(self, arg: str) -> bool:  # noqa: N802 - name defined by cmd.Cmd
        """Ctrl-D - stop the service"""
        self._end_of_input = True
        return True

    do_e = do_exit
    do_q = do_exit
