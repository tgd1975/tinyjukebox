"""Threading helpers."""

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from tinyjukebox.core import unexpected

__all__ = ["LoggerThread"]


class LoggerThread(threading.Thread):
    """``threading.Thread`` that logs uncaught exceptions of its target before re-raising them.

    Exceptions in plain threads only end up on stderr. Services running as daemons should have them in the log file.
    """

    def __init__(
        self,
        group: None = None,
        target: Callable[..., object] | None = None,
        name: str | None = None,
        args: Iterable[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        *,
        daemon: bool | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(group=group, target=target, name=name, args=args, kwargs=kwargs, daemon=daemon)
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def run(self) -> None:
        try:
            super().run()
        except Exception as error:
            self._logger.exception("uncaught exception in thread '%s'", self.name)
            unexpected.report(error, f"thread {self.name}")
            raise
