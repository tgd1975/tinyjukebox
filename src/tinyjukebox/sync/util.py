"""Helpers of box and library: files written in one step, checksums, versions, time."""

import datetime
import hashlib
import json
import os
import subprocess
import time
from typing import Any

__all__ = [
    "clock_synchronized",
    "read_json",
    "sha256_bytes",
    "sha256_file",
    "timestamp",
    "version_key",
    "write_atomic",
    "write_json",
]

_clock: tuple[float, bool] | None = None  # when the clock was asked last, and the answer


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(filename: str) -> str:
    digest = hashlib.sha256()
    with open(filename, "rb") as infile:
        while chunk := infile.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(filename: str, data: bytes | str) -> None:
    """Write under a temporary name and rename: a power cut leaves the old or the new file, never half of one."""
    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
    temporary = f"{filename}.tmp"
    with open(temporary, "wb") as outfile:
        outfile.write(data.encode("utf-8") if isinstance(data, str) else data)
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(temporary, filename)


def read_json(filename: str, default: Any = None) -> Any:
    try:
        with open(filename, encoding="utf-8") as infile:
            return json.load(infile)
    except FileNotFoundError:
        return default


def write_json(filename: str, value: Any) -> None:
    write_atomic(filename, json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def version_key(version: str) -> tuple[int, ...]:
    """Sort key of a version like ``0.8.0``."""
    return tuple(int(part) for part in version.split("."))


def timestamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock_synchronized() -> bool:
    """Whether the clock of this computer was set from the network. The Pi has no real-time clock: without WLAN its
    time is wrong, and order is never decided by it."""
    global _clock  # noqa: PLW0603 - asked once a minute at most
    if os.path.exists("/run/systemd/timesync/synchronized"):
        return True
    if _clock is None or time.monotonic() - _clock[0] > 60:
        try:
            answer = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout.strip()  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            answer = ""
        _clock = (time.monotonic(), answer == "yes")
    return _clock[1]
