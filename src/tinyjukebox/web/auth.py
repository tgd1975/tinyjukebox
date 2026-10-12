"""Access control for the rest api: password hashes, users file, sessions and login throttling."""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable
from typing import Any

import yaml

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "ROLES",
    "LoginThrottle",
    "Session",
    "Sessions",
    "UsersFile",
    "has_role",
    "hash_password",
    "verify_password",
]

ROLES = ("user", "admin")  # admin may do everything a user may do
MIN_PASSWORD_LENGTH = 8

# scrypt parameters - n=2**14 needs 16 MB and ~50 ms on a Raspberry Pi 3
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

Clock = Callable[[], float]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """'scrypt$n$r$p$salt$hash' (salt and hash base64 encoded)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, password_hash: object) -> bool:
    """Constant time comparison; malformed hashes never match."""
    try:
        algorithm, n, r, p, salt, digest = str(password_hash).split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(digest)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


# compared against if the user is unknown - login takes as long as for a known user (no user enumeration)
_DUMMY_HASH = hash_password(secrets.token_hex(16))

# one password check at a time: each one needs 16 MB, and parallel logins (one thread per connection) would multiply
# that - and pass the login throttle before the first failure has been counted
_VERIFY_LOCK = threading.Lock()


def has_role(role: str, required_role: str) -> bool:
    return ROLES.index(role) >= ROLES.index(required_role)


class UsersFile:
    """Users are stored in a yaml file (managed with ``tinyjukebox-users``)::

        users:
            mama:
                password-hash: scrypt$16384$8$1$...
                role: admin

    The file is reloaded if it has been changed - adding or removing users needs no restart. The web page changes
    it with :meth:`change`.
    """

    def __init__(self, filename: str, logger: logging.Logger) -> None:
        self.filename = filename
        self._logger = logger
        self._lock = threading.Lock()
        self._mtime: int | None = None
        self._users: dict[str, dict[str, Any]] = {}
        self._reload()

    @staticmethod
    def read(filename: str) -> dict[str, dict[str, Any]]:
        """:raises ValueError: if the file is not a valid users file."""
        try:
            with open(filename, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}
        users = data.get("users") or {} if isinstance(data, dict) else None
        if not isinstance(users, dict):
            raise ValueError(f"'users' in '{filename}' must be a mapping.")
        for name, user in users.items():
            if not isinstance(user, dict) or "password-hash" not in user or user.get("role") not in ROLES:
                raise ValueError(f"user '{name}' in '{filename}' needs a password-hash and a role ({'/'.join(ROLES)}).")
        return users

    @staticmethod
    def write(filename: str, users: dict[str, dict[str, Any]]) -> None:
        """Write the users file (mode 600, atomically)."""
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        temp = filename + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump({"users": users}, f, default_flow_style=False, allow_unicode=True)
        os.replace(temp, filename)

    def _reload(self) -> None:
        try:
            st = os.stat(self.filename)
        except FileNotFoundError:
            if self._mtime is not None or not self._users:
                self._logger.warning(
                    "users file '%s' not found - nobody can log in. "
                    "create users with 'tinyjukebox-users -f %s add NAME'.",
                    self.filename,
                    self.filename,
                )
            self._users = {}
            self._mtime = None
            return
        if st.st_mtime_ns == self._mtime:
            return
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            self._logger.warning("users file '%s' is readable by other users - use 'chmod 600'.", self.filename)
        try:
            self._users = self.read(self.filename)
        except (ValueError, yaml.YAMLError) as e:
            self._logger.error("cannot read users file: %s - keeping the previous users.", e)
        self._mtime = st.st_mtime_ns

    def roles(self) -> dict[str, str]:
        """The role of every user."""
        with self._lock:
            self._reload()
            return {name: str(user["role"]) for name, user in self._users.items()}

    def change(self, change: Callable[[dict[str, dict[str, Any]]], None]) -> None:
        """Apply ``change`` to the users of the file and write it back. Whatever ``change`` raises leaves the file
        as it is.

        :raises ValueError: if the file is not a valid users file."""
        with self._lock:
            users = self.read(self.filename)
            change(users)
            self.write(self.filename, users)
            # not via _reload: on a coarse file system the new file may have the mtime of the old one
            self._users = users
            self._mtime = os.stat(self.filename).st_mtime_ns

    def authenticate(self, username: str, password: str) -> str | None:
        """The role of the user - None for unknown users or wrong passwords."""
        with self._lock:
            self._reload()
            user = self._users.get(username)
        with _VERIFY_LOCK:
            if user is None:
                verify_password(password, _DUMMY_HASH)
                return None
            return str(user["role"]) if verify_password(password, user["password-hash"]) else None

    def get_role(self, username: str) -> str | None:
        """Current role of the user - None if the user has been removed in the meantime."""
        with self._lock:
            self._reload()
            user = self._users.get(username)
        return None if user is None else str(user["role"])


class Session:
    def __init__(self, username: str, role: str, expires: float, active: float) -> None:
        self.username = username
        self.role = role
        self.expires = expires
        self.active = active  # when the user did something last


class Sessions:
    """Random session tokens; only their sha256 hash is kept. Sessions expire after ``timeout`` seconds without
    use, and after ``idle_timeout`` seconds in which the user did nothing (0: never) - an open page asks all the time,
    so only what it reports as done by the user counts for that."""

    def __init__(self, timeout: float, clock: Clock = time.monotonic, idle_timeout: float = 0) -> None:
        self._timeout = timeout
        self.idle_timeout = idle_timeout
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, username: str, role: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            now = self._clock()
            self._sessions[self._key(token)] = Session(username, role, now + self._timeout, now)
        return token

    def get(self, token: str | None, idle: float | None = 0) -> Session | None:
        """The session of the token (its expiry is extended) - None for unknown or expired tokens.

        ``idle``: the user did something last this many seconds ago - 0 for a request that is an action itself, None
        for one that says nothing about the user (the page asking by itself)."""
        if not token:
            return None
        key = self._key(token)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            now = self._clock()
            if idle is not None:
                session.active = max(session.active, now - max(idle, 0))
            if session.expires < now or (self.idle_timeout > 0 and now - session.active > self.idle_timeout):
                del self._sessions[key]
                return None
            session.expires = now + self._timeout
            return session

    def remove(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(self._key(token), None)

    def remove_user(self, username: str, keep: str | None = None) -> None:
        """Ends all sessions of the user - except the one of the token ``keep``."""
        keep_key = self._key(keep) if keep else None
        with self._lock:
            for key in [k for k, s in self._sessions.items() if s.username == username and k != keep_key]:
                del self._sessions[key]

    def _purge(self) -> None:
        now = self._clock()
        idle = self.idle_timeout
        for key in [k for k, s in self._sessions.items() if s.expires < now or (idle > 0 and now - s.active > idle)]:
            del self._sessions[key]


class LoginThrottle:
    """After ``max_failures`` failed logins from one address, every further failed attempt blocks the address for
    ``lockout`` seconds. Failures are forgotten ``reset_after`` seconds after the last failure or with a successful
    login.

    At most ``max_addresses`` addresses are tracked. If there are more, addresses that are not blocked are forgotten
    first (oldest failure first) - flooding the table from many addresses must not unblock an attacked address.
    """

    def __init__(
        self,
        max_failures: int = 5,
        lockout: float = 30,
        reset_after: float = 900,
        clock: Clock = time.monotonic,
        max_addresses: int = 10000,
    ) -> None:
        self._max_failures = max_failures
        self._lockout = lockout
        self._reset_after = reset_after
        self._clock = clock
        self._max_addresses = max_addresses
        self._failures: dict[str, list[float]] = {}  # address -> [count, time of last failure, blocked until]
        self._lock = threading.Lock()

    def _entry(self, address: str) -> list[float] | None:
        entry = self._failures.get(address)
        if entry is not None and self._clock() - entry[1] > self._reset_after:
            del self._failures[address]
            entry = None
        return entry

    def retry_after(self, address: str) -> int:
        """Seconds until the next login attempt is allowed (0: allowed now)."""
        with self._lock:
            entry = self._entry(address)
            if entry is None:
                return 0
            return max(0, int(entry[2] - self._clock() + 0.999))

    def failed(self, address: str) -> None:
        with self._lock:
            now = self._clock()
            entry = self._entry(address)
            if entry is None:
                entry = self._failures[address] = [0, now, 0]
            entry[0] += 1
            entry[1] = now
            if entry[0] >= self._max_failures:
                entry[2] = now + self._lockout
            if len(self._failures) > self._max_addresses:
                self._shrink(now)

    def _shrink(self, now: float) -> None:
        excess = len(self._failures) - self._max_addresses
        # not blocked before blocked, then oldest failure first
        order = sorted(self._failures.items(), key=lambda item: (item[1][2] > now, item[1][1]))
        for address, _ in order[:excess]:
            del self._failures[address]

    def succeeded(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)
