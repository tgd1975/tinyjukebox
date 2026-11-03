"""Access control of the rest api: password hashes, users file and tool, sessions and login throttling."""

import io
import os
import stat

import pytest

from tinyjukebox.web import users as users_tool
from tinyjukebox.web.auth import LoginThrottle, Sessions, UsersFile, hash_password, verify_password


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_throttle_lockout_and_reset():
    now = Clock()
    throttle = LoginThrottle(max_failures=3, lockout=30, reset_after=900, clock=now)
    for _ in range(2):
        throttle.failed("a")
    assert throttle.retry_after("a") == 0
    throttle.failed("a")
    assert throttle.retry_after("a") == 30
    assert throttle.retry_after("b") == 0  # other addresses are not affected
    now.now = 31
    assert throttle.retry_after("a") == 0
    throttle.failed("a")
    assert throttle.retry_after("a") == 30  # never longer than 30s
    now.now = 70
    throttle.failed("a")
    assert throttle.retry_after("a") == 30
    now.now = 70 + 901  # 15 minutes without failure - counter is reset
    assert throttle.retry_after("a") == 0
    throttle.failed("a")
    assert throttle.retry_after("a") == 0
    throttle.failed("a")
    throttle.failed("a")
    assert throttle.retry_after("a") == 30
    throttle.succeeded("a")
    assert throttle.retry_after("a") == 0


def test_throttle_table_is_limited_and_keeps_blocked_addresses():
    """If more than max_addresses addresses failed, unblocked addresses are forgotten first (oldest failure first) -
    flooding the table from many addresses must not unblock an attacked address."""
    now = Clock()
    throttle = LoginThrottle(max_failures=3, lockout=30, reset_after=900, clock=now, max_addresses=3)
    for _ in range(3):
        throttle.failed("attacked")
    now.now = 1
    throttle.failed("a1")
    throttle.failed("a1")
    now.now = 2
    throttle.failed("a2")
    throttle.failed("a2")
    now.now = 3
    throttle.failed("a3")  # 4 addresses - a1 (oldest, not blocked) is forgotten
    now.now = 4
    throttle.failed("a2")
    assert throttle.retry_after("a2") == 30  # a2 has been kept: third failure
    now.now = 5
    throttle.failed("a1")
    assert throttle.retry_after("a1") == 0  # a1 has been forgotten: first failure only

    for i in range(1000):  # flood from many addresses
        throttle.failed(f"10.0.{i // 256}.{i % 256}")
    assert throttle.retry_after("attacked") == 25
    assert throttle.retry_after("a2") == 29
    assert len(throttle._failures) == 3  # the flooding addresses are forgotten again


def test_throttle_with_only_blocked_addresses():
    """All tracked addresses are blocked - the oldest blocked ones are dropped (the table never grows beyond the
    limit)."""
    now = Clock()
    throttle = LoginThrottle(max_failures=1, lockout=30, reset_after=900, clock=now, max_addresses=2)
    for address in ["a", "b", "c"]:
        throttle.failed(address)
        now.now += 1
    assert throttle.retry_after("a") == 0
    assert throttle.retry_after("b") > 0
    assert throttle.retry_after("c") > 0


def test_session_expires():
    now = Clock()
    sessions = Sessions(timeout=100, clock=now)
    token = sessions.create("a", "admin")
    now.now = 90
    session = sessions.get(token)  # use extends the session
    assert session is not None
    assert session.username == "a"
    now.now = 180
    assert sessions.get(token) is not None
    now.now = 281
    assert sessions.get(token) is None
    assert sessions.get("") is None


def test_a_session_ends_when_the_user_did_nothing_for_too_long():
    now = Clock()
    sessions = Sessions(timeout=1000, clock=now, idle_timeout=100)
    token = sessions.create("a", "admin")
    now.now = 90
    assert sessions.get(token, idle=None) is not None  # the page asks by itself: that says nothing about the user
    now.now = 99
    assert sessions.get(token, idle=95) is not None  # ... and says that the user did something at 4
    now.now = 104
    assert sessions.get(token, idle=None) is not None
    now.now = 105
    assert sessions.get(token, idle=None) is None
    assert sessions.get(token) is None  # gone for good, also for a request that is an action

    token = sessions.create("a", "admin")
    now.now = 200
    assert sessions.get(token) is not None  # an action
    assert sessions.get(token, idle=60) is not None  # a tab in which less was done does not take it back
    now.now = 300
    assert sessions.get(token, idle=None) is not None
    now.now = 301
    assert sessions.get(token, idle=1000) is None


def test_without_a_limit_a_session_only_ends_when_nobody_asks():
    now = Clock()
    sessions = Sessions(timeout=1000, clock=now)
    token = sessions.create("a", "admin")
    for now.now in (900, 1800, 2700):
        assert sessions.get(token, idle=None) is not None


def test_password_hashes():
    password_hash = hash_password("geheim123")
    assert password_hash.startswith("scrypt$")
    assert "geheim123" not in password_hash
    assert verify_password("geheim123", password_hash)
    assert not verify_password("geheim124", password_hash)
    assert hash_password("geheim123") != password_hash  # salted
    for broken in ["", "plain", "md5$1$2$3$4$5", "scrypt$x$8$1$AAAA$AAAA", None]:
        assert not verify_password("geheim123", broken)


def test_users_tool(tmp_path, monkeypatch, capsys, logger):
    filename = str(tmp_path / "ssl" / "users.yaml")

    def tool(*args, password=None):
        if password is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
        users_tool.main(["-f", filename, "--password-stdin", *args])

    tool("add", "mama", "--role", "admin", password="sehr-geheim")
    tool("add", "kind", password="auch-geheim")
    assert stat.S_IMODE(os.stat(filename).st_mode) == 0o600
    users = UsersFile(filename, logger)
    assert users.authenticate("mama", "sehr-geheim") == "admin"
    assert users.authenticate("kind", "auch-geheim") == "user"

    with pytest.raises(SystemExit):
        tool("add", "mama", password="sehr-geheim")  # exists
    with pytest.raises(SystemExit):
        tool("add", "neu", password="kurz")  # too short
    tool("passwd", "kind", password="neues-passwort")
    tool("role", "kind", "admin")
    tool("remove", "mama")
    capsys.readouterr()
    tool("list")
    assert capsys.readouterr().out.strip() == "kind (admin)"
    assert UsersFile.read(filename)["kind"]["role"] == "admin"
    assert verify_password("neues-passwort", UsersFile.read(filename)["kind"]["password-hash"])


def test_the_last_admin_stays(tmp_path, monkeypatch):
    """Without an admin nobody could manage the page: the command neither removes the last one nor makes it a user."""
    filename = str(tmp_path / "users.yaml")

    def tool(*args, password=None):
        if password is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
        users_tool.main(["-f", filename, "--password-stdin", *args])

    tool("add", "mama", "--role", "admin", password="sehr-geheim")
    tool("add", "kind", password="auch-geheim")
    with pytest.raises(SystemExit, match="last admin"):
        tool("remove", "mama")
    with pytest.raises(SystemExit, match="last admin"):
        tool("role", "mama", "user")
    assert UsersFile.read(filename)["mama"]["role"] == "admin"
    tool("remove", "kind")  # a user can go
    tool("add", "papa", "--role", "admin", password="noch-geheimer")
    tool("role", "mama", "user")  # with a second admin both work
    tool("remove", "mama")
    assert {name: user["role"] for name, user in UsersFile.read(filename).items()} == {"papa": "admin"}


def test_a_session_ends_with_a_new_password(tmp_path, logger):
    """What a session remembers of the password changes with the password - wherever it is set."""
    filename = str(tmp_path / "users.yaml")
    UsersFile.write(filename, {"mama": {"password-hash": hash_password("sehr-geheim"), "role": "admin"}})
    users = UsersFile(filename, logger)
    sessions = Sessions(timeout=100)
    token = sessions.create("mama", "admin", users.credential("mama") or "")
    session = sessions.get(token)
    assert session is not None
    assert users.session_role(session) == "admin"
    assert users.credential("nobody") is None
    mtime = os.stat(filename).st_mtime_ns
    UsersFile.write(filename, {"mama": {"password-hash": hash_password("ganz-neu-1"), "role": "admin"}})
    os.utime(filename, ns=(mtime + 10**9, mtime + 10**9))
    assert users.session_role(session) is None
    # the one who set the password keeps the session: it gets the new credential
    sessions.remove_user("mama", keep=token, credential=users.credential("mama"))
    assert users.session_role(session) == "admin"


def test_the_command_line_takes_the_names_the_page_takes(tmp_path):
    from tinyjukebox.web import users

    for name in (" anna", "anna ", "an:na", "a" * 65, "an\tna"):
        with pytest.raises(SystemExit, match="invalid user name"):
            users.main(["-f", str(tmp_path / "users.yaml"), "--password-stdin", "add", name])
