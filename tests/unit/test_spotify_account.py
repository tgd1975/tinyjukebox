"""Key, player and login of Spotify on a box (tinyjukebox.spotify.account) and the cards "Account" and "Player"."""

import datetime
import email.message
import hashlib
import io
import subprocess
import tarfile
import urllib.error

import pytest

from tests.unit.test_web import _fast_server, certificate, make_box  # noqa: F401 - fixtures
from tinyjukebox.errors import BadRequest, Conflict
from tinyjukebox.spotify import account as account_module
from tinyjukebox.spotify.account import LIFETIME_DAYS, SpotifyAccount, Unavailable, build_time

BUILT = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.UTC)
VERSION = f"1.3.9.1 build {int(BUILT.timestamp())} (20261002) (gabc), linux/aarch64"


def archive(content=b"#!/bin/sh\n"):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, payload in (("CHANGELOG.md", b"# Soloist"), ("soloist", content)):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(payload))
    return data.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, last_modified="Wed, 14 Oct 2026 00:09:57 GMT", etag=None):
        super().__init__(data)
        self.headers = email.message.Message()
        self.headers["Last-Modified"] = last_modified
        self.headers["ETag"] = f'"{etag if etag is not None else hashlib.md5(data).hexdigest()}"'  # noqa: S324

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Spotify:
    """What Spotify answers: an archive, "not modified" or a failure - and what was asked."""

    def __init__(self):
        self.answers = []
        self.asked = []

    def __call__(self, request, timeout):
        self.asked.append(dict(request.headers))
        answer = self.answers.pop(0) if self.answers else Response(archive())
        if isinstance(answer, Exception):
            raise answer
        return answer


class Program:
    """``subprocess.run`` for ``soloist --version`` and systemctl."""

    def __init__(self):
        self.version = (0, VERSION)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[-1] == "--version":
            code, out = self.version
            return subprocess.CompletedProcess(command, code, out if code == 0 else "", out if code else "")
        return subprocess.CompletedProcess(command, 0, "", "")


class Pairing:
    """``soloist --pair``: ends with ``code``, or not at all (``None``)."""

    def __init__(self, code):
        self.code = code
        self.returncode = None

    def communicate(self, timeout=None):
        if self.code is None and self.returncode is None:
            raise subprocess.TimeoutExpired("soloist", timeout)
        self.returncode = self.code if self.returncode is None else self.returncode
        return b"", b"said something"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class Account(SpotifyAccount):
    """An account with what Spotify, the program and the pairings saw."""

    spotify: Spotify
    programs: Program
    pairings: list[list[str]]


@pytest.fixture
def now():
    return {"time": BUILT + datetime.timedelta(days=10)}


@pytest.fixture
def make_account(tmp_path, logger, now):
    def make(pairing=0, **kwargs):
        spotify, program, pairings = Spotify(), Program(), []

        def popen(command, **_):
            pairings.append(command)
            return Pairing(pairing)

        kwargs.setdefault("machine", "aarch64")
        account = Account(str(tmp_path / "spotify"), logger, lambda: "Kinderzimmer", run=program,
                                 opener=spotify, popen=popen, clock=lambda: now["time"], connect_seconds=0.1,
                                 **kwargs)  # fmt: skip
        account.spotify, account.programs, account.pairings = spotify, program, pairings
        return account

    return make


def test_build_time():
    assert build_time(VERSION) == BUILT.replace(hour=12)
    assert build_time("1.3.9 (20261002)") == datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC)
    assert build_time("soloist") is None


def test_key_fetches_the_player(make_account):
    account = make_account()
    assert account.player_info()["state"] == "none"
    with pytest.raises(Unavailable) as missing:
        account.usable()
    assert missing.value.reason == "no_key"
    for wrong in ("", "  ", "a b", None, "x" * 500):
        with pytest.raises(BadRequest):
            account.set_key(wrong)
    account.set_key(" abc123 ")
    account.wait_until_fetched(5)
    assert account.key == "abc123"
    info = account.player_info()
    assert (info["state"], info["version"], info["result"]) == ("ready", VERSION, "new")
    assert info["built"] == "2026-10-02T12:00:00+00:00"
    assert info["last_day"] == (BUILT + datetime.timedelta(days=LIFETIME_DAYS)).isoformat()
    assert account.usable() == account.program
    assert account.spotify.asked == [{}]  # no If-Modified-Since: the box had no build


def test_daily_check_asks_only_30_days_before_the_last_day(make_account, now):
    account = make_account()
    account.set_key("abc")
    account.wait_until_fetched(5)
    account.check()  # 80 days left
    account.wait_until_fetched(5)
    assert len(account.spotify.asked) == 1
    now["time"] = BUILT + datetime.timedelta(days=61)  # 29 days left
    account.spotify.answers.append(urllib.error.HTTPError("url", 304, "Not Modified", email.message.Message(), None))
    account.check()
    account.wait_until_fetched(5)
    assert account.spotify.asked[-1] == {"If-modified-since": "Wed, 14 Oct 2026 00:09:57 GMT"}
    assert account.player_info()["result"] == "unchanged"


def test_a_new_build_that_does_not_start_is_not_taken(make_account):
    account = make_account()
    account.set_key("abc")
    account.wait_until_fetched(5)
    account.programs.version = (1, "error while loading shared libraries: libatomic.so.1")
    account.update_now()
    account.wait_until_fetched(5)
    info = account.player_info()
    assert (info["state"], info["result"], info["version"]) == ("ready", "broken", VERSION)


def test_a_download_that_does_not_match_its_checksum_is_not_taken(make_account):
    account = make_account()
    account.spotify.answers.append(Response(archive(), etag="0" * 32))
    account.set_key("abc")
    account.wait_until_fetched(5)
    assert account.player_info()["state"] == "none"
    assert account.player_info()["result"] == "failed"


def test_without_internet(make_account):
    account = make_account()
    account.spotify.answers.append(OSError("no network"))
    account.set_key("abc")
    account.wait_until_fetched(5)
    assert (account.player_info()["state"], account.player_info()["result"]) == ("none", "failed")
    with pytest.raises(Unavailable) as missing:
        account.usable()
    assert missing.value.reason == "no_player"


def test_first_build_that_does_not_start(make_account):
    account = make_account()
    account.programs.version = (1, "error while loading shared libraries: libatomic.so.1: cannot open")
    account.set_key("abc")
    account.wait_until_fetched(5)
    info = account.player_info()
    assert (info["state"], info["error"]) == ("none", "libatomic1 is missing")


def test_expired_build(make_account, now):
    account = make_account()
    account.set_key("abc")
    account.wait_until_fetched(5)
    now["time"] = BUILT + datetime.timedelta(days=91)
    assert account.player_info()["state"] == "expired"
    with pytest.raises(Unavailable) as expired:
        account.usable()
    assert expired.value.reason == "expired"
    account.programs.version = (0, f"1.3.9.2 build {int(now['time'].timestamp())}")
    account.expired()
    account.wait_until_fetched(5)
    assert account.player_info()["state"] == "ready"


def test_update_now_needs_the_key(make_account):
    with pytest.raises(Conflict):
        make_account().update_now()


def test_connect(make_account):
    account = make_account(pairing=0, manage_avahi=True)
    with pytest.raises(Conflict):
        account.connect()  # no key
    account.set_key("abc")
    account.wait_until_fetched(5)
    stopped = []
    account.before_connect = lambda: stopped.append(True)
    account.connect()
    account.wait_until_connected(5)
    info = account.info()
    assert (info["connected"], info["connect_result"], info["connecting"]) == (True, "connected", False)
    assert account.pairings[0][:4] == [account.program, "--pair", "-n", "Kinderzimmer"]
    assert stopped == [True]
    avahi = [c for c in account.programs.commands if "systemctl" in c]
    assert [c[3] for c in avahi] == ["stop", "start"]
    account.disconnect()
    assert not account.info()["connected"]


def test_connect_that_nobody_answers(make_account):
    account = make_account(pairing=None)
    account.set_key("abc")
    account.wait_until_fetched(5)
    account.connect()
    account.wait_until_connected(5)
    assert (account.info()["connected"], account.info()["connect_result"]) == (False, "timeout")
    assert not [c for c in account.programs.commands if "systemctl" in c]  # avahi is left alone without hardware


def test_speaker_and_lost_login(make_account):
    account = make_account()
    changes: list[bool] = []
    account.on_speaker = changes.append
    account.set_speaker(True)
    assert account.info()["speaker"] is True
    assert changes == [True]
    with pytest.raises(BadRequest):
        account.set_speaker("yes")
    account.set_key("abc")
    account.wait_until_fetched(5)
    account.connect()
    account.wait_until_connected(5)
    account.lost()
    assert not account.info()["connected"]


def test_no_build_for_this_processor(make_account):
    account = make_account(machine="sparc")
    account.set_key("abc")
    account.wait_until_fetched(5)
    assert account.player_info()["result"] == "failed"


# --- the page ---------------------------------------------------------------------------------------------------------


def test_cards_account_and_player(make_box, tmp_path, logger, now):  # noqa: F811 - the fixture
    box = make_box()
    spotify = Spotify()
    box.control.spotify = SpotifyAccount(str(tmp_path / "s"), logger, lambda: "box", machine="x86_64", run=Program(),
                                         opener=spotify, clock=lambda: now["time"])  # fmt: skip
    admin, user = box.admin(), box.user()
    assert user.get("/api/spotify-account").status == 403
    assert admin.get("/api/spotify-account").json["key"] is False
    assert admin.post("/api/spotify-player", {}).status == 409  # no key
    assert admin.put("/api/spotify-account/key", {"key": ""}).status == 400
    assert admin.put("/api/spotify-account/key", {"key": "abc"}).json["key"] is True
    box.control.spotify.wait_until_fetched(5)
    assert admin.get("/api/spotify-player").json["state"] == "ready"
    assert admin.put("/api/spotify-account/speaker", {"enabled": True}).json["speaker"] is True
    assert admin.post("/api/spotify-player", {}).status == 202
    box.control.spotify.wait_until_fetched(5)
    assert admin.delete("/api/spotify-account").json["connected"] is False
    box.control.spotify = None
    assert admin.get("/api/spotify-account").status == 404


def test_account_module_names_the_builds():
    assert account_module.BUILDS.format("arm64") == "https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz"
