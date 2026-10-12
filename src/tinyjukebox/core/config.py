"""Reading, validating and masking of YAML configuration files.

A configuration is a YAML file that is converted into plain ``dict``/``list`` structures. All keys are converted to
lower case.
"""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import jsonschema
import yaml

__all__ = [
    "ConfigLoader",
    "load_yaml",
    "lowercase_keys",
    "mask_entries",
    "read_config",
    "validate_config",
]

_DEFAULT_MASK_PATTERNS = ("credentials", "password", "user")
_BOOL_TAG = "tag:yaml.org,2002:bool"

_BaseLoader: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class ConfigLoader(_BaseLoader):  # type: ignore[valid-type,misc]
    """Safe YAML loader with YAML 1.2 boolean semantics.

    YAML 1.1 (and thus PyYAML) converts ``on/off/yes/no`` into booleans. Configuration values like ``state: on``
    must stay strings, therefore only ``true``/``false`` are resolved to booleans. The adjustment is local to this
    loader class and does not alter PyYAML's global state.
    """


ConfigLoader.yaml_implicit_resolvers = {
    first_char: [resolver for resolver in resolvers if resolver[0] != _BOOL_TAG]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ConfigLoader.add_implicit_resolver(_BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_yaml(filename: str | Path) -> Any:
    """Load a YAML file with :class:`ConfigLoader`."""
    with open(filename, encoding="utf-8") as f:
        return yaml.load(f, Loader=ConfigLoader)  # noqa: S506 - ConfigLoader is a SafeLoader


def lowercase_keys(data: Any) -> Any:
    """Return a deep copy of ``data`` in which all string keys of all (nested) dicts are lower case."""
    if isinstance(data, dict):
        return {(k.lower() if isinstance(k, str) else k): lowercase_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [lowercase_keys(entry) for entry in data]
    return data


def read_config(config_filename: str | Path) -> dict[str, Any]:
    """Read a YAML config file and lower-case all keys.

    :raises FileNotFoundError: if the config file does not exist.
    """
    config_file = Path(config_filename).expanduser()
    if not config_file.is_file():
        raise FileNotFoundError(f"config file '{config_filename}' not found.")

    config: dict[str, Any] = lowercase_keys(load_yaml(config_file))
    if not isinstance(config, dict):
        raise ValueError(f"config file '{config_filename}' must contain a mapping at root level.")
    return config


def validate_config(config: Any, schema: dict[str, Any]) -> str | None:
    """Validate ``config`` against the JSON ``schema``.

    :return: ``None`` if the config is valid, otherwise a description of the first validation error.
    """
    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        return f"{e.message} (at '{path}')"
    return None


def mask_entries(config: Any, patterns: Iterable[str] = _DEFAULT_MASK_PATTERNS, mask_string: str = "*****") -> None:
    """Replace (in place) all string values whose key contains one of ``patterns`` with ``mask_string``."""
    patterns = tuple(patterns)
    if isinstance(config, dict):
        for key, value in config.items():
            if isinstance(value, (dict, list)):
                mask_entries(value, patterns, mask_string)
            elif isinstance(value, str) and isinstance(key, str) and any(p in key for p in patterns):
                config[key] = mask_string
    elif isinstance(config, list):
        for entry in config:
            mask_entries(entry, patterns, mask_string)
