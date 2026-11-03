"""The key, the player and the login of Spotify on this box (docs/developer/architecture.md, section 4.1).

Everything is kept in one folder of the box, ``spotify`` next to ``json`` and ``sync``. It is never synchronized and
is not part of a backup::

    spotify/key             the Soloist API key (mode 600)
    spotify/settings.json   {"speaker": bool, "connected": time or null}
    spotify/state.json      the build: when it was made, its Last-Modified; the last question to Spotify
    spotify/bin/soloist     the player, fetched from Spotify; soloist.previous: the build before
    spotify/data            the data directory of Soloist: the login (--data-dir)
    spotify/cache           its cache (--cache-dir)

Spotify does not allow passing Soloist on: the box fetches it itself, from Spotify, when the key is entered. Every
build stops working 90 days after it was built. The box keeps the build it has while that has more than
:data:`RENEW_DAYS` days left: once a day it looks at its age, and only then it asks Spotify whether the archive has
changed (``If-Modified-Since``). A build that has expired (exit code 10) and "Update now" ask at once.

A download is checked against its ``ETag`` when that is the MD5 of the archive; otherwise it is trusted because it
comes from Spotify's address over HTTPS, and the new build replaces the old one only if ``soloist --version`` runs. It
is unpacked in pieces and refused if the program unpacks to more than :data:`MAX_PROGRAM` bytes, everything else to more
than :data:`MAX_REST` bytes, or if more than :data:`MAX_FILES` files come before the program.
"""

import contextlib
import ctypes.util
import datetime
import functools
import gzip
import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from typing import IO, Any, cast

from tinyjukebox.core.logging import get_child
from tinyjukebox.errors import BadRequest, Conflict

__all__ = [
    "ARCHITECTURES",
    "BUILDS",
    "CONNECT_SECONDS",
    "EXPIRED",
    "LIFETIME_DAYS",
    "MAX_FILES",
    "MAX_PROGRAM",
    "MAX_REST",
    "RENEW_DAYS",
    "SpotifyAccount",
    "Unavailable",
    "build_time",
]

BUILDS = "https://soloist-builds.spotifycdn.com/soloist_release_{}.tar.gz"
ARCHITECTURES = {"aarch64": "arm64", "arm64": "arm64", "armv7l": "arm32", "x86_64": "x86_64", "amd64": "x86_64"}
"""``uname -m`` -> the name of the build of Soloist."""
LIFETIME_DAYS = 90
RENEW_DAYS = 30
"""The box looks for a new build once its build has fewer days left than this."""
EXPIRED = 10
"""Exit code of Soloist: the build has expired."""
CONNECT_SECONDS = 300.0
"""How long "Connect" waits for the box to be chosen in the Spotify app."""
CHECK_EVERY = 24 * 3600.0
MAX_ARCHIVE = 128 * 2**20
MAX_PROGRAM = 4 * MAX_ARCHIVE
"""Bytes the program may unpack to at most - a decompression bomb would fill the card."""
MAX_REST = 16 * 2**20
"""Bytes of everything else in the archive: the other files and the headers, which tarfile keeps in memory."""
MAX_FILES = 100
"""Files the archive may hold before the program (it holds a few)."""
CHUNK = 2**20
MAX_KEY_LENGTH = 400
AVAHI = ("avahi-daemon.socket", "avahi-daemon.service")
_BUILD = re.compile(r"\bbuild\s+(\d{9,11})\b")
_DATE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")

Run = Callable[..., subprocess.CompletedProcess[str]]
Opener = Callable[..., Any]


class Unavailable(Exception):  # noqa: N818
    """Spotify cannot play on this box - ``reason`` is a word for the page: ``no_key``, ``no_player``,
    ``not_connected``, ``expired``, ``cannot_run``, ``missing``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _Unpacked:
    """The unpacked archive as tarfile reads it: at most ``program`` bytes while the program is copied
    (:attr:`copying`), at most ``rest`` bytes of everything else. :raises ValueError: beyond."""

    def __init__(self, stream: io.BufferedIOBase, program: int, rest: int) -> None:
        self._stream = stream
        self._left = {True: program, False: rest}
        self.copying = False

    def read(self, size: int = -1) -> bytes:
        left = self._left[self.copying]
        data = self._stream.read(left + 1 if size < 0 else min(size, left + 1))
        self._left[self.copying] = left - len(data)
        if len(data) > left:
            raise ValueError("the program is too large." if self.copying else "the archive holds too much.")
        return data


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(moment: datetime.datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment is not None else None


def _parse(text: object) -> datetime.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def build_time(version: str) -> datetime.datetime | None:
    """When the build was made, from the output of ``soloist --version`` (e.g. ``1.3.8.105 build 1790942580
    (20261002) (g5c3a2053ac), linux/aarch64``) - ``None`` if it does not say."""
    match = _BUILD.search(version)
    if match:
        return datetime.datetime.fromtimestamp(int(match.group(1)), datetime.UTC)
    match = _DATE.search(version)
    if match:
        with contextlib.suppress(ValueError):
            year, month, day = (int(part) for part in match.groups())
            return datetime.datetime(year, month, day, tzinfo=datetime.UTC)
    return None


@functools.cache
def _library(name: str) -> str | None:
    """``ctypes.util.find_library`` runs ``ldconfig`` - once per run is enough."""
    return ctypes.util.find_library(name)


def _write(path: str, data: bytes | IO[bytes], mode: int = 0o600) -> None:
    """``data`` - bytes or a stream, copied in pieces - into ``path``: complete or not at all."""
    temp = path + ".tmp"
    try:
        with open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "wb") as f:
            if isinstance(data, bytes):
                f.write(data)
            else:
                while chunk := data.read(CHUNK):
                    f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temp)
        raise
    os.replace(temp, path)


class SpotifyAccount:
    """Key, player and login of this box, kept in ``folder``.

    ``device_name()``: the name the box has in the Spotify app. ``manage_avahi``: stop ``avahi-daemon`` while the box
    waits to be chosen in the Spotify app, so that Soloist gets the port for its announcement (a box with hardware).
    ``run`` and ``opener`` are ``subprocess.run`` and ``urllib.request.urlopen`` (tests replace them).
    """

    def __init__(
        self,
        folder: str,
        logger: logging.Logger,
        device_name: Callable[[], str],
        manage_avahi: bool = False,
        machine: str | None = None,
        run: Run = subprocess.run,
        opener: Opener = urllib.request.urlopen,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        clock: Callable[[], datetime.datetime] = _now,
        connect_seconds: float = CONNECT_SECONDS,
    ) -> None:
        self.folder = folder
        self._logger = get_child(logger, type(self).__name__)
        self._device_name = device_name
        self._manage_avahi = manage_avahi
        self._architecture = ARCHITECTURES.get((machine or platform.machine()).lower())
        self._run = run
        self._opener = opener
        self._popen = popen
        self._clock = clock
        self._connect_seconds = connect_seconds
        self._lock = threading.RLock()
        self._fetching: threading.Thread | None = None
        self._connecting: threading.Thread | None = None
        self._pairing: subprocess.Popen[bytes] | None = None
        self._stopped = threading.Event()
        self._checker: threading.Thread | None = None
        self.before_connect: Callable[[], None] = lambda: None
        """Called before "Connect": Soloist must not run with the same data directory meanwhile."""
        self.on_speaker: Callable[[bool], None] = lambda enabled: None
        """Called when the switch of the speaker changed."""
        self.on_connected: Callable[[], None] = lambda: None
        """Called when the box was connected or disconnected."""

    # --- files -------------------------------------------------------------------------------------------------------

    def _path(self, *parts: str) -> str:
        return os.path.join(self.folder, *parts)

    @property
    def program(self) -> str:
        return self._path("bin", "soloist")

    @property
    def data_dir(self) -> str:
        return self._path("data")

    @property
    def cache_dir(self) -> str:
        return self._path("cache")

    def _read_json(self, name: str) -> dict[str, Any]:
        try:
            with open(self._path(name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _update_json(self, name: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            data = {**self._read_json(name), **changes}
            os.makedirs(self.folder, exist_ok=True)
            _write(self._path(name), json.dumps(data, indent=2).encode(), 0o644)
            return data

    # --- key ---------------------------------------------------------------------------------------------------------

    @property
    def key(self) -> str | None:
        try:
            with open(self._path("key"), encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def set_key(self, key: object) -> None:
        """Store the Soloist API key; the box fetches the player then if it has none."""
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > MAX_KEY_LENGTH:
            raise BadRequest("the key must not be empty.", code="spotify.key")
        key = key.strip()
        if any(c.isspace() or not c.isprintable() for c in key):
            raise BadRequest("the key must not contain blanks.", code="spotify.key")
        os.makedirs(self.folder, exist_ok=True)
        _write(self._path("key"), key.encode())
        self._logger.info("the Soloist API key was entered")
        if not os.path.exists(self.program):
            self._fetch_in_background(force=True)

    # --- settings ----------------------------------------------------------------------------------------------------

    @property
    def speaker(self) -> bool:
        return bool(self._read_json("settings.json").get("speaker"))

    def set_speaker(self, enabled: object) -> None:
        if not isinstance(enabled, bool):
            raise BadRequest("enabled must be true or false.")
        self._update_json("settings.json", speaker=enabled)
        self._logger.info("Spotify speaker while no tag is on the box: %s", "on" if enabled else "off")
        self.on_speaker(enabled)

    @property
    def connected(self) -> bool:
        return _parse(self._read_json("settings.json").get("connected")) is not None

    def lost(self) -> None:
        """Soloist says that it has no login (any more): the box is not connected."""
        if self.connected:
            self._logger.warning("Spotify: the login of the box is gone - connect again")
            self._update_json("settings.json", connected=None)
            self.on_connected()

    def info(self) -> dict[str, Any]:
        settings = self._read_json("settings.json")
        return {
            "key": self.key is not None,
            "connected": _parse(settings.get("connected")) is not None,
            "connected_since": settings.get("connected"),
            "connecting": self._connecting is not None,
            "connect_result": settings.get("connect-result"),
            "speaker": bool(settings.get("speaker")),
        }

    # --- connect -----------------------------------------------------------------------------------------------------

    def connect(self) -> None:
        """Wait up to :data:`CONNECT_SECONDS` to be chosen in the Spotify app (``soloist --pair``) - in the
        background. The app connects the box to its account."""
        key = self.key
        if key is None:
            raise Conflict("enter the key of Spotify first.", code="spotify.no_key")
        self.usable()
        with self._lock:
            if self._connecting is not None:
                raise Conflict("the box is waiting to be connected already.", code="spotify.connecting")
            self._connecting = threading.Thread(target=self._connect, args=(key,), name="Spotify.connect", daemon=True)
            self._update_json("settings.json", **{"connect-result": None})
            self._connecting.start()

    def _avahi(self, action: str) -> None:
        if not self._manage_avahi:
            return
        try:
            self._run(["sudo", "-n", "systemctl", action, *AVAHI], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            self._logger.warning("cannot %s avahi-daemon: %s", action, e)

    def _connect(self, key: str) -> None:
        result = "failed"
        try:
            self.before_connect()
            self._avahi("stop")
            command = [self.program, "--pair", "-n", self._device_name(), "-k", key, "-D", self.data_dir, "-C",
                       self.cache_dir]  # fmt: skip
            os.makedirs(self.data_dir, exist_ok=True)
            self._logger.info("Spotify: waiting %.0f s to be chosen in the Spotify app", self._connect_seconds)
            process = self._popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self._pairing = process
            try:
                _, error = process.communicate(timeout=self._connect_seconds)
            except subprocess.TimeoutExpired:
                self._end_pairing(process)
                result = "timeout"
            else:
                if process.returncode == 0:
                    result = "connected"
                elif self._stopped.is_set():
                    result = "stopped"
                else:
                    self._logger.warning(
                        "Spotify: connecting failed (%s): %s", process.returncode, error.decode(errors="replace")[-300:]
                    )
                    if process.returncode == EXPIRED:
                        self._fetch_in_background(force=True)
        except OSError as e:
            self._logger.error("Spotify: cannot start the player to connect: %s", e)
        finally:
            self._pairing = None
            self._avahi("start")
            changes: dict[str, Any] = {"connect-result": result}
            if result == "connected":
                changes["connected"] = _iso(self._clock())
                self._logger.info("Spotify: the box is connected to an account")
            self._update_json("settings.json", **changes)
            with self._lock:
                self._connecting = None
            if result == "connected":
                self.on_connected()

    def _end_pairing(self, process: subprocess.Popen[bytes]) -> None:
        """Nobody chose the box in time: end ``soloist --pair`` - every wait is bounded."""
        for end in (process.terminate, process.kill):
            end()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                continue
            return
        self._logger.error("Spotify: soloist --pair does not end (pid %s)", process.pid)

    def disconnect(self) -> None:
        """Remove the login from the box."""
        with self._lock:
            if self._connecting is not None:
                raise Conflict("the box is waiting to be connected.", code="spotify.connecting")
            self.before_connect()
            shutil.rmtree(self.data_dir, ignore_errors=True)
            self._update_json("settings.json", connected=None, **{"connect-result": None})
        self._logger.info("Spotify: the box is disconnected from its account")
        self.on_connected()

    # --- the player --------------------------------------------------------------------------------------------------

    def missing(self) -> list[str]:
        """Packages of the system that Soloist and the sound need and that are not there."""
        result = []
        if _library("atomic") is None:
            result.append("libatomic1")
        for program, package in (("pipewire", "pipewire"), ("wireplumber", "wireplumber")):
            if shutil.which(program) is None:
                result.append(package)
        return result

    def _version(self, program: str) -> tuple[str | None, str | None]:
        """(output of ``--version``, error)"""
        try:
            done = self._run([program, "--version"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            return None, str(e)
        if done.returncode == 0:
            return done.stdout.strip() or done.stderr.strip(), None
        if done.returncode == EXPIRED:
            return None, "expired"
        said = (done.stderr or done.stdout or "").strip()
        if "libatomic" in said:
            return None, "libatomic1 is missing"
        return None, said[-300:] or f"exit code {done.returncode}"

    def _days_left(self, state: dict[str, Any]) -> float | None:
        built = _parse(state.get("built"))
        if built is None:
            return None
        return LIFETIME_DAYS - (self._clock() - built).total_seconds() / 86400

    def player_info(self) -> dict[str, Any]:
        """What the card "Player" shows."""
        state = self._read_json("state.json")
        built = _parse(state.get("built"))
        days_left = self._days_left(state)
        if self._fetching is not None:
            status = "fetching"
        elif not os.path.exists(self.program):
            status = "none"
        elif state.get("error"):
            status = "cannot_run"
        elif days_left is not None and days_left <= 0:
            status = "expired"
        else:
            status = "ready"
        return {
            "state": status,
            "error": state.get("error"),
            "version": state.get("version"),
            "built": _iso(built),
            "last_day": _iso(built + datetime.timedelta(days=LIFETIME_DAYS)) if built else None,
            "renew_from": _iso(built + datetime.timedelta(days=LIFETIME_DAYS - RENEW_DAYS)) if built else None,
            "asked": state.get("asked"),
            "result": state.get("result"),
            "missing": self.missing(),
        }

    def usable(self) -> str:
        """The program of Soloist, if it can be used. :raises Unavailable: otherwise."""
        if self.key is None:
            raise Unavailable("no_key", "the key of Spotify has not been entered.")
        if not os.path.exists(self.program):
            raise Unavailable("no_player", "the player of Spotify has not been fetched yet.")
        state = self._read_json("state.json")
        if state.get("error"):
            raise Unavailable("cannot_run", f"the player of Spotify does not start: {state['error']}")
        days_left = self._days_left(state)
        if days_left is not None and days_left <= 0:
            raise Unavailable("expired", "the player of Spotify has expired.")
        return self.program

    def update_now(self) -> None:
        """Ask Spotify at once whether there is a new build, whatever the age of the one the box has."""
        if self.key is None:
            raise Conflict("enter the key of Spotify first.", code="spotify.no_key")
        if not self._fetch_in_background(force=True):
            raise Conflict("the player is being fetched already.", code="spotify.fetching")

    def expired(self) -> None:
        """Soloist ended with exit code 10: its build has expired - a new one is fetched."""
        self._logger.warning("Spotify: the build of the player has expired - fetching a new one")
        self._update_json("state.json", error=None, built=_iso(self._clock() - datetime.timedelta(days=LIFETIME_DAYS)))
        self._fetch_in_background(force=True)

    def wait_until_fetched(self, timeout: float | None = None) -> None:
        thread = self._fetching
        if thread is not None:
            thread.join(timeout)

    def wait_until_connected(self, timeout: float | None = None) -> None:
        thread = self._connecting
        if thread is not None:
            thread.join(timeout)

    def _fetch_in_background(self, force: bool) -> bool:
        with self._lock:
            if self._fetching is not None:
                return False
            self._fetching = threading.Thread(target=self._fetch, args=(force,), name="Spotify.fetch", daemon=True)
            self._fetching.start()
            return True

    def check(self) -> None:
        """The daily look: with a key, and no build or one with less than :data:`RENEW_DAYS` days left, ask
        Spotify."""
        if self.key is None:
            return
        days_left = self._days_left(self._read_json("state.json"))
        if not os.path.exists(self.program) or days_left is None or days_left < RENEW_DAYS:
            self._fetch_in_background(force=False)

    def _fetch(self, force: bool) -> None:
        try:
            result, error = self._ask(force)
        except Exception as e:  # a failed fetch must not end the thread silently
            self._logger.exception("Spotify: fetching the player failed")
            result, error = "failed", str(e)
        changes: dict[str, Any] = {"asked": _iso(self._clock()), "result": result}
        if error is not None:
            changes["fetch-error"] = error
        self._update_json("state.json", **changes)
        with self._lock:
            self._fetching = None

    def _ask(self, force: bool) -> tuple[str, str | None]:  # noqa: PLR0911 - one return per outcome
        """(result, error) of one question to Spotify: ``unchanged``, ``new``, ``failed``, ``broken`` (the new build
        does not start; the box keeps the one it has)."""
        if self._architecture is None:
            return "failed", f"there is no build of Soloist for the processor '{platform.machine()}'."
        state = self._read_json("state.json")
        headers = {}
        if os.path.exists(self.program) and state.get("last-modified") and not state.get("error"):
            headers["If-Modified-Since"] = state["last-modified"]
        request = urllib.request.Request(BUILDS.format(self._architecture), headers=headers)  # noqa: S310 - https
        try:
            with self._opener(request, timeout=60) as response:
                data = response.read(MAX_ARCHIVE + 1)
                last_modified = response.headers.get("Last-Modified")
                etag = (response.headers.get("ETag") or "").strip('"')
        except urllib.error.HTTPError as e:
            if e.code == 304:
                self._logger.info("Spotify: the box has the current build of the player")
                return "unchanged", None
            return "failed", f"HTTP {e.code}"
        except OSError as e:
            self._logger.info("Spotify: cannot ask for a new build of the player: %s", e)
            return "failed", str(e)
        if len(data) > MAX_ARCHIVE:
            return "failed", "the archive is too large."
        if re.fullmatch(r"[0-9a-f]{32}", etag) and hashlib.md5(data).hexdigest() != etag:  # noqa: S324 - a checksum
            return "failed", "the archive does not match its checksum."
        try:
            program = self._unpack(data)
        except (OSError, EOFError, zlib.error, tarfile.TarError, ValueError) as e:
            self._logger.error("Spotify: cannot unpack the new build of the player: %s", e)
            return "failed", f"cannot unpack the archive: {e}"
        version, error = self._version(program)
        if version is None:
            os.remove(program)
            self._logger.error("Spotify: the new build of the player does not start: %s", error)
            if not os.path.exists(self.program):
                self._update_json("state.json", error=error)
            return "broken", error
        if os.path.exists(self.program):
            os.replace(self.program, self.program + ".previous")
        os.replace(program, self.program)
        built = build_time(version) or self._clock()
        self._update_json(
            "state.json", version=version, built=_iso(built), error=None, **{"last-modified": last_modified}
        )
        self._logger.info("Spotify: new build of the player: %s", version)
        return "new", None

    def _unpack(self, data: bytes) -> str:
        """The program ``soloist`` of the archive, as ``bin/soloist.new`` - unpacked and written in pieces.
        :raises ValueError: if it holds none, or more than :data:`MAX_PROGRAM`, :data:`MAX_REST`, :data:`MAX_FILES`."""
        os.makedirs(self._path("bin"), exist_ok=True)
        target = self.program + ".new"
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as unpacked:
            stream = _Unpacked(unpacked, MAX_PROGRAM, MAX_REST)
            # read as a stream: tarfile keeps the headers, never the content of a file
            with tarfile.open(fileobj=cast(IO[bytes], stream), mode="r|") as archive:
                for count, member in enumerate(archive):
                    if count >= MAX_FILES:
                        raise ValueError("the archive holds too many files.")
                    if os.path.basename(member.name) == "soloist" and member.isfile():
                        source = archive.extractfile(member)
                        if source is None:
                            break
                        stream.copying = True
                        _write(target, source, 0o700)
                        return target
        raise ValueError("the archive holds no program 'soloist'.")

    def back_to_previous(self) -> bool:
        """The build that was fetched last does not run: use the one before. Returns whether there was one."""
        previous = self.program + ".previous"
        if not os.path.exists(previous):
            return False
        version, _ = self._version(previous)
        if version is None:
            return False
        os.replace(previous, self.program)
        built = build_time(version) or self._clock()
        self._update_json("state.json", version=version, built=_iso(built), error=None)
        self._logger.warning("Spotify: back to the build before: %s", version)
        return True

    # --- life cycle --------------------------------------------------------------------------------------------------

    def start(self, first_check: float = 60.0) -> None:
        """The daily look at the age of the build - the first one ``first_check`` seconds after the start."""
        self._stopped.clear()
        self._checker = threading.Thread(target=self._check_daily, args=(first_check,), name="Spotify.check")
        self._checker.daemon = True
        self._checker.start()

    def _check_daily(self, first: float) -> None:
        wait = first
        while not self._stopped.wait(wait):
            self.check()
            wait = CHECK_EVERY

    def stop(self) -> None:
        self._stopped.set()
        pairing = self._pairing
        if pairing is not None and pairing.poll() is None:
            pairing.terminate()
        for thread in (self._checker, self._connecting, self._fetching):
            if thread is not None and thread is not threading.current_thread():
                thread.join(10)
        self._checker = None
