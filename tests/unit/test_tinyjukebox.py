"""The complete TinyJukebox service with the in-process queue pubsub."""

import functools
import json
import socket
import threading

import pytest

from tests.support import ONE_SECOND_MP3, TAG_ALBUM, ApiClient, box_config, create_library, wait_for
from tinyjukebox.albumplayer.dummyplayer import DummyPlayer
from tinyjukebox.albumplayer.simpleplayer import SimplePlayer
from tinyjukebox.core.config import validate_config
from tinyjukebox.core.pubsub.localqueue import LocalQueue
from tinyjukebox.messages import BUTTON_PRESSED, LED, RFID_POLL, RFID_STATE, Button, Color, RfidState
from tinyjukebox.stm.stmoperation import States
from tinyjukebox.stm.stmplayalbum import STMPlayAlbum
from tinyjukebox.tinyjukebox import TinyJukebox
from tinyjukebox.volumemanager import VirtualVolumeControl
from tinyjukebox.web.auth import UsersFile, hash_password

POLL_INTERVAL = 0.05
ALBUMPLAYER = {"pause-between-tracks": 0.2, "rewind-max-time": 5, "update-schedule": 0.2}


@pytest.fixture
def library(tmp_path, logger):
    """Album 1: three ~1 s tracks - the positions below are taken within the first track."""
    return create_library(tmp_path, logger, track=ONE_SECOND_MP3)


@pytest.fixture
def config(library):
    return box_config(library, albumplayer=dict(ALBUMPLAYER))


@pytest.fixture
def volumes(monkeypatch):
    """Volumes set on the (virtual) mixer."""
    volumes = []
    original = VirtualVolumeControl.set_volume

    def set_volume(self, volume):
        volumes.append(volume)
        original(self, volume)

    monkeypatch.setattr(VirtualVolumeControl, "set_volume", set_volume)
    return volumes


class Service:
    """Started TinyJukebox instance plus helpers to send messages and to record published messages."""

    def __init__(self, config, logger, player_factory):
        self.tinyjukebox = TinyJukebox(config, logger=logger, no_gui=True, hardware=False,
                                       player_factory=player_factory, poll_interval=POLL_INTERVAL)  # fmt: skip
        pubsub = self.tinyjukebox._pubsub_client
        assert isinstance(pubsub, LocalQueue)
        self.pubsub = pubsub
        self.received = {}
        for topic in [LED, RFID_POLL]:
            self.pubsub.subscribe(topic, functools.partial(self._record, topic))
        self.tinyjukebox.start()

    def _record(self, topic, message):
        self.received.setdefault(topic, []).append(message)

    def settle(self):
        """Wait until all messages have been delivered and all state machines are idle."""
        for _ in range(3):  # a state machine may publish again (e.g. led) - deliver that as well
            self.pubsub.wait_until_delivered()
            self.tinyjukebox._stmvolume.wait_until_idle()
            self.tinyjukebox.stmoperation.wait_until_idle()
            album = self.tinyjukebox.stmoperation.get_album()
            if album is not None:
                assert isinstance(album, STMPlayAlbum)
                album.wait_until_idle()

    def send(self, topic, message):
        self.pubsub.publish(topic, message)
        self.settle()

    def rfid(self, uid, present=True):
        self.send(RFID_STATE, RfidState(uid if present else None))

    def state(self):
        return self.tinyjukebox.stmoperation.get_active_state()

    def album_info(self):
        album = self.tinyjukebox.stmoperation.get_album()
        return album.get_info() if album is not None else None

    def leds(self):
        """Names of the static colors (``None`` for alternating colors)."""
        return [Color(m.colors[0]).name if len(m.colors) == 1 else None for m in self.received.get(LED, [])]


@pytest.fixture
def service(config, logger, volumes, clock):
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    yield s
    s.tinyjukebox.stop()


def read_json(filename):
    with open(filename, encoding="utf-8") as f:
        return json.load(f)


def test_start_and_stop(config, logger, volumes, clock):
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    s.settle()
    assert s.state() == States.WAIT_FOR_TAG
    assert "GREEN" in s.leds()
    assert wait_for(lambda: len(s.received.get(RFID_POLL, [])) >= 2)  # initial request + polling
    assert volumes[-1] == 50
    s.tinyjukebox.stop()
    assert s.state() == States.END
    assert volumes[-1] == 0


def test_play_pause_resume_and_finish(service, library, volumes, clock):
    service.rfid(TAG_ALBUM)
    assert service.state() == States.PLAY_ALBUM
    assert service.album_info()["state"] == "play"
    assert "BLUE" in service.leds()

    service.send(BUTTON_PRESSED, Button.NEXT)
    assert service.album_info()["track"]["track_id"] == 1

    service.send(BUTTON_PRESSED, Button.VOLUME_UP)
    assert volumes[-1] == 55

    clock.advance(0.4)
    assert service.album_info()["track"]["pos"] > 0.3
    service.rfid(TAG_ALBUM, present=False)
    assert service.state() == States.WAIT_FOR_TAG
    saved = read_json(library.activities_file)[TAG_ALBUM]
    assert saved["track-id"] == 1
    assert saved["track-time"] > 0.3

    service.rfid(TAG_ALBUM)
    info = service.album_info()
    assert info["state"] == "play"
    assert info["track"]["track_id"] == 1
    assert info["track"]["pos"] >= saved["track-time"] - 0.05

    clock.advance(info["track"]["length"])  # end of track 1 -> pause between tracks (real time) -> track 2
    assert wait_for(lambda: service.album_info()["track"]["track_id"] == 2 and clock.pending() > 0)
    clock.advance(info["track"]["length"])
    service.settle()
    assert service.album_info()["state"] == "finished"
    assert read_json(library.activities_file)[TAG_ALBUM]["album-finished"] is True


def test_new_tag_is_stored_as_unmapped(service, library):
    service.rfid("7:7:7:7")
    assert service.state() == States.UNMAPPED_TAG
    assert "YELLOW" in service.leds()
    assert read_json(library.tags_file)["7:7:7:7"]["target"]["id"] == -1


def test_no_threads_left_after_stop(config, logger, volumes):
    """With the real player (ffmpeg, silent output) - all its threads end with the service."""
    before = {t for t in threading.enumerate() if not t.daemon}
    s = Service(config, logger, SimplePlayer)
    s.rfid(TAG_ALBUM)
    assert s.album_info()["state"] == "play"
    s.tinyjukebox.stop()
    assert wait_for(lambda: {t for t in threading.enumerate() if not t.daemon} <= before)


def test_a_step_that_fails_while_stopping_stops_the_rest_all_the_same(config, logger, volumes, clock):
    """The led turns red, the volume goes to 0, the service counts as stopped - the error comes afterwards."""
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    s.settle()
    machine_stop = s.tinyjukebox._stmoperation.stop

    def failing() -> None:
        machine_stop()
        raise OSError(30, "Read-only file system")

    s.tinyjukebox._stmoperation.stop = failing  # type: ignore[method-assign]
    with pytest.raises(OSError, match="Read-only"):
        s.tinyjukebox.stop()
    assert volumes[-1] == 0
    assert s.tinyjukebox._rgb_led.get_state() == {"color": "RED"}
    assert s.tinyjukebox._is_stopped.is_set()


def test_rest_api(config, logger, volumes, clock, tmp_path):
    """The service with rest api: the buttons of the web page reach the box via the (queue) pubsub like the drivers."""
    ssl_folder = tmp_path / "ssl"
    config["tinyjukebox"]["rest-api"] = {"host": "127.0.0.1", "port": 0,
                                         "certificate": str(ssl_folder / "tinyjukebox.crt"),
                                         "private-key": str(ssl_folder / "tinyjukebox.key"),
                                         "users-file": str(ssl_folder / "users.yaml")}  # fmt: skip
    assert validate_config(config, TinyJukebox.get_schema()) is None
    UsersFile.write(str(ssl_folder / "users.yaml"), {"mama": {"password-hash": hash_password("geheim123"),
                                                              "role": "admin"}})  # fmt: skip
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    try:
        assert s.tinyjukebox.rest_api is not None
        port = s.tinyjukebox.rest_api.port
        client = ApiClient(port, str(ssl_folder / "tinyjukebox.crt")).login("mama", "geheim123")
        assert client.post("/api/buttons/volume-up").status == 204
        assert wait_for(lambda: volumes[-1] == 55)
        s.rfid(TAG_ALBUM)
        assert wait_for(lambda: (client.get("/api/status").json["album"] or {}).get("id") == "1")
        assert client.post("/api/buttons/next").status == 204
        assert wait_for(lambda: client.get("/api/status").json["album"]["playback"]["track_id"] == 1)
    finally:
        s.tinyjukebox.stop()
    with pytest.raises(ConnectionRefusedError):  # the server is stopped
        client.get("/api/status")


def test_failed_start_stops_what_has_been_started(config, logger, volumes, clock, tmp_path):
    """The rest api cannot listen (port in use) - the parts started before are stopped again, no thread keeps the
    process alive (systemd restarts the box only if the process ends)."""
    ssl_folder = tmp_path / "ssl"
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        config["tinyjukebox"]["rest-api"] = {"host": "127.0.0.1", "port": blocker.getsockname()[1],
                                             "certificate": str(ssl_folder / "tinyjukebox.crt"),
                                             "private-key": str(ssl_folder / "tinyjukebox.key"),
                                             "users-file": str(ssl_folder / "users.yaml")}  # fmt: skip
        before = {t for t in threading.enumerate() if not t.daemon}
        tinyjukebox = TinyJukebox(config, logger=logger, no_gui=True, hardware=False,
                                  player_factory=functools.partial(DummyPlayer, clock=clock))  # fmt: skip
        with pytest.raises(OSError, match="in use"):
            tinyjukebox.start()
    assert tinyjukebox.is_stopped
    assert tinyjukebox.stmoperation.get_active_state() == States.END
    assert volumes[-1] == 0
    assert wait_for(lambda: {t for t in threading.enumerate() if not t.daemon} <= before)


def test_folders_of_the_config_may_begin_with_the_home_folder(config, logger, library, clock, monkeypatch):
    """The box on the PC: ``~`` in the albums folder and in the json files is the home folder of the user."""
    monkeypatch.setenv("HOME", str(library.folder))
    config["tinyjukebox"]["albums-folder"] = "~/albums"
    config["tinyjukebox"]["json"] = {"tags": "~/json/tags.json", "targets": "~/json/targets.json",
                                     "activities": "~/json/activity.json"}  # fmt: skip
    assert validate_config(config, TinyJukebox.get_schema()) is None
    tinyjukebox = TinyJukebox(config, logger=logger, no_gui=True, hardware=False,
                              player_factory=functools.partial(DummyPlayer, clock=clock))  # fmt: skip
    assert {tag["uid"] for tag in tinyjukebox.control.list_tags()} == {tag.tag_uid for tag in library.tags.all()}
    assert tinyjukebox.control.get_album("1")["tracks"]


# --- bluetooth headset (the virtual one of a box without hardware) ----------------------------------------------------


def test_virtual_headset(config, logger, volumes, clock, library, monkeypatch):
    """The whole chain with the queue pubsub: pairing, output, volume, pause on disconnect, led."""
    from tinyjukebox.devices.bluetooth import virtual

    monkeypatch.setattr(virtual, "SEARCH_TIME", 0.1)
    config["tinyjukebox"]["bluetooth"] = {"default-volume": 30, "max-volume": 60}
    assert validate_config(config, TinyJukebox.get_schema()) is None
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    tinyjukebox, control = s.tinyjukebox, s.tinyjukebox.control
    try:
        assert {"virtual_headset", "virtual_headset_button", "bluetooth_pair"} <= set(tinyjukebox.ui_commands)
        s.rfid(TAG_ALBUM)
        s.settle()
        assert control.bluetooth_info()["available"]
        # next + prev held on the box: search (blue blinking fast), then paired (white) and connected
        s.send(BUTTON_PRESSED, Button.PREV)
        s.send("button/held", Button.NEXT)
        s.send("button/held", Button.PREV)
        assert control.status()["led"] == {"color_a": "BLUE", "color_b": "BLACK", "delay": 0.2}
        assert wait_for(lambda: control.bluetooth_info()["connected"])
        s.settle()
        status = control.status()
        assert status["led"] == {"color": "WHITE"}
        assert status["volume"]["output"] == "headphones"
        assert status["volume"]["volume"] == 30
        assert status["bluetooth"]["volume"] == 30
        assert status["album"]["playback"]["state"] == "play"
        assert tinyjukebox._output.headset
        speaker = volumes[-1]
        s.send(BUTTON_PRESSED, Button.VOLUME_UP)  # the buttons of the box change the headset
        assert wait_for(lambda: control.bluetooth_info()["volume"] == 35)
        assert volumes[-1] == speaker
        control.set_volume_limits("headphones", 20, 25)
        assert wait_for(lambda: control.bluetooth_info()["volume"] == 25)
        assert read_json(library.tags_file.replace("tags.json", "volume.json")) == {
            "headphones": {"default-volume": 20, "max-volume": 25}
        }
        # the headset goes: pause, the speaker is the output again
        control.virtual_headset(connected=False)
        s.settle()
        assert s.album_info()["state"] == "paused"
        assert control.status()["volume"]["output"] == "speaker"
        assert not tinyjukebox._output.headset
        s.send(BUTTON_PRESSED, Button.NEXT)  # goes on with the speaker
        assert s.album_info()["state"] == "play"
        control.virtual_headset(connected=True)  # connecting while it plays: the track continues on the headset
        s.settle()
        assert s.album_info()["state"] == "play"
        assert control.status()["volume"]["volume"] == 20  # the (new) start value
        control.virtual_headset(button="pause")
        s.settle()
        assert s.album_info()["state"] == "paused"
        tinyjukebox._rgb_led.show_overlay(Color.WHITE, duration=30)
    finally:
        tinyjukebox.stop()
    assert tinyjukebox._rgb_led.get_state() == {"color": "RED"}  # also while the led showed a search result


def test_changed_volume_limits_apply_after_a_restart(config, logger, volumes, clock, library):
    with open(library.tags_file.replace("tags.json", "volume.json"), "w", encoding="utf-8") as f:
        json.dump({"speaker": {"default-volume": 35, "max-volume": 45}}, f)
    s = Service(config, logger, functools.partial(DummyPlayer, clock=clock))
    try:
        s.settle()
        assert volumes[-1] == 35  # not the 50 of the config
        s.send(BUTTON_PRESSED, Button.VOLUME_UP)
        s.send(BUTTON_PRESSED, Button.VOLUME_UP)
        s.send(BUTTON_PRESSED, Button.VOLUME_UP)
        assert volumes[-1] == 45
        assert s.tinyjukebox.control.get_volume()["profiles"]["headphones"] == {
            "volume": 40, "default_volume": 40, "max_volume": 75,
        }  # fmt: skip
    finally:
        s.tinyjukebox.stop()
