"""Schema of the ``logger`` config section."""

from typing import Any

KEY = "logger"

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def get_schema() -> tuple[str, dict[str, Any]]:
    schema = {
        "description": "Logger configuration",
        "type": "object",
        "properties": {
            "log-level": {"description": "Log level to be used.", "type": "string", "enum": LOG_LEVELS},
            "log-file": {"description": "File name for the logger.", "type": "string"},
            "log-rotation": {
                "description": "Parameters for log rotation (optional).",
                "type": "object",
                "properties": {
                    "maxbytes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Rollover occurs whenever the current log file is nearly maxbytes in length.",
                    },
                    "backupcount": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "With a backupcount of 5 and a base file name of app.log, you would get "
                        "app.log, app.log.1, app.log.2, up to app.log.5.",
                    },
                },
                "required": ["maxbytes", "backupcount"],
                "additionalProperties": False,
            },
        },
        "required": ["log-level", "log-file"],
        "additionalProperties": False,
    }
    return KEY, schema
