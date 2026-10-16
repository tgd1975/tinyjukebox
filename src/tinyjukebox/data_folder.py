"""Starting with one folder (``--data DIR``, docs/developer/configuration.md, section 1): library and box without a
config file of their own and without a users file made by hand - for the Docker image and the package from PyPI.

Everything lies below the folder, in the folders library and box use anyway. A file ``DIR/config.yaml``, if there is
one, changes single values. What the first start needs comes from environment variables, and counts at the first
start only; afterwards the folder is the truth, and passwords are changed on the page.
"""

import copy
import getpass
import logging
import os
import secrets
import sys
from typing import Any

from tinyjukebox.core.config import read_config
from tinyjukebox.web.auth import MIN_PASSWORD_LENGTH, UsersFile, hash_password

__all__ = [
    "ADMIN_VARIABLE",
    "BOOTSTRAP_VARIABLE",
    "LIBRARY_VARIABLE",
    "SLOTS_VARIABLE",
    "box_config",
    "first_admin",
    "library_config",
    "link_library",
]

ADMIN_VARIABLE = "TINYJUKEBOX_ADMIN_PASSWORD"  # the password of the user admin of the page, at the first start
BOOTSTRAP_VARIABLE = "TINYJUKEBOX_BOOTSTRAP_PASSWORD"  # a library: the one new boxes prove; a box: the one it proves
LIBRARY_VARIABLE = "TINYJUKEBOX_LIBRARY"  # a box: the address of its library, at the first start
SLOTS_VARIABLE = "TINYJUKEBOX_SLOTS"  # a library: the folder with the slot it brings along (the Docker image)
REPOSITORY = "tgd1975/tinyjukebox"  # where a library finds new versions for its boxes
PLATFORM = "aarch64-debian-trixie"  # the platform of the boxes


def _merged(base: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


def _with_changes(folder: str, config: dict[str, Any]) -> dict[str, Any]:
    changes = os.path.join(folder, "config.yaml")
    return _merged(config, read_config(changes)) if os.path.isfile(changes) else config


def _logger(folder: str, name: str) -> dict[str, Any]:
    return {
        "log-level": "INFO",
        "log-file": os.path.join(folder, "log", f"{name}.log"),
        "log-rotation": {"maxbytes": 1048576, "backupcount": 4},
    }


def _rest_api(folder: str, port: int, name: str) -> dict[str, Any]:
    return {
        "port": port,
        "certificate": os.path.join(folder, "ssl", f"{name}.crt"),
        "private-key": os.path.join(folder, "ssl", f"{name}.key"),
        "users-file": os.path.join(folder, "ssl", "users.yaml"),
    }


def _folders(folder: str, *names: str) -> None:
    for name in names:
        os.makedirs(os.path.join(folder, name), exist_ok=True)
    os.chmod(os.path.join(folder, "ssl"), 0o700)  # private key, password hashes


def library_config(folder: str) -> dict[str, Any]:
    """The config of a library below ``folder``."""
    folder = os.path.abspath(os.path.expanduser(folder))
    _folders(folder, "data", "albums", "images", "ssl", "log")
    library: dict[str, Any] = {
        "data-folder": os.path.join(folder, "data"),
        "albums-folder": os.path.join(folder, "albums"),
        "images-folder": os.path.join(folder, "images"),
        "backups-folder": os.path.join(folder, "backups"),
        "playlist-filename": "playlist.m3u",
        "platform": PLATFORM,
        "releases": {"repository": REPOSITORY},
        "rest-api": _rest_api(folder, 8866, "library"),
    }
    if os.environ.get(BOOTSTRAP_VARIABLE):
        library["bootstrap-password"] = os.environ[BOOTSTRAP_VARIABLE]  # taken over once (library.md, section 3.8)
    if os.environ.get(SLOTS_VARIABLE):
        library["slots-folder"] = os.environ[SLOTS_VARIABLE]
    return _with_changes(folder, {"logger": _logger(folder, "library"), "library": library})


def box_config(folder: str) -> dict[str, Any]:
    """The config of a box without hardware below ``folder``, as ``deploy/config/tinyjukebox-sim.yaml`` has it."""
    folder = os.path.abspath(os.path.expanduser(folder))
    _folders(folder, "albums", "json", "ssl", "log")
    json_files = {}
    for key, name in (("tags", "tags"), ("targets", "targets"), ("activities", "activity")):
        json_files[key] = os.path.join(folder, "json", f"{name}.json")
        if not os.path.exists(json_files[key]):
            with open(json_files[key], "w", encoding="utf-8") as outfile:
                outfile.write("{}\n")
    box = {
        "albums-folder": os.path.join(folder, "albums"),
        "playlist-filename": "playlist.m3u",
        "volume": {"max-volume": 100, "default-volume": 40, "volume-step": 5},
        "albumplayer": {"rewind-max-time": 10, "update-schedule": 5, "pause-between-tracks": 0},
        "json": json_files,
        "rest-api": _rest_api(folder, 8443, "tinyjukebox"),
    }
    return _with_changes(folder, {"logger": _logger(folder, "tinyjukebox"), "tinyjukebox": box})


def link_library(folder: str, logger: logging.Logger) -> None:
    """A box at its first start: the library of ``TINYJUKEBOX_LIBRARY`` with its bootstrap password - as if they had
    been entered on the page (library.md, section 4.2). Later the page changes them."""
    address = os.environ.get(LIBRARY_VARIABLE, "").strip().rstrip("/")
    identity = os.path.join(os.path.abspath(os.path.expanduser(folder)), "identity")
    if not address or os.path.exists(os.path.join(identity, "library")):
        return
    os.makedirs(identity, mode=0o700, exist_ok=True)
    with open(os.path.join(identity, "library"), "w", encoding="utf-8") as outfile:
        outfile.write(address + "\n")
    password = os.environ.get(BOOTSTRAP_VARIABLE, "")
    if password:
        descriptor = os.open(os.path.join(identity, "bootstrap"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as outfile:
            outfile.write(password + "\n")
    logger.info("linked to the library %s at the first start", address)


def first_admin(users_file: str, logger: logging.Logger) -> None:
    """The user ``admin`` of the page, if the page has no user yet: its password from ``TINYJUKEBOX_ADMIN_PASSWORD``,
    asked on the terminal, or - without a terminal - generated and written to the log once."""
    users = UsersFile.read(users_file)
    if users:
        return
    password = os.environ.get(ADMIN_VARIABLE, "")
    generated = False
    if not password and sys.stdin.isatty():
        while True:
            password = getpass.getpass(f"password of the user admin of the page (at least {MIN_PASSWORD_LENGTH}): ")
            if len(password) >= MIN_PASSWORD_LENGTH and getpass.getpass("again: ") == password:
                break
            print("too short, or the two do not match", file=sys.stderr)
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"{ADMIN_VARIABLE}: the password needs at least {MIN_PASSWORD_LENGTH} characters")
    UsersFile.write(users_file, {"admin": {"password-hash": hash_password(password), "role": "admin"}})
    if generated:
        message = f"the page has the user admin with the password {password} - change it on the page (tab System)"
        logger.warning(message)
        print(message, file=sys.stderr)
    else:
        logger.info("the page has the user admin")
