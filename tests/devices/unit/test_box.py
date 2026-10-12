"""The complete box in one process: real drivers on fake hardware, state machines and player share one LocalQueue."""

import copy
import logging

import pytest

from tests.devices.fakes import FakeBluez
from tests.support import TAG_ALBUM, box_config, wait_for
from tests.unit.test_config import DEPLOYMENT_CONFIG_FOLDER
from tinyjukebox.core.config import read_config
from tinyjukebox.core.pubsub import LocalQueue
from tinyjukebox.devices.drivers import bluetooth
from tinyjukebox.stm.stmoperation import States
from tinyjukebox.tinyjukebox import TinyJukebox
from tinyjukebox.volumemanager import AlsaVolumeControl, VolumeProfile

VOLUME_UP_PIN = 6
NEXT_PIN = 25
PREV_PIN = 23


@pytest.fixture
def volumes(monkeypatch):
    """Volumes set on the alsa mixer (amixer is not called)."""
    volumes = []
    monkeypatch.setattr(AlsaVolumeControl, "set_volume", lambda self, volume: volumes.append(volume))
    return volumes


@pytest.fixture
def config(library, rfid_reader):
    """The deployment config of the box with the test library; rfid-reader is a process controlled by the test."""
    deployment = read_config(DEPLOYMENT_CONFIG_FOLDER / "tinyjukebox.yaml")
    config = box_config(library)
    config["drivers"] = copy.deepcopy(deployment["drivers"])
    for driver in config["drivers"]:
        if driver["type"] == "rfid":
            driver["command"] = rfid_reader.command
    return config


@pytest.fixture
def bluez(monkeypatch):
    """BlueZ of the bluetooth driver - the tests never touch the bluetooth of the machine they run on."""
    fake = FakeBluez()
    monkeypatch.setattr(bluetooth, "DbusBluez", lambda logger, adapter: fake)
    return fake


@pytest.fixture
def box(config, logger, gpio, volumes, bluez):
    tinyjukebox = TinyJukebox(config, logger=logger, no_gui=True, hardware=True, webserver=False, audio=False)
    tinyjukebox.start()
    yield tinyjukebox
    if not tinyjukebox.is_stopped:
        tinyjukebox.stop()


def press(gpio, pin):
    gpio.set_input(pin, 0)
    gpio.set_input(pin, 1)


def test_drivers_and_state_machines_share_one_queue(box):
    assert isinstance(box._pubsub_client, LocalQueue)
    assert box._driver_manager is not None
    assert {driver.driver_type.lower() for driver in box._driver_manager.drivers.values()} == {
        "rfid",
        "input",
        "rgbled",
        "bluetooth",
    }
    assert all(driver._pubsub_client is box._pubsub_client for driver in box._driver_manager.drivers.values())


def latency_lines(caplog):
    return [record.getMessage() for record in caplog.records if record.name.endswith(".latency")]


def test_tag_starts_and_stops_the_album(box, rfid_reader, caplog):
    caplog.set_level(logging.INFO)
    rfid_reader.ready()
    rfid_reader.place(TAG_ALBUM)
    assert wait_for(lambda: box.stmoperation.get_active_state() == States.PLAY_ALBUM)
    assert wait_for(lambda: any(line.startswith("tag: ffmpeg started after") for line in latency_lines(caplog)))
    assert latency_lines(caplog)[0].startswith("tag: NEW_TAG after")
    rfid_reader.remove()
    assert wait_for(lambda: box.stmoperation.get_active_state() == States.WAIT_FOR_TAG)


def test_button_changes_the_volume(box, gpio, volumes, caplog):
    caplog.set_level(logging.INFO)
    speaker = box._volumes.manager(VolumeProfile.SPEAKER)
    start = speaker.get_volume()
    press(gpio, VOLUME_UP_PIN)
    assert wait_for(lambda: speaker.get_volume() > start)
    assert volumes[-1] == speaker.get_volume()
    assert wait_for(lambda: len(latency_lines(caplog)) == 2)
    assert latency_lines(caplog)[0].startswith("volume button: handler after")
    assert latency_lines(caplog)[1].startswith("volume button: volume set after")


def test_held_volume_button_keeps_changing_the_volume(box, gpio, volumes):
    """The deployment config: one step per press, then a small step every 0.2 s until the button is released."""
    speaker = box._volumes.manager(VolumeProfile.SPEAKER)
    start = speaker.get_volume()
    gpio.set_input(VOLUME_UP_PIN, 0)
    assert wait_for(lambda: speaker.get_volume() >= start + 7, timeout=3)
    gpio.set_input(VOLUME_UP_PIN, 1)
    assert wait_for(lambda: volumes[-1] == speaker.get_volume())
    changes = volumes[volumes.index(start + 5) :]
    assert changes[:3] == [start + 5, start + 6, start + 7]
    reached = speaker.get_volume()
    assert not wait_for(lambda: speaker.get_volume() != reached, timeout=0.5)  # released: it stays


def test_held_prev_button_restarts_the_album(box, rfid_reader, gpio):
    """The deployment config: prev held for 1 s plays the album from the beginning."""
    rfid_reader.place(TAG_ALBUM)
    assert wait_for(lambda: box.stmoperation.get_album() is not None)  # set once the album has been started
    album = box.stmoperation.get_album()
    assert wait_for(lambda: album.get_info()["state"] == "play")
    press(gpio, NEXT_PIN)
    press(gpio, NEXT_PIN)
    assert wait_for(lambda: album.get_info()["track"]["track_id"] == 2)
    gpio.set_input(PREV_PIN, 0)
    assert wait_for(lambda: album.get_info()["track"]["track_id"] == 1)  # the press
    assert wait_for(lambda: album.get_info()["track"]["track_id"] == 0, timeout=3)  # the hold
    gpio.set_input(PREV_PIN, 1)


def test_cli_offers_the_driver_commands(box):
    assert "list_drivers" in box.ui_commands
    assert "rgbled_gpio_state" in box.ui_commands


def test_no_hardware_creates_no_drivers(config, logger):
    tinyjukebox = TinyJukebox(config, logger=logger, no_gui=True, hardware=False, webserver=False, audio=False)
    assert tinyjukebox._driver_manager is None
