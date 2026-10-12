"""Logger setup.

The configuration section ``logger`` (see :mod:`tinyjukebox.core.schema.logger`) defines the log level and the log file
of a service. :func:`create_logger` attaches the file handler to the root logger once; all loggers of a process thus
write into the same file. Components create child loggers with :func:`get_child`.
"""

import logging
import logging.handlers
import os
import sys
from collections.abc import Mapping
from typing import Any

__all__ = [
    "LOG_FORMAT",
    "add_stdout_handler",
    "create_logger",
    "get_child",
    "get_level_name",
    "get_log_level",
]

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_FILE_HANDLER_NAME = "tinyjukebox-file"
_STDOUT_HANDLER_NAME = "tinyjukebox-stdout"
_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")


def get_log_level(name: str) -> int:
    """Convert a level name (case-insensitive, e.g. ``"info"``) into the numeric logging level.

    :raises ValueError: for unknown level names.
    """
    upper = name.upper()
    if upper not in _LEVELS:
        raise ValueError(f"unknown value for logger level ('{name}').")
    level: int = logging.getLevelName(upper)
    return level


def get_level_name(level: int) -> str:
    """Convert a numeric logging level into its name.

    :raises ValueError: for levels that are not one of the standard levels.
    """
    name = logging.getLevelName(level)
    if name not in _LEVELS:
        raise ValueError(f"unknown value for logger level ('{level}').")
    return str(name)


def get_child(logger: logging.Logger, logger_name: str, config: Mapping[str, Any] | None = None) -> logging.Logger:
    """Create a child logger; if ``config`` contains ``log-level``, that level is applied to the child."""
    child = logger.getChild(logger_name)
    if config is not None and "log-level" in config:
        child.setLevel(get_log_level(config["log-level"]))
    return child


def _has_handler(logger: logging.Logger, name: str) -> bool:
    return any(handler.name == name for handler in logger.handlers)


def create_logger(config: Mapping[str, Any], logger_name: str) -> logging.Logger:
    """Create the logger ``logger_name`` and - once per process - a file handler at the root logger.

    :param config: ``logger`` config section with ``log-level``, ``log-file`` (may begin with ``~``) and the optional
        ``log-rotation`` (``maxbytes``, ``backupcount``).
    :param logger_name: name of the logger.
    """
    level = get_log_level(config["log-level"])
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    root = logging.getLogger()
    if not _has_handler(root, _FILE_HANDLER_NAME):
        rotation = config.get("log-rotation")
        log_file = os.path.expanduser(config["log-file"])
        handler: logging.Handler
        if rotation:
            handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=int(rotation["maxbytes"]),
                backupCount=int(rotation["backupcount"]),
                encoding="utf-8",
            )
        else:
            handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.name = _FILE_HANDLER_NAME
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)

    return logger


def add_stdout_handler(level: str | None) -> None:
    """Add a console handler (``sys.stdout``, message only) to the root logger.

    Nothing happens if ``level`` is empty/``None`` (quiet mode) or if the handler has been added before.
    """
    if not level:
        return
    root = logging.getLogger()
    if _has_handler(root, _STDOUT_HANDLER_NAME):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.name = _STDOUT_HANDLER_NAME
    handler.setLevel(get_log_level(level))
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
