"""Rest api: real https server (self signed certificate), real box control and state machines (DummyPlayer with a
manual clock), requests via http.client."""

import contextlib
import functools
import http.client
import io
import json
import logging
import os
import re
import shutil
import socket
import ssl
import stat
import tarfile
import threading
import time
import urllib.parse
import warnings
from types import SimpleNamespace
from typing import Any

import mutagen
import pytest

from tests.support import (
    ONE_SECOND_MP3,
    TAG_ALBUM,
    TAG_UNKNOWN_ALBUM,
    TAG_UNMAPPED,
    ApiClient,
    FakeRadioPlayer,
    LoopbackPubSub,
    make_volumes,
    wait_for,
)
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.backup import verify
from tinyjukebox.control import BoxControl
from tinyjukebox.data import targetalbumtools
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.targets import Targets
from tinyjukebox.data.volumelimits import VolumeLimits
from tinyjukebox.devices.bluetooth.virtual import VirtualHeadset
from tinyjukebox.headset import BluetoothVolumeControl, Headset
from tinyjukebox.images import Images
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, RFID_STATE, Button, RfidState
from tinyjukebox.rgbled import Color, RGBLed
from tinyjukebox.stm.playradio import PlayRadio
from tinyjukebox.stm.stmoperation import States, STMOperation
from tinyjukebox.stm.stmplayalbum import STMPlayAlbum
from tinyjukebox.stm.stmvolume import STMVolume
from tinyjukebox.volumemanager import VolumeProfile
from tinyjukebox.web import auth, server, uploads
from tinyjukebox.web.auth import UsersFile
from tinyjukebox.web.certificates import create_self_signed_certificate, create_ssl_context
from tinyjukebox.web.server import RequestHandler, RestApi
from tinyjukebox.web.uploads import STAGING_FOLDER, safe_name

ADMIN = ("admin", "admin-password")
USER = ("kind", "kind-password")

CONFIG: dict[str, Any] = {
    "playlist-filename": "playlist.m3u",
    "volume": {"max-volume": 80, "default-volume": 40, "volume-step": 5},
}
ALBUMPLAYER = {"rewind-max-time": 5, "update-schedule": 0.1, "pause-between-tracks": 0}


class _FastServer(server._Server):
    """serve_forever polls for shutdown every 0.5 s by default - every test would wait that long in stop()."""

    def serve_forever(self, poll_interval=0.01):
        super().serve_forever(poll_interval)


@pytest.fixture(autouse=True)
def _fast_server(monkeypatch):
    monkeypatch.setattr(server, "_Server", _FastServer)


def test_nagle_is_disabled():
    """Headers and body are written separately - with nagle every response would wait ~40 ms for the delayed ack."""
    assert server.RequestHandler.disable_nagle_algorithm


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """(certificate, private key) - created once per module (openssl), copied into each box."""
    folder = tmp_path_factory.mktemp("certificate")
    files = (str(folder / "tinyjukebox.crt"), str(folder / "tinyjukebox.key"))
    create_self_signed_certificate(*files)
    return files


@functools.cache
def users():
    """Users file content - hashed with a small scrypt cost (fast logins; the real parameters: test_auth.py)."""
    original = auth._SCRYPT_N
    auth._SCRYPT_N = 2**8
    try:
        return {
            ADMIN[0]: {"password-hash": auth.hash_password(ADMIN[1]), "role": "admin"},
            USER[0]: {"password-hash": auth.hash_password(USER[1]), "role": "user"},
        }
    finally:
        auth._SCRYPT_N = original


class RecordingVolumeControl:
    def __init__(self):
        self.volumes = []

    def set_volume(self, volume):
        self.volumes.append(volume)


def quote(text):
    return urllib.parse.quote(text, safe="")


class Box:
    """Tags/targets/activities, STMOperation (DummyPlayer with a manual clock), STMVolume, BoxControl and the rest
    api."""

    def __init__(self, library, logger, tmp_path, rgbled, clock, config=None, **api_config):
        self.library = library
        self.logger = logger
        self.config = config or CONFIG
        self.pubsub = LoopbackPubSub(logger)
        self.volume_control = RecordingVolumeControl()
        headphones_control = BluetoothVolumeControl(self.pubsub, logger)
        self.volume_file = os.path.join(os.path.dirname(library.tags_file), "volume.json")
        self.volumes = make_volumes(logger, self.volume_control, self.config["volume"], headphones_control,
                                    VolumeLimits(self.volume_file, logger))  # fmt: skip
        self.volumemanager = self.volumes.manager(VolumeProfile.SPEAKER)
        self.stmvolume = STMVolume(self.pubsub, self.volumes, logger)
        player_factory = functools.partial(DummyPlayer, clock=clock)

        def factory(target, activity):
            return STMPlayAlbum(ALBUMPLAYER, self.pubsub, logger,
                                rgbled, target, activity, player_factory)  # fmt: skip

        def radio_factory(station):
            return PlayRadio(logger, rgbled, station, FakeRadioPlayer, self.pubsub)

        self.stmoperation = STMOperation(self.pubsub, logger, rgbled, library.tags, library.targets,
                                         library.activities, factory, poll_interval=0.1,
                                         radio_factory=radio_factory)  # fmt: skip
        self.output = AudioOutput()
        self.headset = Headset(self.pubsub, logger, rgbled, self.output, self.volumes, headphones_control,
                               self.stmoperation)  # fmt: skip
        self.virtual_headset = VirtualHeadset(self.pubsub, logger, search_time=0.05)
        self.control = BoxControl(self.config, self.pubsub, self.volumes, self.stmoperation, library.tags,
                                  library.targets, library.activities, logger, rgbled,
                                  Images(os.path.join(os.path.dirname(library.tags_file), "images")),
                                  self.headset, self.virtual_headset,
                                  data_files=[library.tags_file, library.targets_file, library.activities_file,
                                              self.volume_file])  # fmt: skip
        self.ssl_folder = tmp_path / "ssl"
        self.users_file = str(self.ssl_folder / "users.yaml")
        self.certificate = str(self.ssl_folder / "tinyjukebox.crt")
        self.api_config = {"host": "127.0.0.1", "port": 0, "certificate": self.certificate,
                           "private-key": str(self.ssl_folder / "tinyjukebox.key"), "users-file": self.users_file,
                           **api_config}  # fmt: skip
        self.api: RestApi  # created by start()

    def create_api(self):
        return RestApi(self.api_config, self.control, self.library.targets, self.logger)

    def start(self):
        self.headset.start()
        self.stmvolume.start()
        self.stmoperation.start()
        self.virtual_headset.start()
        self.api = self.create_api()
        self.api.start()
        self.client = ApiClient(self.api.port, self.certificate)

    def stop(self):
        self.api.stop()
        self.virtual_headset.stop()
        if self.stmoperation.get_active_state() != States.END:
            self.stmoperation.stop()
        self.stmvolume.stop()
        self.headset.stop()

    def settle(self):
        """Wait until the state machines have processed all queued events."""
        self.stmoperation.wait_until_idle()
        album = self.stmoperation.get_album()
        if isinstance(album, (STMPlayAlbum, PlayRadio)):
            album.wait_until_idle()
        self.stmvolume.wait_until_idle()

    def album_info(self):
        album = self.stmoperation.get_album()
        assert album is not None
        return album.get_info()

    def place_tag(self, uid):
        self.pubsub.deliver(RFID_STATE, RfidState(uid))
        self.settle()

    def admin(self):
        return ApiClient(self.api.port, self.certificate).login(*ADMIN)

    def user(self):
        return ApiClient(self.api.port, self.certificate).login(*USER)


@pytest.fixture
def make_box(library, logger, tmp_path, rgbled, clock, certificate):
    """make_box(copy_certificate=True, config=None, **api_config) - a started box, stopped after the test."""
    with contextlib.ExitStack() as stack:

        def make(copy_certificate=True, config=None, **api_config):
            box = Box(library, logger, tmp_path, rgbled, clock, config, **api_config)
            box.ssl_folder.mkdir(exist_ok=True)
            if copy_certificate:
                shutil.copy2(certificate[0], box.api_config["certificate"])
                shutil.copy2(certificate[1], box.api_config["private-key"])
            UsersFile.write(box.users_file, users())
            box.start()
            stack.callback(box.stop)
            return box

        yield make


@pytest.fixture
def box(make_box):
    return make_box()


def mp3(folder, name, **tags):
    """Copy of the one second test mp3 with the given id3 tags (title, tracknumber, ...)."""
    filename = os.path.join(str(folder), name)
    shutil.copy(ONE_SECOND_MP3, filename)
    if tags:
        audio = mutagen.File(filename, easy=True)
        if audio.tags is None:
            audio.add_tags()
        for key, value in tags.items():
            audio[key] = str(value)
        audio.save()
    return filename


def make_album_folder(folder, *names):
    os.makedirs(folder)
    for name in names:
        mp3(folder, name)
    with open(os.path.join(folder, "playlist.m3u"), "w") as f:
        f.write("".join(name + "\n" for name in names))


def upload_files(client, upload_id, filenames):
    for filename in filenames:
        with open(filename, "rb") as f:
            response = client.put(f"/api/uploads/{upload_id}/files/{quote(os.path.basename(filename))}", raw=f.read())
        assert response.status == 201, response.content


def read_playlist(library, folder):
    with open(os.path.join(library.albums_folder, folder, "playlist.m3u")) as f:
        return f.read().splitlines()


def served(client):
    try:
        return client.get("/").status == 200
    except (OSError, http.client.HTTPException):
        return False


# --- https / certificate ---------------------------------------------------------------------------------------------


def test_self_signed_certificate_is_created(make_box):
    box = make_box(copy_certificate=False)
    key_file = box.api_config["private-key"]
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    # Client verifies the certificate chain and the host name 'localhost'
    assert box.client.get("/").status == 200
    assert len(box.api.fingerprint.split(":")) == 32


def test_private_key_mode_and_umask(tmp_path):
    """The key file is created with mode 600 - without changing the umask of the process (other threads)."""
    certificate, key = str(tmp_path / "ssl" / "tinyjukebox.crt"), str(tmp_path / "ssl" / "tinyjukebox.key")
    previous = os.umask(0o002)
    try:
        create_self_signed_certificate(certificate, key)
        umask = os.umask(0o002)
    finally:
        os.umask(previous)
    assert umask == 0o002
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    # an existing key file with a wrong mode is fixed
    os.chmod(key, 0o644)
    create_self_signed_certificate(certificate, key)
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_existing_certificate_is_kept(box, certificate):
    fingerprint = box.api.fingerprint
    box.api.stop()
    box.api = box.create_api()
    box.api.start()
    assert box.api.fingerprint == fingerprint
    with open(certificate[0]) as original, open(box.certificate) as used:
        assert original.read() == used.read()


def test_plain_http_is_refused(box):
    connection = http.client.HTTPConnection("127.0.0.1", box.api.port, timeout=5)

    def plain_request():
        connection.request("GET", "/api/status")
        return connection.getresponse()

    with pytest.raises((http.client.HTTPException, ConnectionError, OSError)):
        plain_request()
    connection.close()


def test_old_tls_versions_are_refused(box):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = ssl.TLSVersion.TLSv1
            context.maximum_version = ssl.TLSVersion.TLSv1_1
    except (ValueError, ssl.SSLError):
        pytest.skip("tls 1.1 not supported by the local openssl")

    def old_tls_request(sock):
        with context.wrap_socket(sock) as tls:
            tls.sendall(b"GET / HTTP/1.1\r\n\r\n")
            tls.recv(1)

    with (
        socket.create_connection(("127.0.0.1", box.api.port), timeout=5) as sock,
        pytest.raises((ssl.SSLError, OSError)),
    ):
        old_tls_request(sock)


def test_security_headers(box):
    response = box.client.get("/")
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Frame-Options") == "DENY"
    assert response.getheader("Strict-Transport-Security")
    assert b"Tiny Jukebox" in response.content
    for path in ["/app.js", "/style.css", "/favicon.svg"]:
        assert box.client.get(path).status == 200
    texts = box.client.get("/texts/de.json")  # the texts of the page are there before the login
    assert texts.status == 200
    assert texts.getheader("Content-Type") == "application/json; charset=utf-8"
    assert json.loads(texts.content)["login.submit"] == "Anmelden"
    assert box.client.get("/texts/../server.py").status in (401, 404)


def test_connection_limit(certificate, logger):
    """Connections beyond max_connections are closed right away; finished connections free their slot."""
    handler = type("Handler", (RequestHandler,), {"api": SimpleNamespace(logger=logger)})
    httpd = server._Server(("127.0.0.1", 0), handler, create_ssl_context(*certificate), max_connections=2)
    thread = threading.Thread(target=httpd.serve_forever, name="test-server", daemon=True)
    thread.start()
    address = ("127.0.0.1", httpd.server_address[1])
    client = ApiClient(address[1], certificate[0])
    try:
        # two clients that never start the tls handshake - both worker threads wait for them
        idle = [socket.create_connection(address, timeout=5) for _ in range(2)]
        try:
            with socket.create_connection(address, timeout=5) as third, contextlib.suppress(ConnectionResetError):
                assert third.recv(1) == b""  # closed immediately (not after the handshake timeout)
            idle[0].settimeout(0.1)
            with pytest.raises(TimeoutError):
                idle[0].recv(1)  # the first connections are kept open
            assert not served(client)
        finally:
            for sock in idle:
                sock.close()
        # the slots are released - also after normal requests
        for _ in range(5):
            assert wait_for(lambda: served(client))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


# --- access control --------------------------------------------------------------------------------------------------

ROUTES = [("GET", "/api/status"), ("GET", "/api/tags"), ("PATCH", "/api/tags/x"), ("PUT", "/api/tags/x/album"),
          ("DELETE", "/api/tags/x"), ("GET", "/api/albums"), ("POST", "/api/albums"), ("GET", "/api/albums/1"),
          ("GET", "/api/albums/1/cover"), ("DELETE", "/api/albums/1"), ("GET", "/api/albums/new"),
          ("POST", "/api/albums/new"), ("GET", "/api/activities"), ("POST", "/api/activities/rebuild"),
          ("POST", "/api/sanity-check"), ("POST", "/api/player/next"), ("PUT", "/api/volume"),
          ("POST", "/api/buttons/next"), ("POST", "/api/uploads"), ("PUT", "/api/uploads/x/files/a.mp3"),
          ("POST", "/api/uploads/x/finish"), ("GET", "/api/session"), ("PUT", "/api/session/password"),
          ("GET", "/api/users"), ("POST", "/api/users"), ("PUT", "/api/users/x/password"),
          ("DELETE", "/api/users/x"), ("GET", "/api/box/hardware")]  # fmt: skip
ADMIN_ROUTES = [("PATCH", f"/api/tags/{TAG_ALBUM}"), ("PUT", f"/api/tags/{TAG_ALBUM}/album"),
                ("DELETE", f"/api/tags/{TAG_ALBUM}"), ("POST", "/api/albums"), ("DELETE", "/api/albums/2"),
                ("GET", "/api/albums/new"), ("POST", "/api/albums/new"), ("POST", "/api/activities/rebuild"),
                ("POST", "/api/sanity-check"), ("GET", "/api/uploads"), ("POST", "/api/uploads"),
                ("PUT", "/api/uploads/x/files/a.mp3"), ("POST", "/api/uploads/x/finish"), ("GET", "/api/users"),
                ("POST", "/api/users"), ("PUT", f"/api/users/{ADMIN[0]}/password"),
                ("DELETE", f"/api/users/{ADMIN[0]}"), ("GET", "/api/box/hardware")]  # fmt: skip


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_login_required(box, method, path):
    response = box.client.request(method, path, {} if method in ("POST", "PUT", "PATCH") else None)
    assert response.status == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_are_forbidden_for_users(box, method, path):
    user = box.user()
    response = user.request(method, path, {"name": "x"} if method in ("POST", "PUT", "PATCH") else None)
    assert response.status == 403
    assert TAG_ALBUM in box.library.tags.tags
    assert "2" in box.library.targets.targets  # nothing changed
    assert UsersFile.read(box.users_file) == users()


def test_user_may_control_the_player(box):
    user = box.user()
    assert user.get("/api/status").status == 200
    assert user.put("/api/volume", {"volume": 30}).status == 200
    assert user.post("/api/buttons/volume-up").status == 204
    assert user.get("/api/session").json == {"username": USER[0], "role": "user", "mode": "box", "idle_timeout": 1800}


def test_hardware_of_a_box_without_drivers(box):
    """The technical view of the tab "System": a box that runs without device drivers says so."""
    hardware = box.admin().get("/api/box/hardware").json
    assert (hardware["available"], hardware["buttons"], hardware["leds"], hardware["readers"]) == (False, [], [], [])
    assert hardware["time"] == pytest.approx(time.time(), abs=60)


def test_the_page_is_logged_out_when_nothing_was_done_on_it_for_too_long(make_box):
    """The page asks all the time, so a request alone keeps no session alive: the page says with every request how
    long ago the user did something. A request of a program, with the token in the header, is an action itself."""
    box = make_box(**{"idle-timeout": 100})
    now = [1000.0]
    box.api.sessions._clock = lambda: now[0]
    login = box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})
    assert login.json["idle_timeout"] == 100
    cookie = login.headers["Set-Cookie"].split(";")[0]

    def page(idle=None):
        headers = {"Cookie": cookie} if idle is None else {"Cookie": cookie, "X-TinyJukebox-Idle": str(idle)}
        return box.client.get("/api/session", headers=headers).status

    now[0] = 1090
    assert page(idle=90) == 200
    assert page() == 200  # e.g. an image the browser loads: says nothing
    assert page(idle="soon") == 200
    now[0] = 1101
    assert page(idle=101) == 401
    assert page(idle=0) == 401  # the session is gone, not paused

    login = box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})
    cookie = login.headers["Set-Cookie"].split(";")[0]
    now[0] = 1190
    assert page(idle=3) == 200  # the user did something three seconds ago
    now[0] = 1280
    assert page() == 200
    now[0] = 1288
    assert page() == 401

    program = box.admin()
    for now[0] in (1380, 1470, 1560, 1700):  # every request is an action, however long the pause before it
        assert program.get("/api/session").status == 200


def test_the_limit_of_the_page_can_be_switched_off(make_box):
    box = make_box(**{"idle-timeout": 0})
    now = [1000.0]
    box.api.sessions._clock = lambda: now[0]
    login = box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})
    assert login.json["idle_timeout"] == 0
    now[0] = 90000
    assert box.client.get("/api/session", headers={"Cookie": login.headers["Set-Cookie"].split(";")[0]}).status == 200


def test_wrong_password_and_unknown_user(box):
    for credentials in [(ADMIN[0], "wrong"), ("nobody", ADMIN[1]), (ADMIN[0], "")]:
        response = box.client.post("/api/login", {"username": credentials[0], "password": credentials[1]})
        assert response.status == 401
        assert "token" not in response.json
    assert box.client.post("/api/login", {"username": ADMIN[0]}).status == 400


def test_login_is_throttled(box):
    for _ in range(5):
        assert box.client.post("/api/login", {"username": ADMIN[0], "password": "guess"}).status == 401
    response = box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})
    assert response.status == 429  # even the right password is refused during the lockout
    assert int(response.getheader("Retry-After")) > 0


def test_bearer_token_and_logout(box):
    admin = box.admin()
    assert admin.get("/api/session").json["role"] == "admin"
    assert admin.post("/api/logout").status == 204
    assert admin.get("/api/session").status == 401
    assert admin.get("/api/session", token="invalid").status == 401


def test_cookie_login_needs_csrf_header(box):
    response = box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]})
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Strict" in cookie
    cookie = cookie.split(";")[0]
    client = ApiClient(box.api.port, box.certificate)
    assert client.get("/api/status", headers={"Cookie": cookie}).status == 200
    # a request from another site cannot set custom headers
    assert client.put("/api/volume", {"volume": 10}, headers={"Cookie": cookie}).status == 403
    response = client.put("/api/volume", {"volume": 10}, headers={"Cookie": cookie, "X-TinyJukebox-Request": "1"})
    assert response.status == 200


def test_removed_user_and_changed_role_apply_immediately(box):
    admin = box.admin()
    user = box.user()
    current = UsersFile.read(box.users_file)
    current[ADMIN[0]]["role"] = "user"
    del current[USER[0]]
    mtime = os.stat(box.users_file).st_mtime_ns
    UsersFile.write(box.users_file, current)
    os.utime(box.users_file, ns=(mtime + 10**9, mtime + 10**9))  # a new mtime even on coarse file systems
    assert user.get("/api/status").status == 401
    assert admin.post("/api/sanity-check").status == 403
    assert admin.get("/api/status").status == 200


def test_missing_users_file_means_nobody_can_log_in(box):
    os.remove(box.users_file)
    assert box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]}).status == 401


def test_list_add_and_remove_users(box):
    admin = box.admin()
    both = [{"username": ADMIN[0], "role": "admin"}, {"username": USER[0], "role": "user"}]
    assert admin.get("/api/users").json == both

    response = admin.post("/api/users", {"username": "oma", "password": "oma-password", "role": "admin"})
    assert (response.status, response.json) == (201, {"username": "oma", "role": "admin"})
    assert admin.post("/api/users", {"username": "opa", "password": "opa-password"}).json["role"] == "user"
    assert [u["username"] for u in admin.get("/api/users").json] == [ADMIN[0], USER[0], "oma", "opa"]
    assert stat.S_IMODE(os.stat(box.users_file).st_mode) == 0o600
    oma = ApiClient(box.api.port, box.certificate).login("oma", "oma-password")
    assert oma.post("/api/sanity-check").status == 200

    assert admin.delete(f"/api/users/{quote('oma')}").status == 204
    assert oma.get("/api/status").status == 401  # her session is gone
    assert box.client.post("/api/login", {"username": "oma", "password": "oma-password"}).status == 401
    assert admin.delete("/api/users/oma").status == 404
    assert admin.delete(f"/api/users/{ADMIN[0]}").status == 409  # an admin always remains
    assert [u["username"] for u in admin.get("/api/users").json] == [ADMIN[0], USER[0], "opa"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"username": USER[0], "password": "new-password"}, 409),
        ({"username": "neu", "password": "short"}, 400),
        ({"username": "neu"}, 400),
        ({"username": "", "password": "new-password"}, 400),
        ({"username": " neu", "password": "new-password"}, 400),
        ({"username": "a:b", "password": "new-password"}, 400),
        ({"username": "n" * 65, "password": "new-password"}, 400),
        ({"username": "neu", "password": "new-password", "role": "root"}, 400),
        ({"password": "new-password"}, 400),
    ],
)
def test_invalid_users_are_rejected(box, body, status):
    assert box.admin().post("/api/users", body).status == status
    assert UsersFile.read(box.users_file) == users()


def test_set_password(box):
    admin = box.admin()
    user = box.user()
    assert admin.put(f"/api/users/{USER[0]}/password", {"password": "short"}).status == 400
    assert admin.put("/api/users/nobody/password", {"password": "new-password"}).status == 404
    assert user.get("/api/status").status == 200

    assert admin.put(f"/api/users/{USER[0]}/password", {"password": "new-password"}).status == 204
    assert user.get("/api/status").status == 401  # logged in with the old password
    assert box.client.post("/api/login", {"username": USER[0], "password": USER[1]}).status == 401
    response = box.client.post("/api/login", {"username": USER[0], "password": "new-password"})
    assert (response.status, response.json["role"]) == (200, "user")

    # the own password: this session stays, the other ones of the admin end
    other = box.admin()
    assert admin.put(f"/api/users/{ADMIN[0]}/password", {"password": "new-admin-password"}).status == 204
    assert admin.get("/api/status").status == 200
    assert other.get("/api/status").status == 401


OWN_PASSWORD = "/api/session/password"


def test_change_own_password(box):
    user = box.user()
    other = box.user()
    assert user.put(OWN_PASSWORD, {"current_password": USER[1], "password": "short"}).status == 400
    assert user.put(OWN_PASSWORD, {"password": "new-password"}).status == 400
    assert user.put(OWN_PASSWORD, {"current_password": "wrong", "password": "new-password"}).status == 403
    assert UsersFile.read(box.users_file) == users()

    assert user.put(OWN_PASSWORD, {"current_password": USER[1], "password": "new-password"}).status == 204
    assert user.get("/api/status").status == 200  # this session stays, the other ones end
    assert other.get("/api/status").status == 401
    assert box.client.post("/api/login", {"username": USER[0], "password": USER[1]}).status == 401
    response = box.client.post("/api/login", {"username": USER[0], "password": "new-password"})
    assert (response.status, response.json["role"]) == (200, "user")
    assert UsersFile.read(box.users_file)[ADMIN[0]] == users()[ADMIN[0]]


def test_wrong_current_password_is_throttled(box):
    user = box.user()
    for _ in range(5):
        assert user.put(OWN_PASSWORD, {"current_password": "guess", "password": "new-password"}).status == 403
    response = user.put(OWN_PASSWORD, {"current_password": USER[1], "password": "new-password"})
    assert response.status == 429  # even the right password is refused during the lockout
    assert int(response.getheader("Retry-After")) > 0
    # the same lockout as for the login
    assert box.client.post("/api/login", {"username": ADMIN[0], "password": ADMIN[1]}).status == 429
    assert user.get("/api/status").status == 200  # the session stays
    assert UsersFile.read(box.users_file) == users()


def test_errors(box):
    admin = box.admin()
    assert admin.get("/api/unknown").status == 404
    assert admin.request("POST", "/api/status", {}).status == 405
    response = admin.request("PUT", "/api/volume", raw=b"{broken", headers={"Content-Type": "application/json"})
    assert response.status == 400
    assert admin.request("PUT", "/api/volume", raw=b"[1]").status == 400
    assert admin.request("PUT", "/api/volume", raw=b"x" * 70000).status == 413
    # the server is still fine
    assert admin.get("/api/status").status == 200


# --- status / player -------------------------------------------------------------------------------------------------


def test_status_without_tag(box):
    status = box.admin().get("/api/status").json
    assert status["state"] == "wait_for_tag"
    assert status["tag"] is None
    assert status["album"] is None
    assert status["volume"] == {
        "volume": 40, "max_volume": 80, "default_volume": 40, "step": 5, "output": "speaker",
        "profiles": {"speaker": {"volume": 40, "default_volume": 40, "max_volume": 80},
                     "headphones": {"volume": 40, "default_volume": 40, "max_volume": 75}},
    }  # fmt: skip
    assert status["bluetooth"] == {
        "available": True, "virtual": True, "device": None, "connected": False, "battery": None, "volume": None,
        "pairing": False, "pairing_result": None, "default_volume": 40, "max_volume": 75,
    }  # fmt: skip


def test_status_during_playback(box, clock):
    admin = box.admin()
    box.place_tag(TAG_ALBUM)
    clock.advance(0.1)
    status = admin.get("/api/status").json
    assert status["state"] == "play_album"
    assert status["tag"]["uid"] == TAG_ALBUM
    assert status["tag"]["present"]
    album = status["album"]
    assert album["playback"]["state"] == "play"
    assert album["id"] == "1"
    assert album["folder"] == "short"
    assert album["track_count"] == 3
    assert [t["filename"] for t in album["tracks"]] == ["1.mp3", "2.mp3", "3.mp3"]
    assert album["tracks"][0]["length"] == pytest.approx(0.34, abs=0.05)  # SHORT_MP3
    assert album["playback"]["track_id"] == 0
    assert 0 <= album["playback"]["position"] <= album["playback"]["length"]
    assert album["playback"]["position"] == pytest.approx(0.1)


def test_player_commands(box):
    admin = box.admin()
    assert admin.post("/api/player/next").status == 409  # nothing is playing
    box.place_tag(TAG_ALBUM)
    assert box.album_info()["state"] == "play"
    for command, track_id in [("next", 1), ("prev", 0), ("next", 1), ("restart", 0)]:
        assert admin.post("/api/player/" + command).status == 204
        box.settle()
        assert box.album_info()["track"]["track_id"] == track_id, command


def test_volume(box):
    admin = box.admin()
    assert admin.get("/api/volume").json["volume"] == 40
    assert admin.put("/api/volume", {"volume": 60}).json["volume"] == 60
    assert box.volume_control.volumes[-1] == 60
    assert admin.put("/api/volume", {"volume": 95}).json["volume"] == 80  # max-volume
    for invalid in [-1, 101, "50", None, True]:
        assert admin.put("/api/volume", {"volume": invalid}).status == 400


@pytest.mark.parametrize(
    ("button", "action", "topic", "message"),
    [
        ("volume-up", "press", BUTTON_PRESSED, Button.VOLUME_UP),
        ("volume-down", "press", BUTTON_PRESSED, Button.VOLUME_DOWN),
        ("next", "press", BUTTON_PRESSED, Button.NEXT),
        ("prev", "press", BUTTON_PRESSED, Button.PREV),
        ("volume-up", "hold", BUTTON_HELD, Button.VOLUME_UP),
        ("next", "hold", BUTTON_HELD, Button.NEXT),
    ],
)
def test_buttons_publish_the_hardware_messages(box, button, action, topic, message):
    assert box.admin().post("/api/buttons/" + button, {"action": action}).status == 204
    assert box.pubsub.messages(topic) == [message]
    box.settle()


def test_volume_buttons_work_like_the_hardware(box):
    admin = box.admin()
    admin.post("/api/buttons/volume-up")
    admin.post("/api/buttons/volume-up")
    admin.post("/api/buttons/volume-down")
    box.settle()
    assert box.volumemanager.get_volume() == 45
    assert box.volume_control.volumes[-1] == 45


def test_track_buttons_work_like_the_hardware(box):
    admin = box.admin()
    box.place_tag(TAG_ALBUM)
    assert box.album_info()["state"] == "play"
    admin.post("/api/buttons/next")
    box.settle()
    assert box.album_info()["track"]["track_id"] == 1


def test_unknown_button(box):
    admin = box.admin()
    assert admin.post("/api/buttons/eject").status == 404
    assert admin.post("/api/buttons/next", {"action": "double-click"}).status == 400
    assert box.pubsub.published == [m for m in box.pubsub.published if "position" not in m[0]]


# --- tags ------------------------------------------------------------------------------------------------------------


def test_list_tags(box):
    admin = box.admin()
    tags = {t["uid"]: t for t in admin.get("/api/tags").json}
    assert set(tags) == {TAG_ALBUM, TAG_UNMAPPED, TAG_UNKNOWN_ALBUM}
    assert tags[TAG_ALBUM]["album_id"] == "1"
    assert tags[TAG_ALBUM]["album_exists"]
    assert tags[TAG_UNMAPPED]["album_id"] is None
    assert tags[TAG_UNKNOWN_ALBUM]["album_exists"] is False
    assert [t["uid"] for t in admin.get("/api/tags?unmapped=1").json] == [TAG_UNMAPPED]
    assert admin.get("/api/tags/" + quote(TAG_ALBUM)).json["name"] == "album"
    assert admin.get("/api/tags/0:0:0:0").status == 404


def test_new_tag_appears_after_placing_it(box):
    box.place_tag("9:9:9:9")
    admin = box.admin()
    assert "9:9:9:9" in [t["uid"] for t in admin.get("/api/tags?unmapped=1").json]
    assert admin.get("/api/tags/current").json["uid"] == "9:9:9:9"


def test_set_tag_name(box):
    admin = box.admin()
    response = admin.patch("/api/tags/" + quote(TAG_UNMAPPED), {"name": "  Bär  "})
    assert response.json["name"] == "Bär"
    with open(box.library.tags_file) as f:
        assert json.load(f)[TAG_UNMAPPED]["tag"]["name"] == "Bär"
    assert admin.patch("/api/tags/" + quote(TAG_UNMAPPED), {"name": ""}).status == 400
    assert admin.patch("/api/tags/0:0:0:0", {"name": "x"}).status == 404


def test_link_and_unlink_tag(box):
    admin = box.admin()
    path = f"/api/tags/{quote(TAG_UNMAPPED)}/album"
    assert admin.put(path, {"album_id": "2"}).json["album_id"] == "2"
    assert str(box.library.tags.get_tag(TAG_UNMAPPED).target_id) == "2"
    assert admin.put(path, {"album_id": "99"}).status == 404
    assert admin.delete(path).json["album_id"] is None
    assert str(box.library.tags.get_tag(TAG_UNMAPPED).target_id) == "-1"


def test_link_current_tag_starts_playback(box):
    admin = box.admin()
    assert admin.put("/api/tags/current/album", {"album_id": "1"}).status == 404  # no tag on the box
    box.place_tag(TAG_UNMAPPED)
    assert box.stmoperation.get_active_state() == States.UNMAPPED_TAG
    response = admin.put("/api/tags/current/album", {"album_id": "1"})
    assert response.json["uid"] == TAG_UNMAPPED
    # no need to remove and replace the tag
    box.settle()
    assert box.stmoperation.get_active_state() == States.PLAY_ALBUM
    assert admin.get("/api/status").json["album"]["id"] == "1"


def test_relink_playing_tag_switches_album(box):
    admin = box.admin()
    box.place_tag(TAG_ALBUM)
    assert admin.get("/api/status").json["album"]["id"] == "1"
    admin.put("/api/tags/current/album", {"album_id": "2"})
    box.settle()
    assert admin.get("/api/status").json["album"]["id"] == "2"


def test_remove_tag(box):
    admin = box.admin()
    assert TAG_ALBUM in box.library.activities.activities
    assert admin.delete("/api/tags/" + quote(TAG_ALBUM)).status == 204
    assert TAG_ALBUM not in box.library.tags.tags
    assert TAG_ALBUM not in box.library.activities.activities
    assert admin.delete("/api/tags/" + quote(TAG_ALBUM)).status == 404


# --- albums ----------------------------------------------------------------------------------------------------------


def test_list_albums(box):
    admin = box.admin()
    albums = admin.get("/api/albums").json
    assert [a["id"] for a in albums] == ["1", "2"]
    assert albums[0]["tags"] == [{"uid": TAG_ALBUM, "name": "album"}]
    assert albums[0]["track_count"] == 3
    assert albums[0]["title"] == "short"
    assert albums[1]["length"] == pytest.approx(12, abs=0.5)  # tracks of 4, 3 and 5 s
    assert [a["id"] for a in admin.get("/api/albums?unmapped=1").json] == ["2"]


def test_album_details(box):
    album = box.admin().get("/api/albums/2").json
    assert album["folder"] == "simple_album_b"
    assert album["track_count"] == len(album["tracks"]) > 0
    assert [t["index"] for t in album["tracks"]] == list(range(len(album["tracks"])))
    assert album["length"] == pytest.approx(sum(t["length"] for t in album["tracks"]), abs=0.1)
    assert box.admin().get("/api/albums/99").status == 404


def test_album_title_from_id3_tags(box, library):
    folder = os.path.join(library.albums_folder, "short")
    for i in (1, 2, 3):
        mp3(folder, f"{i}.mp3", title=f"Kapitel {i}", album="Beispielalbum", artist="Beispielautor")
    album = box.admin().get("/api/albums/1").json
    assert album["title"] == "Beispielalbum"
    assert album["artist"] == "Beispielautor"
    assert [t["title"] for t in album["tracks"]] == ["Kapitel 1", "Kapitel 2", "Kapitel 3"]


def test_cover(box, library):
    from mutagen.id3 import APIC, ID3

    admin = box.admin()
    assert admin.get("/api/albums/1/cover").status == 404
    # embedded cover
    first = os.path.join(library.albums_folder, "short", "1.mp3")
    tags = mutagen.File(first).tags or ID3()
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"\x89PNG embedded"))
    tags.save(first)
    response = admin.get("/api/albums/1/cover")
    assert response.status == 200
    assert response.content == b"\x89PNG embedded"
    assert response.getheader("Content-Type") == "image/png"
    # cover file in the folder wins
    with open(os.path.join(library.albums_folder, "short", "folder.jpg"), "wb") as f:
        f.write(b"\xff\xd8 jpeg")
    response = admin.get("/api/albums/1/cover")
    assert response.content == b"\xff\xd8 jpeg"
    assert response.getheader("Content-Type") == "image/jpeg"
    assert admin.get("/api/albums/1").json["cover"] is None  # no cover of its own
    # ... and the cover of the album, an image of the images folder, over both
    assert admin.request("PUT", "/api/albums/1/cover", raw=PNG).status == 200
    assert admin.get("/api/albums/1/cover").content == PNG


def test_add_album_from_folder(box, library):
    admin = box.admin()
    make_album_folder(os.path.join(library.albums_folder, "neu"), "a.mp3")
    for title in (None, "", "   ", 7, "x" * 201, "zwei\nZeilen"):
        assert admin.post("/api/albums", {"folder": "neu", "title": title}).status == 400, title
    response = admin.post("/api/albums", {"folder": "neu", "title": "  Das neue Album "})
    assert response.status == 201
    assert response.json["folder"] == "neu"
    assert response.json["title"] == "Das neue Album"
    with open(library.targets_file) as f:
        assert json.load(f)[response.json["id"]]["title"] == "Das neue Album"
    assert admin.post("/api/albums", {"folder": "neu", "title": "Noch einmal"}).status == 409
    assert admin.post("/api/albums", {"folder": "gibt-es-nicht", "title": "Titel"}).status == 400
    assert admin.post("/api/albums", {"folder": "../../etc", "title": "Titel"}).status == 400
    assert admin.post("/api/albums", {"folder": "/etc", "title": "Titel"}).status == 400


def test_album_title_is_changed(box, library):
    """An album without a title of its own shows the album of its tracks or its folder; a title that was given
    wins and is stored in the entry of the album."""
    admin = box.admin()
    assert admin.get("/api/albums/1").json["title"] == "short"
    with open(library.targets_file) as f:
        assert "title" not in json.load(f)["1"]
    response = admin.request("PATCH", "/api/albums/1", {"title": " Kurz und gut "})
    assert response.status == 200
    assert response.json["title"] == "Kurz und gut"
    assert admin.get("/api/albums/1").json["title"] == "Kurz und gut"
    assert [a["title"] for a in admin.get("/api/albums").json] == ["Kurz und gut", "simple_album_b"]
    with open(library.targets_file) as f:
        assert json.load(f)["1"]["title"] == "Kurz und gut"
    # the title wins over the album that the tracks name
    for i in (1, 2, 3):
        mp3(os.path.join(library.albums_folder, "short"), f"{i}.mp3", album="Beispielalbum")
    assert admin.get("/api/albums/1").json["title"] == "Kurz und gut"
    for title in (None, "", "  ", 7, "x" * 201):
        assert admin.request("PATCH", "/api/albums/1", {"title": title}).status == 400, title
    assert admin.get("/api/albums/1").json["title"] == "Kurz und gut"
    assert admin.request("PATCH", "/api/albums/99", {"title": "Titel"}).status == 404
    assert box.user().request("PATCH", "/api/albums/1", {"title": "Titel"}).status == 403
    # the title survives a restart
    reloaded = Targets(library.targets_file, library.albums_folder, logging.getLogger("tests"))
    assert reloaded.get_target("1").to_dict()["title"] == "Kurz und gut"


def test_album_cover_is_uploaded(box, library):
    """The cover of an album is kept in the images folder, as the cover of a station is: a backup without the
    albums has it."""
    admin = box.admin()
    folder = os.path.join(library.albums_folder, "short")
    images_folder = os.path.join(os.path.dirname(library.tags_file), "images")
    before = sorted(os.listdir(folder))
    assert admin.get("/api/albums/1/cover").status == 404
    response = admin.request("PUT", "/api/albums/1/cover", raw=JPEG)
    assert response.status == 200
    assert response.json["id"] == "1"
    first = response.json["cover"]
    assert re.fullmatch(r"album-[0-9a-f]{8}\.jpg", first)
    cover = admin.get("/api/albums/1/cover")
    assert cover.content == JPEG
    assert cover.getheader("Content-Type") == "image/jpeg"
    assert os.listdir(images_folder) == [first]
    assert sorted(os.listdir(folder)) == before  # nothing is written into the album folder
    with open(library.targets_file, encoding="utf-8") as f:
        assert json.load(f)["1"]["cover"] == first
    assert next(album for album in admin.get("/api/albums").json if album["id"] == "1")["cover"] == first
    # another image replaces it - also one of another type
    second = admin.request("PUT", "/api/albums/1/cover", raw=PNG).json["cover"]
    cover = admin.get("/api/albums/1/cover")
    assert cover.content == PNG
    assert cover.getheader("Content-Type") == "image/png"
    assert os.listdir(images_folder) == [second]
    assert admin.request("PUT", "/api/albums/1/cover", raw=b"GIF89a" + b"0" * 100).status == 400
    assert admin.request("PUT", "/api/albums/1/cover", raw=b"").status == 400
    assert admin.get("/api/albums/1/cover").content == PNG
    assert admin.request("PUT", "/api/albums/99/cover", raw=JPEG).status == 404
    assert box.user().request("PUT", "/api/albums/1/cover", raw=JPEG).status == 403
    # the album is still what it was: the cover is no track
    assert admin.get("/api/albums/1").json["problems"] == []
    # a cover that an older version stored in the album folder goes when a new one is set
    with open(os.path.join(folder, "cover.jpg"), "wb") as f:
        f.write(JPEG)
    third = admin.request("PUT", "/api/albums/1/cover", raw=PNG).json["cover"]
    assert sorted(os.listdir(folder)) == before
    # the cover goes with the album
    assert os.listdir(images_folder) == [third]
    admin.delete(f"/api/tags/{quote(TAG_ALBUM)}/album")
    assert admin.delete("/api/albums/1").status == 204
    assert os.listdir(images_folder) == []


def test_a_box_without_a_library_moves_the_stored_covers_into_the_images_folder(box, library):
    """Until 0.31 the cover of an album was ``cover.jpg`` or ``cover.png`` in its folder, which a backup without
    the albums does not hold. Only this file is moved: another image of the folder stays and is still shown for an
    album that names no cover."""
    control = box.control
    images_folder = os.path.join(os.path.dirname(library.tags_file), "images")
    short = os.path.join(library.albums_folder, "short")
    other = os.path.join(library.albums_folder, library.targets.get_target("2").album_folder)
    with open(os.path.join(short, "cover.png"), "wb") as f:
        f.write(PNG)
    with open(os.path.join(short, "folder.jpg"), "wb") as f:
        f.write(JPEG)
    with open(os.path.join(other, "front.jpg"), "wb") as f:
        f.write(JPEG)
    assert control.adopt_stored_covers() == 1
    cover = library.targets.get_target("1").cover
    assert os.listdir(images_folder) == [cover]
    assert cover.endswith(".png")
    assert sorted(n for n in os.listdir(short) if not n.endswith((".mp3", ".m3u"))) == ["folder.jpg"]
    assert control.get_cover("1") == (PNG, "image/png")
    assert library.targets.get_target("2").cover is None
    assert control.get_cover("2") == (JPEG, "image/jpeg")
    assert os.path.isfile(os.path.join(other, "front.jpg"))
    assert control.adopt_stored_covers() == 0
    # an album that names its cover keeps it, whatever lies in its folder
    with open(os.path.join(short, "cover.jpg"), "wb") as f:
        f.write(JPEG)
    assert control.adopt_stored_covers() == 0
    assert control.get_cover("1") == (PNG, "image/png")
    # a stored cover that is no image stays where it is
    with open(os.path.join(other, "cover.jpg"), "wb") as f:
        f.write(b"kein bild")
    assert control.adopt_stored_covers() == 0
    assert os.path.isfile(os.path.join(other, "cover.jpg"))


def test_add_album_outside_the_albums_folder_is_rejected(box, library, tmp_path):
    """Valid album folders that are not inside the albums folder: symlink, '..' and absolute paths."""
    admin = box.admin()
    outside = str(tmp_path / "outside")
    make_album_folder(outside, "a.mp3")
    os.symlink(outside, os.path.join(library.albums_folder, "link"))
    for folder in ["link", "../outside", outside, "short/../../outside", os.path.join(library.albums_folder, "short")]:
        response = admin.post("/api/albums", {"folder": folder})
        assert response.status == 400, folder
    assert admin.post("/api/albums", {"folder": "."}).status == 400  # the albums folder itself
    assert [a["id"] for a in admin.get("/api/albums").json] == ["1", "2"]


def test_scan_and_add_new_albums(box, library):
    admin = box.admin()
    assert admin.get("/api/albums/new").json == []
    for name in ["b", "a"]:
        make_album_folder(os.path.join(library.albums_folder, "neu", name), "1.mp3")
    broken = os.path.join(library.albums_folder, "kaputt")
    os.makedirs(broken)
    with open(os.path.join(broken, "playlist.m3u"), "w") as f:
        f.write("fehlt.mp3\n")
    mp3(os.path.join(library.albums_folder, "neu", "b"), "1.mp3", album="Album laut Titel")
    assert admin.get("/api/albums/new").json == [
        {"folder": "kaputt", "title": "kaputt"},
        {"folder": "neu/a", "title": "a"},
        {"folder": "neu/b", "title": "Album laut Titel"},
    ]
    assert admin.post("/api/albums/new", {"titles": ["neu/a"]}).status == 400
    result = admin.post("/api/albums/new", {"titles": {"neu/a": "Der Titel von a"}}).json
    assert [(a["folder"], a["title"]) for a in result["added"]] == [
        ("neu/a", "Der Titel von a"),
        ("neu/b", "Album laut Titel"),
    ]
    assert [s["folder"] for s in result["skipped"]] == ["kaputt"]
    assert admin.get("/api/albums/new").json == [{"folder": "kaputt", "title": "kaputt"}]
    with open(library.targets_file) as f:
        assert sorted(t.get("title", "") for t in json.load(f).values()) == [
            "",
            "",
            "Album laut Titel",
            "Der Titel von a",
        ]


def test_delete_album(box, library):
    admin = box.admin()
    response = admin.delete("/api/albums/1")
    assert response.status == 409
    assert response.json["tags"] == [{"uid": TAG_ALBUM, "name": "album"}]
    assert admin.delete("/api/albums/2").status == 204
    assert "2" not in library.targets.targets
    assert os.path.isdir(os.path.join(library.albums_folder, "simple_album_b"))  # files are kept by default
    admin.delete(f"/api/tags/{quote(TAG_ALBUM)}/album")
    assert admin.delete("/api/albums/1?delete_files=1").status == 204
    assert not os.path.exists(os.path.join(library.albums_folder, "short"))
    assert os.path.isdir(library.albums_folder)
    assert admin.delete("/api/albums/1").status == 404


def test_delete_album_removes_its_positions(box, library):
    """Positions in a deleted album (stored for tags that were unlinked before) are removed with it."""
    admin = box.admin()
    library.activities.activities[TAG_ALBUM].update(2, 1.5, False)
    admin.delete(f"/api/tags/{quote(TAG_ALBUM)}/album")
    assert TAG_ALBUM in library.activities.activities  # unlinking keeps the position
    assert admin.delete("/api/albums/1").status == 204
    assert TAG_ALBUM not in library.activities.activities
    with open(library.activities_file, encoding="utf-8") as f:
        assert TAG_ALBUM not in json.load(f)


def test_delete_album_files_does_not_follow_symlinks(box, library, tmp_path):
    """An album folder that is a symlink to a folder outside the albums folder (e.g. created by hand) - deleting
    the album with its files must not delete the files outside."""
    admin = box.admin()
    outside = str(tmp_path / "outside")
    make_album_folder(outside, "a.mp3")
    os.symlink(outside, os.path.join(library.albums_folder, "link"))
    album = targetalbumtools.add_album_to_targets(library.targets, box.logger, "link", "playlist.m3u")
    assert admin.delete(f"/api/albums/{album.target_id}?delete_files=1").status == 204
    assert str(album.target_id) not in library.targets.targets
    assert sorted(os.listdir(outside)) == ["a.mp3", "playlist.m3u"]


def test_playing_album_cannot_be_deleted(box):
    admin = box.admin()
    box.place_tag(TAG_ALBUM)
    assert admin.delete("/api/tags/" + quote(TAG_ALBUM)).status == 204  # the album keeps playing
    response = admin.delete("/api/albums/1?delete_files=1")
    assert response.status == 409
    assert "playing" in response.json["error"]
    assert response.json["code"] == "album.playing"  # the page has a text of its own for it, in its language
    assert os.path.isdir(os.path.join(box.library.albums_folder, "short"))


def test_an_error_names_its_code_and_the_values_of_its_text(box):
    admin = box.admin()
    assert admin.post("/api/tags", {"uid": "04:AA:BB"}).status == 201
    response = admin.post("/api/tags", {"uid": "04:AA:BB"})
    assert response.status == 409
    assert response.json == {"error": "tag '04:AA:BB' exists already.", "code": "tag.exists", "uid": "04:AA:BB"}
    assert "code" not in admin.get("/api/nothing").json  # an error the page does not run into


# --- activities / maintenance ----------------------------------------------------------------------------------------


def test_activities_and_rebuild(box):
    admin = box.admin()
    box.library.activities.get_activity(TAG_ALBUM).update(2, 0.5, False)
    activities = [a for a in admin.get("/api/activities").json if a["tag_uid"] == TAG_ALBUM]
    assert activities == [{"tag_uid": TAG_ALBUM, "album_id": "1", "track_id": 2, "track_time": 0.5,
                           "finished": False}]  # fmt: skip
    assert admin.get("/api/tags/" + quote(TAG_ALBUM)).json["activity"] == {"track_id": 2, "track_time": 0.5,
                                                                            "finished": False}  # fmt: skip
    activities = admin.post("/api/activities/rebuild").json
    assert [(a["track_id"], a["track_time"]) for a in activities if a["tag_uid"] == TAG_ALBUM] == [(0, 0)]


def test_sanity_check(box):
    result = box.admin().post("/api/sanity-check").json
    assert result["passed"] is False  # TAG_UNKNOWN_ALBUM points to album 99
    assert any("99" in m for m in result["messages"])
    box.admin().delete("/api/tags/" + quote(TAG_UNKNOWN_ALBUM))
    assert box.admin().post("/api/sanity-check").json["passed"] is True


# --- upload ----------------------------------------------------------------------------------------------------------


def test_upload_album_with_track_numbers(box, library, tmp_path):
    admin = box.admin()
    upload = admin.post("/api/uploads", {"name": "Neues Album"})
    assert upload.status == 201
    upload_id = upload.json["id"]
    # file names in the wrong order - the id3 track numbers decide
    files = [mp3(tmp_path, "a.mp3", tracknumber="3/3", title="Drei"),
             mp3(tmp_path, "b.mp3", tracknumber="1/3", title="Eins"),
             mp3(tmp_path, "c.mp3", tracknumber="2/3", title="Zwei")]  # fmt: skip
    upload_files(admin, upload_id, files)
    state = admin.get("/api/uploads/" + upload_id).json
    assert state["playlist"] == ["b.mp3", "c.mp3", "a.mp3"]
    assert {f["filename"]: f["title"] for f in state["files"]} == {"a.mp3": "Drei", "b.mp3": "Eins", "c.mp3": "Zwei"}

    response = admin.post(f"/api/uploads/{upload_id}/finish")
    assert response.status == 201, response.content
    album = response.json["album"]
    assert album["folder"] == "Neues Album"
    assert album["title"] == "Neues Album"
    assert [t["title"] for t in album["tracks"]] == ["Eins", "Zwei", "Drei"]
    assert read_playlist(library, "Neues Album") == ["b.mp3", "c.mp3", "a.mp3"]
    with open(library.targets_file) as f:
        assert json.load(f)[album["id"]]["album-folder"] == "Neues Album"
    assert admin.get("/api/uploads/" + upload_id).status == 404
    assert os.listdir(os.path.join(library.albums_folder, STAGING_FOLDER)) == []


def test_upload_sorts_file_names_naturally(box, library, tmp_path):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Hörspiel"}).json["id"]
    upload_files(admin, upload_id, [mp3(tmp_path, name) for name in ["10 Ende.mp3", "2 Mitte.mp3", "1 Anfang.mp3"]])
    admin.post(f"/api/uploads/{upload_id}/finish")
    assert read_playlist(library, "Hörspiel") == ["1 Anfang.mp3", "2 Mitte.mp3", "10 Ende.mp3"]


def test_upload_with_custom_order_and_tag(box, library, tmp_path):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Lieder"}).json["id"]
    upload_files(admin, upload_id, [mp3(tmp_path, name) for name in ["a.mp3", "b.mp3"]])
    finish = f"/api/uploads/{upload_id}/finish"
    assert admin.post(finish, {"order": ["a.mp3"]}).status == 400
    assert admin.post(finish, {"order": ["a.mp3", "b.mp3", "x.mp3"]}).status == 400
    response = admin.post(finish, {"order": ["b.mp3", "a.mp3"], "tag_uid": TAG_UNMAPPED})
    assert response.status == 201
    assert read_playlist(library, "Lieder") == ["b.mp3", "a.mp3"]
    assert response.json["tag"]["album_id"] == response.json["album"]["id"]
    assert str(library.tags.get_tag(TAG_UNMAPPED).target_id) == response.json["album"]["id"]


def test_upload_for_the_tag_on_the_box_starts_playback(box, tmp_path):
    admin = box.admin()
    box.place_tag("7:7:7:7")
    assert box.stmoperation.get_active_state() == States.UNMAPPED_TAG
    upload_id = admin.post("/api/uploads", {"name": "Neu"}).json["id"]
    upload_files(admin, upload_id, [mp3(tmp_path, "1.mp3")])
    admin.post(f"/api/uploads/{upload_id}/finish", {"tag_uid": "current"})
    box.settle()
    assert admin.get("/api/status").json["album"]["folder"] == "Neu"


def test_upload_rejects_invalid_files(box, library):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Test"}).json["id"]
    path = f"/api/uploads/{upload_id}/files/"
    assert admin.put(path + "virus.exe", raw=b"MZ").status == 400
    assert admin.put(path + "fake.mp3", raw=b"this is no mp3" * 100).status == 400
    assert admin.put(path + "playlist.m3u", raw=b"x").status == 400
    with open(ONE_SECOND_MP3, "rb") as f:
        data = f.read()
    # path traversal - the file ends up in the staging folder with a harmless name
    response = admin.put(path + quote("../../evil.mp3"), raw=data)
    assert response.status == 201
    assert "/" not in response.json["filename"]
    assert not response.json["filename"].startswith(".")
    assert not os.path.exists(os.path.join(library.albums_folder, "evil.mp3"))
    assert not os.path.exists(os.path.join(str(library.folder), "evil.mp3"))
    staging = os.path.join(library.albums_folder, STAGING_FOLDER, upload_id)
    assert sorted(os.listdir(staging)) == [response.json["filename"]]  # no leftovers of the rejected files


def test_upload_size_limit(make_box):
    box = make_box(**{"max-upload-size": 0.001})  # 1 kB
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Groß"}).json["id"]
    with open(ONE_SECOND_MP3, "rb") as f:
        response = admin.put(f"/api/uploads/{upload_id}/files/a.mp3", raw=f.read())
    assert response.status == 413
    assert admin.get("/api/uploads/" + upload_id).json["files"] == []


def test_upload_names(box):
    admin = box.admin()
    assert admin.post("/api/uploads", {"name": "short"}).status == 409  # album folder exists
    assert admin.post("/api/uploads", {"name": "Neu"}).status == 201
    assert admin.post("/api/uploads", {"name": "Neu"}).status == 409  # upload in progress
    assert admin.post("/api/uploads", {"name": "  "}).status == 400
    assert admin.post("/api/uploads", {"name": "../.."}).status in (201, 400)
    upload = admin.post("/api/uploads", {"name": "a/b\\c"}).json
    folder = upload["folder"]
    assert "/" not in folder
    assert "\\" not in folder
    assert upload["title"] == "a/b\\c"  # the title keeps what the folder must not have
    assert admin.post("/api/uploads", {"name": "x" * 201}).status == 400
    assert safe_name("Die drei ??? – Folge 1") == "Die drei ___ – Folge 1"
    assert safe_name("..hidden") == "hidden"


def test_upload_abort_and_remove_file(box, library, tmp_path):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Weg"}).json["id"]
    upload_files(admin, upload_id, [mp3(tmp_path, "a.mp3"), mp3(tmp_path, "b.mp3")])
    assert admin.delete(f"/api/uploads/{upload_id}/files/a.mp3").status == 204
    assert [f["filename"] for f in admin.get("/api/uploads/" + upload_id).json["files"]] == ["b.mp3"]
    assert admin.delete(f"/api/uploads/{upload_id}/files/a.mp3").status == 404
    assert admin.delete("/api/uploads/" + upload_id).status == 204
    assert not os.path.exists(os.path.join(library.albums_folder, STAGING_FOLDER, upload_id))
    assert admin.post(f"/api/uploads/{upload_id}/finish").status == 404
    assert "Weg" not in [a["folder"] for a in admin.get("/api/albums").json]


def test_finish_without_files(box):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Leer"}).json["id"]
    assert admin.post(f"/api/uploads/{upload_id}/finish").status == 400


def test_stale_uploads_are_removed_on_start(box, library):
    admin = box.admin()
    upload_id = admin.post("/api/uploads", {"name": "Halb"}).json["id"]
    box.api.stop()
    box.api = box.create_api()
    box.api.start()
    assert not os.path.exists(os.path.join(library.albums_folder, STAGING_FOLDER, upload_id))


def test_list_and_expire_uploads(box, monkeypatch):
    admin = box.admin()
    first = admin.post("/api/uploads", {"name": "Alt"}).json["id"]
    assert [u["id"] for u in admin.get("/api/uploads").json] == [first]
    later = time.monotonic() + uploads.UPLOAD_EXPIRY + 1
    monkeypatch.setattr(uploads, "time", SimpleNamespace(monotonic=lambda: later))
    assert admin.get("/api/uploads").json == []
    assert admin.post("/api/uploads", {"name": "Alt"}).status == 201  # the name is free again


# --- virtual tags / new tags / logs / led ------ ---------------------------------------------------------------------


def test_virtual_tag_plays_album(box):
    user = box.user()
    assert user.post("/api/rfid", {"uid": TAG_ALBUM}).status == 204
    assert box.pubsub.messages(RFID_STATE)[-1] == RfidState(TAG_ALBUM)
    box.settle()
    assert user.get("/api/status").json["album"]["id"] == "1"
    assert user.get("/api/tags/current").json["uid"] == TAG_ALBUM
    assert user.delete("/api/rfid").status == 204
    box.settle()
    assert user.get("/api/status").json["state"] == "wait_for_tag"
    assert user.post("/api/rfid", {"uid": "a/b"}).status == 400
    assert user.post("/api/rfid", {}).status == 400


def test_virtual_unknown_tag_is_added(box):
    admin = box.admin()
    admin.post("/api/rfid", {"uid": "5:5:5:5"})
    box.settle()
    assert admin.get("/api/status").json["state"] == "unknown_tag"
    assert admin.get("/api/tags/" + quote("5:5:5:5")).json["album_id"] is None


def test_a_box_takes_no_tag_from_a_phone(box):
    """The phone is the reader of the library (docs/developer/library.md, section 5.1); a box has its own."""
    assert box.user().put("/api/tags/" + quote("04:A1:B2:C3:D4:E5:80")).status == 405
    assert box.admin().get("/api/tags/" + quote("04:A1:B2:C3:D4:E5:80")).status == 404


def test_create_tag(box, library):
    admin = box.admin()
    response = admin.post("/api/tags", {"uid": "8:8:8:8", "name": "rote Katze", "album_id": "2"})
    assert response.status == 201
    assert response.json["name"] == "rote Katze"
    assert response.json["album_id"] == "2"
    assert str(library.tags.get_tag("8:8:8:8").target_id) == "2"
    assert admin.post("/api/tags", {"uid": "8:8:8:8"}).status == 409
    assert admin.post("/api/tags", {"uid": "a/b"}).status == 400
    assert admin.post("/api/tags", {"uid": "current"}).status == 400
    assert admin.post("/api/tags", {"album_id": "99"}).status == 404
    generated = admin.post("/api/tags", {}).json
    assert len(generated["uid"].split(":")) == 4
    assert generated["album_id"] is None
    assert box.user().post("/api/tags", {}).status == 403
    # the new tag works like a real one
    admin.post("/api/rfid", {"uid": "8:8:8:8"})
    box.settle()
    assert admin.get("/api/status").json["album"]["id"] == "2"


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
JPEG = b"\xff\xd8\xff" + b"0" * 100


def test_a_tag_has_no_picture_of_its_own(box):
    admin = box.admin()
    assert "card" not in admin.get("/api/tags/" + quote(TAG_ALBUM)).json
    assert admin.get("/api/cards").status == 404
    assert admin.put(f"/api/tags/{quote(TAG_ALBUM)}/card", {"card": "x.png"}).status == 404


def test_logs(box, caplog):
    caplog.set_level(logging.DEBUG, logger="tests")
    admin = box.admin()
    admin.post("/api/rfid", {"uid": TAG_UNMAPPED})
    assert any("virtual tag" in e["message"] for e in admin.get("/api/logs").json["entries"])
    result = admin.get("/api/logs?level=WARNING").json
    assert all(e["level"] in ("WARNING", "ERROR", "CRITICAL") for e in result["entries"])
    last_id = admin.get("/api/logs").json["last_id"]
    assert admin.get(f"/api/logs?text=virtual&after={last_id}").json["entries"] == []
    found = admin.get("/api/logs?text=VIRTUAL%20TAG").json["entries"]
    assert found
    assert all("virtual tag" in e["message"] for e in found)
    limited = admin.get("/api/logs?limit=1").json
    assert len(limited["entries"]) == 1
    assert limited["entries"][0]["id"] == limited["last_id"]  # the newest one
    assert admin.get("/api/logs?after=x").status == 400
    assert box.user().get("/api/logs").status == 403
    box.settle()


def test_log_buffer_is_detached_after_stop(box, logger):
    buffer = box.api.log_buffer
    assert buffer in logger.handlers
    box.api.stop()
    assert buffer not in logger.handlers
    box.api = box.create_api()
    box.api.start()


def test_led_state(box, logger):
    led = RGBLed(logger, box.pubsub)
    box.control._rgbled = led
    admin = box.admin()
    led.set_color(Color.GREEN)
    assert admin.get("/api/status").json["led"] == {"color": "GREEN"}
    led.set_alternate_colors(Color.RED, Color.BLACK, 1)
    assert admin.get("/api/status").json["led"] == {"color_a": "RED", "color_b": "BLACK", "delay": 1}


def test_album_problems_and_rebuild_playlist(box, library):
    admin = box.admin()
    folder = os.path.join(library.albums_folder, "simple_album_b")
    assert admin.get("/api/albums/2").json["problems"] == []
    # a windows 'dir' listing instead of a playlist (cp850/cp1252 encoded)
    with open(os.path.join(folder, "playlist.m3u"), "wb") as f:
        f.write(" Datentr\x84ger in Laufwerk D: ist Data\r\n\r\n".encode("latin-1"))
    library.targets.get_target("2").get_tracks(force_update=True)
    problems = admin.get("/api/albums/2").json["problems"]
    assert problems
    assert any("not found" in p for p in problems)
    mp3(folder, "10 zehn.mp3")
    mp3(folder, "9 neun.mp3")
    assert box.user().post("/api/albums/2/playlist").status == 403
    album = admin.post("/api/albums/2/playlist").json
    assert album["problems"] == []
    names = [t["filename"] for t in album["tracks"]]
    assert names.index("9 neun.mp3") + 1 == names.index("10 zehn.mp3")
    assert os.path.exists(os.path.join(folder, "playlist.m3u.bak"))
    assert read_playlist(library, "simple_album_b") == names


def test_album_without_tracks_is_rejected(box, library):
    folder = os.path.join(library.albums_folder, "leer")
    os.makedirs(folder)
    mp3(folder, "a.mp3")
    with open(os.path.join(folder, "playlist.m3u"), "w") as f:
        f.write("#EXTM3U\n")
    assert box.admin().post("/api/albums", {"folder": "leer"}).status == 400


# --- server-sent events ---------------------------------------------------------------------------------------------


class EventStream:
    """Reads the server-sent events of ``/api/events``."""

    def __init__(self, client: ApiClient, timeout: float = 5) -> None:
        self.connection = http.client.HTTPSConnection("localhost", client.port, context=client.context, timeout=timeout)
        self.connection.request("GET", "/api/events", headers={"Authorization": f"Bearer {client.token}"})
        self.response = self.connection.getresponse()

    def next(self) -> tuple[str, Any]:
        """The next event - raises EOFError at the end of the stream and TimeoutError if nothing arrives."""
        event, data = None, None
        while True:
            line = self.response.readline().decode()
            if not line:
                raise EOFError
            line = line.rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and event is not None:
                return event, data

    def drain(self) -> None:
        """Read until the stream ends (EOFError) - other errors are raised."""
        with contextlib.suppress(EOFError):
            while True:
                self.next()

    def next_status(self, predicate) -> dict[str, Any]:
        while True:
            event, data = self.next()
            if event == "status" and predicate(data):
                status: dict[str, Any] = data
                return status

    def close(self) -> None:
        self.connection.close()


@pytest.fixture
def events():
    streams: list[EventStream] = []

    def open_stream(client: ApiClient, timeout: float = 5) -> EventStream:
        streams.append(EventStream(client, timeout))
        return streams[-1]

    yield open_stream
    for stream in streams:
        stream.close()


def test_events_push_status_changes(box, events):
    stream = events(box.admin())
    assert stream.response.status == 200
    assert stream.response.getheader("Content-Type") == "text/event-stream"
    assert stream.next_status(lambda s: True)["tag"] is None  # the current status right away
    started = time.monotonic()
    box.pubsub.deliver(RFID_STATE, RfidState(TAG_ALBUM))
    status = stream.next_status(lambda s: s["tag"] is not None)
    assert status["tag"]["uid"] == TAG_ALBUM
    assert time.monotonic() - started < 2
    box.settle()


def test_events_need_a_login(box):
    assert box.client.get("/api/events").status == 401


def test_position_alone_is_no_change(box, events, clock, monkeypatch):
    monkeypatch.setattr(server, "STATUS_REFRESH", 3600)
    box.place_tag(TAG_ALBUM)
    stream = events(box.user(), timeout=0.6)  # no log events
    stream.next_status(lambda s: s["album"] is not None)
    clock.advance(0.1)  # the playback position changes (the test tracks are shorter than 0.5 s)
    with pytest.raises(TimeoutError):
        stream.next()
    box.settle()


def test_status_is_refreshed_periodically(box, events, monkeypatch):
    monkeypatch.setattr(server, "STATUS_REFRESH", 0.3)
    stream = events(box.user())  # no log events
    for _ in range(3):
        assert stream.next()[0] == "status"


def test_log_events_only_for_admins(box, events, logger, monkeypatch):
    monkeypatch.setattr(server, "STATUS_REFRESH", 3600)
    admin_stream = events(box.admin())
    user_stream = events(box.user(), timeout=0.6)
    assert admin_stream.next()[0] == user_stream.next()[0] == "status"
    logger.warning("something happened")
    event, data = admin_stream.next()
    assert event == "log"
    assert data["last_id"] == box.api.log_buffer.last_id
    with pytest.raises(TimeoutError):
        user_stream.next()


def test_stream_ends_with_the_session(box, events):
    admin = box.admin()
    stream = events(admin)
    stream.next()
    assert admin.post("/api/logout").status == 204
    stream.drain()  # ends instead of timing out


def test_stop_ends_the_streams(box, events):
    stream = events(box.admin())
    stream.next()
    started = time.monotonic()
    box.api.stop()
    assert time.monotonic() - started < 2
    with contextlib.suppress(ConnectionError):
        stream.drain()  # ends instead of timing out
    box.api = box.create_api()
    box.api.start()


def test_without_position():
    status: dict[str, Any] = {
        "state": "play_album",
        "album": {"id": "1", "playback": {"state": "play", "position": 1.5}},
        "tag": {"uid": "1", "activity": {"track_id": 2, "track_time": 1.5}},
    }
    assert server._without_position(status) == {
        "state": "play_album",
        "album": {"id": "1", "playback": {"state": "play"}},
        "tag": {"uid": "1", "activity": {"track_id": 2}},
    }
    assert status["album"]["playback"]["position"] == 1.5  # unchanged
    assert server._without_position({"album": None, "tag": None}) == {"album": None, "tag": None}


# --- radio stations --------------------------------------------------------------------------------------------------

FM4 = {"name": "FM4", "url": "https://example.org/fm4.m3u"}


def test_radio_stations_crud(box, library):
    admin, user = box.admin(), box.user()
    assert admin.get("/api/radios").json == []
    assert user.post("/api/radios", FM4).status == 403
    created = admin.post("/api/radios", FM4)
    assert created.status == 201
    radio_id = created.json["id"]
    assert int(radio_id) > 2  # albums and radio stations share the ids
    assert created.json | {"created": None} == {"id": radio_id, **FM4, "cover": None, "created": None, "tags": []}
    assert user.get("/api/radios").json == [created.json]
    assert user.get(f"/api/radios/{radio_id}").json == created.json
    assert admin.get(f"/api/albums/{radio_id}").status == 404
    assert admin.get("/api/radios/1").status == 404  # an album

    changed = admin.patch(f"/api/radios/{radio_id}", {"name": " Radio FM4 "})
    assert changed.json["name"] == "Radio FM4"
    assert changed.json["url"] == FM4["url"]
    changed = admin.patch(f"/api/radios/{radio_id}", {"url": "http://example.org/stream"})
    assert (changed.json["name"], changed.json["url"]) == ("Radio FM4", "http://example.org/stream")
    assert user.patch(f"/api/radios/{radio_id}", {"name": "x"}).status == 403
    with open(library.targets_file) as f:
        stored = json.load(f)[radio_id]
    assert stored | {"created": None} == {"target-id": radio_id, "target-type": "radio", "name": "Radio FM4",
                                          "url": "http://example.org/stream", "created": None}  # fmt: skip

    assert admin.delete(f"/api/radios/{radio_id}").status == 204
    assert admin.get("/api/radios").json == []
    assert admin.delete(f"/api/radios/{radio_id}").status == 404


def test_radio_station_cover(box, library):
    admin = box.admin()
    radio_id = admin.post("/api/radios", FM4).json["id"]
    path = f"/api/radios/{radio_id}/cover"
    assert admin.get(path).status == 404
    changed = admin.request("PUT", path, raw=JPEG)
    assert changed.status == 200
    first = changed.json["cover"]
    assert first.startswith("station-")
    assert first.endswith(".jpg")
    cover = box.user().get(path)
    assert cover.content == JPEG
    assert cover.getheader("Content-Type") == "image/jpeg"
    assert admin.get("/api/radios").json[0]["cover"] == first
    with open(library.targets_file) as f:
        assert json.load(f)[radio_id]["cover"] == first
    # it is kept in the folder of the images
    images_folder = os.path.join(os.path.dirname(library.tags_file), "images")

    def images():
        return os.listdir(images_folder)

    assert images() == [first]
    # the station that plays shows it
    admin.put(f"/api/tags/{quote(TAG_UNMAPPED)}/radio", {"radio_id": radio_id})
    box.place_tag(TAG_UNMAPPED)
    assert admin.get("/api/status").json["radio"]["cover"] == first
    box.place_tag(None)
    admin.delete(f"/api/tags/{quote(TAG_UNMAPPED)}/radio")
    # another image replaces it
    second = admin.request("PUT", path, raw=PNG).json["cover"]
    assert admin.get(path).content == PNG
    assert images() == [second]
    assert admin.request("PUT", path, raw=b"GIF89a" + b"0" * 100).status == 400
    assert admin.get(path).content == PNG
    assert box.user().request("PUT", path, raw=JPEG).status == 403
    assert admin.request("PUT", "/api/radios/1/cover", raw=JPEG).status == 404  # an album
    # the cover goes with the station
    assert admin.delete(f"/api/radios/{radio_id}").status == 204
    assert images() == []


@pytest.mark.parametrize(
    "body",
    [
        {"url": FM4["url"]},
        {"name": " ", "url": FM4["url"]},
        {"name": "x" * 101, "url": FM4["url"]},
        {"name": "FM4"},
        {"name": "FM4", "url": "ftp://example.org/fm4"},
        {"name": "FM4", "url": "file:///etc/passwd"},
        {"name": "FM4", "url": "https://"},
        {"name": "FM4", "url": "https://example.org/a b"},
        {"name": "FM4", "url": 42},
    ],
)
def test_invalid_radio_stations_are_rejected(box, body):
    admin = box.admin()
    response = admin.post("/api/radios", body)
    assert response.status == 400, response.json
    assert admin.get("/api/radios").json == []


def test_invalid_radio_change_changes_nothing(box):
    admin = box.admin()
    radio_id = admin.post("/api/radios", FM4).json["id"]
    assert admin.patch(f"/api/radios/{radio_id}", {"name": "neu", "url": "nope"}).status == 400
    assert admin.get(f"/api/radios/{radio_id}").json["name"] == "FM4"


def test_tag_linked_to_radio_station_plays_it(box, rgbled):
    admin = box.admin()
    radio_id = admin.post("/api/radios", FM4).json["id"]
    path = f"/api/tags/{quote(TAG_UNMAPPED)}/radio"
    tag = admin.put(path, {"radio_id": radio_id}).json
    assert (tag["radio_id"], tag["radio_exists"], tag["album_id"]) == (radio_id, True, None)
    assert admin.put(path, {"radio_id": "1"}).status == 404  # an album
    assert admin.get(f"/api/radios/{radio_id}").json["tags"] == [{"uid": TAG_UNMAPPED, "name": "unmapped"}]
    assert TAG_UNMAPPED not in [t["uid"] for t in admin.get("/api/tags?unmapped=1").json]

    box.place_tag(TAG_UNMAPPED)
    assert box.stmoperation.get_active_state() == States.PLAY_ALBUM
    status = admin.get("/api/status").json
    assert status["album"] is None
    assert status["radio"] == {"id": radio_id, "name": "FM4", "url": FM4["url"], "cover": None, "state": "playing",
                               "error": None, "test": False}  # fmt: skip
    assert rgbled.last() == ("color", Color.BLUE)
    assert FakeRadioPlayer.players[-1].url == FM4["url"]
    assert TAG_UNMAPPED not in box.library.activities.activities  # no position for radio stations
    # next/prev do nothing, the station keeps playing
    admin.post("/api/buttons/next", {})
    box.settle()
    assert admin.get("/api/status").json["radio"]["state"] == "playing"

    # a linked or playing station cannot be deleted
    assert admin.delete(f"/api/radios/{radio_id}").status == 409
    # a new url restarts the station
    admin.patch(f"/api/radios/{radio_id}", {"url": "https://example.org/other"})
    box.settle()
    assert FakeRadioPlayer.players[-1].url == "https://example.org/other"
    assert admin.get("/api/status").json["radio"]["url"] == "https://example.org/other"

    box.place_tag(None)
    assert not admin.get("/api/status").json["radio"]
    assert admin.delete(path).json["radio_id"] is None
    assert admin.delete(f"/api/radios/{radio_id}").status == 204


def test_create_tag_linked_to_radio_station(box):
    admin = box.admin()
    radio_id = admin.post("/api/radios", FM4).json["id"]
    tag = admin.post("/api/tags", {"uid": "9:9:9:9", "radio_id": radio_id}).json
    assert tag["radio_id"] == radio_id
    assert admin.post("/api/tags", {"uid": "9:9:9:8", "radio_id": radio_id, "album_id": "1"}).status == 400
    assert admin.post("/api/tags", {"uid": "9:9:9:7", "radio_id": "1"}).status == 404


def test_tag_linked_to_deleted_radio_station(box, library):
    admin = box.admin()
    radio_id = admin.post("/api/radios", FM4).json["id"]
    library.tags.assign_target(TAG_UNMAPPED, TargetType.radio, radio_id)
    library.targets.delete_target(radio_id)
    tag = admin.get(f"/api/tags/{quote(TAG_UNMAPPED)}").json
    assert (tag["radio_id"], tag["radio_exists"]) == (radio_id, False)
    box.place_tag(TAG_UNMAPPED)
    assert box.stmoperation.get_active_state() == States.UNKNOWN_ALBUM
    assert not admin.post("/api/sanity-check").json["passed"]


def test_radio_test(box, rgbled):
    admin, user = box.admin(), box.user()
    assert user.post("/api/radio-test", {"url": FM4["url"]}).status == 403
    assert admin.post("/api/radio-test", {"url": "nope"}).status == 400
    assert admin.delete("/api/radio-test").status == 409  # no test running
    response = admin.post("/api/radio-test", {"url": FM4["url"], "name": "FM4"})  # not saved as station
    assert response.status == 202
    box.settle()
    assert box.stmoperation.get_active_state() == States.TEST_RADIO
    radio = admin.get("/api/status").json["radio"]
    assert radio == {"id": "test", "name": "FM4", "url": FM4["url"], "cover": None, "state": "playing", "error": None,
                     "test": True}  # fmt: skip
    assert rgbled.last() == ("color", Color.BLUE)
    # another url replaces the test
    admin.post("/api/radio-test", {"url": "http://example.org/other"})
    box.settle()
    assert admin.get("/api/status").json["radio"]["name"] == "http://example.org/other"
    assert admin.delete("/api/radio-test").status == 204
    box.settle()
    assert box.stmoperation.get_active_state() == States.WAIT_FOR_TAG
    assert admin.get("/api/status").json["radio"] is None
    assert FakeRadioPlayer.players[-1].state.value == "stopped"


def test_radio_test_never_bypasses_a_tag(box):
    admin = box.admin()
    box.place_tag(TAG_ALBUM)
    response = admin.post("/api/radio-test", {"url": FM4["url"]})
    assert response.status == 409
    assert "tag is on the box" in response.json["error"]
    box.place_tag(None)
    admin.post("/api/radio-test", {"url": FM4["url"]})
    box.settle()
    assert box.stmoperation.get_active_state() == States.TEST_RADIO
    # a tag placed during the test ends it - the tag plays
    box.place_tag(TAG_ALBUM)
    assert box.stmoperation.get_active_state() == States.PLAY_ALBUM
    assert admin.get("/api/status").json["album"]["id"] == "1"
    assert FakeRadioPlayer.players[-1].state.value == "stopped"


# --- pause ------------------------------------------------------------------------------------------------------------


def test_pause_and_play(box, clock):
    user = box.user()
    assert user.post("/api/player/pause").status == 409  # nothing plays
    assert user.post("/api/player/play").status == 409
    box.place_tag(TAG_ALBUM)
    clock.advance(0.2)
    assert user.post("/api/player/pause").status == 204
    box.settle()
    playback = user.get("/api/status").json["album"]["playback"]
    assert playback["state"] == "paused"
    assert playback["position"] == pytest.approx(0.2)
    assert user.post("/api/buttons/pause").status == 204  # paused already
    assert user.post("/api/player/play").status == 204
    box.settle()
    assert user.get("/api/status").json["album"]["playback"]["state"] == "play"
    # the buttons of a headset arrive like the buttons of the box
    assert user.post("/api/buttons/pause").status == 204
    box.settle()
    assert box.album_info()["state"] == "paused"
    assert user.post("/api/buttons/next").status == 204  # continues - the next press would skip
    box.settle()
    assert box.album_info()["state"] == "play"
    assert box.album_info()["track"]["track_id"] == 0


def test_pause_of_a_radio_station(box):
    admin = box.admin()
    radio = admin.post("/api/radios", {"name": "FM4", "url": "https://example.org/fm4"}).json
    assert admin.put(f"/api/tags/{quote(TAG_UNMAPPED)}/radio", {"radio_id": radio["id"]}).status == 200
    box.place_tag(TAG_UNMAPPED)
    assert admin.get("/api/status").json["radio"]["state"] == "playing"
    assert admin.post("/api/player/pause").status == 204
    box.settle()
    assert admin.get("/api/status").json["radio"]["state"] == "paused"
    assert admin.post("/api/player/next").status == 204  # live again
    box.settle()
    assert admin.get("/api/status").json["radio"]["state"] == "playing"


# --- volume limits ----------------------------------------------------------------------------------------------------


def test_volume_limits(box):
    admin, user = box.admin(), box.user()
    path = "/api/volume/limits/speaker"
    assert user.put(path, {"default_volume": 10, "max_volume": 100}).status == 403  # hearing protection: admins only
    assert user.get("/api/volume").json["profiles"]["speaker"] == {"volume": 40, "default_volume": 40,
                                                                    "max_volume": 80}  # fmt: skip
    admin.put("/api/volume", {"volume": 70})
    volume = admin.put(path, {"default_volume": 20, "max_volume": 50}).json
    assert (volume["volume"], volume["max_volume"], volume["default_volume"]) == (50, 50, 20)  # lowered at once
    assert box.volume_control.volumes[-1] == 50
    assert admin.put("/api/volume", {"volume": 90}).json["volume"] == 50
    volume = admin.put("/api/volume/limits/headphones", {"default_volume": 30, "max_volume": 60}).json
    assert volume["profiles"]["headphones"] == {"volume": 40, "default_volume": 30, "max_volume": 60}
    assert admin.get("/api/bluetooth").json["max_volume"] == 60
    with open(box.volume_file, encoding="utf-8") as f:
        assert json.load(f) == {"speaker": {"default-volume": 20, "max-volume": 50},
                                "headphones": {"default-volume": 30, "max-volume": 60}}  # fmt: skip
    for invalid in [{"default_volume": 60, "max_volume": 50}, {"default_volume": 10}, {"max_volume": 101},
                    {"default_volume": "10", "max_volume": 50}, {}]:  # fmt: skip
        assert admin.put(path, invalid).status == 400
    assert admin.put("/api/volume/limits/subwoofer", {"default_volume": 10, "max_volume": 50}).status == 404
    assert admin.get("/api/volume").json["profiles"]["speaker"]["max_volume"] == 50


# --- bluetooth headset (the virtual one of a box without hardware) ----------------------------------------------------


def bluetooth(client):
    return client.get("/api/bluetooth").json


def pair(box, admin):
    assert admin.post("/api/bluetooth/pair").status == 202
    assert wait_for(lambda: bluetooth(admin)["connected"])
    box.settle()


def test_bluetooth_pairing_and_roles(box):
    admin, user = box.admin(), box.user()
    assert bluetooth(user)["device"] is None
    assert user.post("/api/bluetooth/pair").status == 403
    assert user.delete("/api/bluetooth/device").status == 403
    assert admin.delete("/api/bluetooth/device").status == 409  # nothing paired
    response = admin.post("/api/bluetooth/pair")
    assert response.status == 202
    assert response.json["pairing"]
    assert admin.post("/api/bluetooth/pair").status == 409  # searching already
    assert wait_for(lambda: bluetooth(user)["connected"])
    info = bluetooth(user)
    assert info["device"] == {"address": "00:00:00:00:00:01", "name": "Virtual headset"}
    assert (info["virtual"], info["battery"], info["pairing"], info["pairing_result"]) == (True, 80, False, "paired")
    status = user.get("/api/status").json
    assert status["bluetooth"] == info
    assert status["volume"]["output"] == "headphones"
    assert status["volume"]["volume"] == 40
    assert admin.delete("/api/bluetooth/device").status == 204
    assert bluetooth(user)["device"] is None
    assert user.get("/api/status").json["volume"]["output"] == "speaker"


def test_headset_volume_is_limited(box):
    admin = box.admin()
    pair(box, admin)
    assert admin.put("/api/volume", {"volume": 90}).json["volume"] == 75  # the maximum of the headset
    assert bluetooth(admin)["volume"] == 75
    assert box.volumemanager.get_volume() == 40  # the speaker keeps its volume
    admin.put("/api/volume/limits/headphones", {"default_volume": 30, "max_volume": 50})
    assert bluetooth(admin)["volume"] == 50  # lowered at once
    for _ in range(3):  # volume buttons of the headset: followed, but not above the maximum
        assert admin.post("/api/bluetooth/virtual", {"button": "volume-down"}).status == 200
    assert bluetooth(admin)["volume"] == 32
    for _ in range(5):
        admin.post("/api/bluetooth/virtual", {"button": "volume-up"})
    assert bluetooth(admin)["volume"] == 50
    assert admin.get("/api/volume").json["volume"] == 50
    admin.post("/api/buttons/volume-down")  # buttons of the box change the headset now
    box.settle()
    assert bluetooth(admin)["volume"] == 45
    assert box.volumemanager.get_volume() == 40


def test_lost_headset_pauses_the_album(box, clock):
    user, admin = box.user(), box.admin()
    box.place_tag(TAG_ALBUM)
    pair(box, admin)
    assert box.album_info()["state"] == "play"
    assert box.output.headset
    assert user.post("/api/bluetooth/virtual", {"connected": False}).json["connected"] is False
    box.settle()
    status = user.get("/api/status").json
    assert status["album"]["playback"]["state"] == "paused"
    assert status["volume"]["output"] == "speaker"
    assert not box.output.headset
    assert user.post("/api/bluetooth/virtual", {"connected": True}).json["connected"] is True  # back in time
    box.settle()
    status = user.get("/api/status").json
    assert status["album"]["playback"]["state"] == "play"
    assert status["volume"]["output"] == "headphones"
    assert status["volume"]["volume"] == 40  # the start value again
    # buttons of the headset
    assert user.post("/api/bluetooth/virtual", {"button": "pause"}).status == 200
    box.settle()
    assert box.album_info()["state"] == "paused"
    assert user.post("/api/bluetooth/virtual", {"button": "play"}).status == 200
    assert user.post("/api/bluetooth/virtual", {"button": "next"}).status == 200
    box.settle()
    assert box.album_info()["state"] == "play"
    assert box.album_info()["track"]["track_id"] == 1
    # unpairing counts as a lost headset
    assert admin.delete("/api/bluetooth/device").status == 204
    box.settle()
    assert box.album_info()["state"] == "paused"
    assert user.post("/api/buttons/next").status == 204  # goes on with the speaker
    box.settle()
    assert box.album_info()["state"] == "play"


def test_virtual_headset_errors(box):
    user = box.user()
    assert user.post("/api/bluetooth/virtual", {"connected": True}).status == 409  # not paired
    assert user.post("/api/bluetooth/virtual", {"button": "play"}).status == 409  # not connected
    assert user.post("/api/bluetooth/virtual", {"connected": "yes"}).status == 400
    pair(box, box.admin())
    assert user.post("/api/bluetooth/virtual", {"button": "eject"}).status == 400
    assert user.post("/api/bluetooth/virtual", {}).status == 200


def test_bluetooth_without_driver(box):
    """A box with hardware, but without bluetooth driver (or before it reports)."""
    box.control._virtual_headset = None
    box.headset._state = None
    admin = box.admin()
    assert bluetooth(admin)["available"] is False
    assert admin.post("/api/bluetooth/pair").status == 409
    assert admin.delete("/api/bluetooth/device").status == 409
    assert admin.post("/api/bluetooth/virtual", {"connected": True}).status == 404
    box.control._headset = None
    assert bluetooth(admin) == {"available": False, "virtual": False, "device": None, "connected": False,
                                "battery": None, "volume": None, "pairing": False, "pairing_result": None,
                                "default_volume": 40, "max_volume": 75}  # fmt: skip
    assert admin.post("/api/bluetooth/pair").status == 409


def test_search_led_overlay(make_box, logger, library, tmp_path, clock, certificate):
    """The search shows on the led of the web page - afterwards the pattern of the state machines is back."""
    box = make_box()
    led = RGBLed(logger, box.pubsub)
    box.control._rgbled = box.headset._rgbled = led
    led.set_color(Color.GREEN)
    admin = box.admin()
    assert admin.post("/api/bluetooth/pair").status == 202
    assert admin.get("/api/status").json["led"] == {"color_a": "BLUE", "color_b": "BLACK", "delay": 0.2}
    assert wait_for(lambda: admin.get("/api/status").json["led"] == {"color": "WHITE"})
    led.clear_overlay()
    assert admin.get("/api/status").json["led"] == {"color": "GREEN"}


@pytest.mark.parametrize("action", ["hold", "release"])
def test_hold_and_release_of_buttons(box, action):
    topic = {"hold": BUTTON_HELD, "release": BUTTON_RELEASED}[action]
    assert box.user().post("/api/buttons/next", {"action": action}).status == 204  # like the buttons of the box
    assert box.pubsub.messages(topic) == [Button.NEXT]


def test_next_and_prev_held_start_the_search(box):
    admin = box.admin()
    admin.post("/api/buttons/next", {"action": "hold"})
    admin.post("/api/buttons/prev", {"action": "hold"})
    assert wait_for(lambda: bluetooth(admin)["connected"])


# --- backup ----------------------------------------------------------------------------------------------------------


def backup_of(response):
    """(what the backup says about itself, its members by name)"""
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        found = {member.name: (archive.extractfile(member) or io.BytesIO()).read() for member in archive}
    return verify(io.BytesIO(response.content)), found


def test_backup_of_a_box_without_and_with_the_albums(box, library):
    admin = box.admin()
    assert admin.put("/api/volume/limits/headphones", {"default_volume": 30, "max_volume": 60}).status == 200
    radio_id = admin.post("/api/radios", FM4).json["id"]
    cover = admin.request("PUT", f"/api/radios/{radio_id}/cover", raw=JPEG).json["cover"]

    plain = admin.get("/api/backup")
    assert plain.status == 200
    assert plain.headers["Content-Type"] == "application/gzip"
    about, found = backup_of(plain)
    assert re.fullmatch(r"tinyjukebox-box-\d{8}-\d{6}\.tar\.gz", plain.headers.get_filename())
    assert (about["kind"], about["albums"], about["skipped"]) == ("box", False, [])
    with open(library.tags_file, "rb") as infile:
        assert found["json/tags.json"] == infile.read()
    assert {"json/targets.json", "json/volume.json", f"images/{cover}"} <= set(found)
    assert not [name for name in found if name.startswith(("albums/", "ssl/", "identity/", "sync/"))]
    assert not [name for name, content in found.items() if b"password" in content]  # e.g. the users of the page

    full = admin.get("/api/backup?albums=1")
    assert full.headers["Content-Type"] == "application/x-tar"
    about, found = backup_of(full)
    assert about["albums"] is True
    with open(os.path.join(library.albums_folder, "short", "1.mp3"), "rb") as infile:
        assert found["albums/short/1.mp3"] == infile.read()
    assert {"albums/short/playlist.m3u", "json/tags.json"} <= set(found)


def test_backup_is_for_admins(box):
    assert box.user().get("/api/backup").status == 403
    assert box.client.get("/api/backup").status == 401
    assert box.admin().post("/api/backup").status == 405
