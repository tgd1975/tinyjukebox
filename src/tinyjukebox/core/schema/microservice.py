"""Schema of a complete microservice config file."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tinyjukebox.core.schema import logger

__all__ = ["dump_schema", "get_schema"]

JSON_SCHEMA_DRAFT = "http://json-schema.org/draft-07/schema#"


def get_schema(sub_schema: Mapping[str, Any], definitions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Combine the service specific ``sub_schema`` with the section ``logger``.

    :param sub_schema: mapping of root keys to their schemas; all of them are required.
    :param definitions: shared schema definitions (referenced via ``#/definitions/<name>``).
    """
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "title": "Configuration of the Tiny Jukebox box.",
        "type": "object",
        "properties": dict(sub_schema),
        "required": list(sub_schema),
        "definitions": dict(definitions or {}),
    }
    key, sub = logger.get_schema()
    schema["properties"][key] = sub
    schema["required"].append(key)
    return schema


def dump_schema(schema: Mapping[str, Any], filename: str | Path) -> bool:
    """Write ``schema`` as JSON to ``filename`` if the content differs from the file.

    :return: ``True`` if the file has been (re)written.
    """
    path = Path(filename)
    new_content = json.dumps(schema, indent=4)
    try:
        if path.read_text(encoding="utf-8") == new_content:
            return False
    except OSError:
        pass
    path.write_text(new_content, encoding="utf-8")
    return True
