"""Headset - what the box does with the state of the bluetooth headset: output, volume, pause, pairing."""

import pytest

from tests.support import make_volumes, wait_for
from tinyjukebox import headset as headset_module
from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.headset import BluetoothVolumeControl, Headset
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_HELD,
    BUTTON_PRESSED,
    BUTTON_RELEASED,
    BluetoothAction,
    BluetoothCommand,
    BluetoothDevice,
    BluetoothState,
    Button,
    PairingResult,
)
from tinyjukebox.rgbled import Color
from tinyjukebox.volumemanager import VolumeProfile

DEVICE = BluetoothDevice("AC:80:0A:11:22:33", "WH-CH530")
PAIRED = BluetoothState(device=DEVICE)
CONNECTED = BluetoothState(device=DEVICE, connected=True, battery=70)
PAIR = BluetoothCommand(BluetoothAction.PAIR)


class FakeAlbum:
    """A playback that handles the loss of the headset at once - ``deferred``: only when the test calls
    :meth:`handled`, as a state machine does in its worker."""

    def __init__(self, deferred=False):
        self.calls = []
        self.deferred = deferred
        self.then = []

    def cmd_output_lost(self, then=None):
        self.calls.append("lost")
        if then is not None:
            self.then.append(then)
        if not self.deferred:
            self.handled()

    def handled(self):
        while self.then:
            self.then.pop(0)()

    def cmd_output_changed(self):
        self.calls.append("changed")


class Albums:
    def __init__(self):
        self.album = None

    def get_album(self):
        return self.album


class Box:
    def __init__(self, pubsub, rgbled, logger):
        self.pubsub = pubsub
        self.rgbled = rgbled
        self.output = AudioOutput()
        control = BluetoothVolumeControl(pubsub, logger)
        self.volumes = make_volumes(logger, None, None, control)
        self.albums = Albums()
        self.headset = Headset(pubsub, logger, rgbled, self.output, self.volumes, control, self.albums)
        self.headset.start()

    def state(self, state):
        self.pubsub.deliver(BLUETOOTH_STATE, state)

    def commands(self):
        return self.pubsub.messages(BLUETOOTH_COMMAND)

    def volume_commands(self):
        return [command.volume for command in self.commands() if command.action is BluetoothAction.VOLUME]

    def device(self):
        return self.output.current()[0], self.output.headset


@pytest.fixture
def box(pubsub, rgbled, logger, monkeypatch):
    from tinyjukebox.albumplayer import simpleplayer

    # the device the players would use (conftest replaces the output command with a null sink)
    monkeypatch.setattr(simpleplayer, "output_command", lambda null_output=False, buffer_time=None, device=None: device)
    box = Box(pubsub, rgbled, logger)
    yield box
    box.headset.stop()


def test_without_bluetooth(box):
    assert box.headset.state is None
    assert box.headset.info() == {
        "available": False, "virtual": False, "device": None, "connected": False, "battery": None, "volume": None,
        "pairing": False, "pairing_result": None,
    }  # fmt: skip
    assert box.device() == (None, False)
    assert box.volumes.active is VolumeProfile.SPEAKER
    assert box.commands() == []  # the volume of the headset is not set while there is none


def test_paired_but_not_connected(box):
    box.state(PAIRED)
    assert box.headset.info()["device"] == {"address": DEVICE.address, "name": "WH-CH530"}
    assert box.headset.info()["available"]
    assert box.device() == (None, False)
    assert box.volumes.active is VolumeProfile.SPEAKER


def test_connected_headset_is_output_and_volume(box):
    box.albums.album = FakeAlbum()
    box.volumes.manager(VolumeProfile.HEADPHONES).set_volume(70)  # of the last time - not sent, not kept
    box.state(CONNECTED)
    # the players open the headset with the start value - whatever it had the last time
    assert box.device() == ("bluealsa:DEV=AC:80:0A:11:22:33,PROFILE=a2dp,VOL=40+", True)
    assert box.volumes.active is VolumeProfile.HEADPHONES
    assert box.volume_commands() == [40]  # always the start value
    assert box.albums.album.calls == ["changed"]  # a playing track continues on the headset
    assert box.headset.info() == {"available": True, "virtual": False, "connected": True, "battery": 70,
                                  "device": {"address": DEVICE.address, "name": "WH-CH530"}, "volume": None,
                                  "pairing": False, "pairing_result": None}  # fmt: skip
    box.volumes.volume_up()  # buttons of the box
    assert box.volume_commands() == [40, 45]
    assert box.device()[0].endswith(",VOL=45+")  # the next track starts with the current volume
    box.state(BluetoothState(device=DEVICE, connected=True, battery=65, volume=45))  # report of the own change
    assert box.volumes.get_volume() == 45
    assert box.albums.album.calls == ["changed"]


def test_virtual_headset_plays_on_the_default_device(box):
    box.state(BluetoothState(device=DEVICE, connected=True, virtual=True))
    assert box.device() == (None, True)
    assert box.headset.info()["virtual"]


def test_volume_changed_on_the_headset_is_taken_over_and_limited(box):
    box.state(BluetoothState(device=DEVICE, connected=True, volume=70))  # as loud as the last time
    box.state(BluetoothState(device=DEVICE, connected=True, volume=70, battery=50))  # unchanged: no volume report
    assert box.volumes.get_volume() == 40
    box.state(BluetoothState(device=DEVICE, connected=True, volume=40))
    box.headset._connected_at -= headset_module.CONNECT_SETTLE  # a while after the connect
    box.state(BluetoothState(device=DEVICE, connected=True, volume=52))  # volume button of the headset
    assert box.volumes.get_volume() == 52
    box.state(BluetoothState(device=DEVICE, connected=True, volume=90))  # above the maximum
    assert box.volumes.get_volume() == 75
    assert box.volume_commands() == [40, 75]
    assert box.volumes.manager(VolumeProfile.SPEAKER).get_volume() == 50


def test_the_volume_of_the_last_time_that_comes_late_after_a_connect_changes_nothing(box):
    """BlueALSA may report the volume the headset had the last time only after the box has set its start value: the
    start value counts and is set again - a button of the box pressed meanwhile counts as well."""
    box.state(CONNECTED)
    assert box.volume_commands() == [40]
    box.state(BluetoothState(device=DEVICE, connected=True, volume=70))  # late: the volume of the last time
    assert box.volumes.get_volume() == 40
    assert box.volume_commands() == [40, 40]  # set again
    box.volumes.volume_up()
    box.state(BluetoothState(device=DEVICE, connected=True, volume=45))  # the report of the own change
    assert box.volumes.get_volume() == 45
    box.headset._connected_at -= headset_module.CONNECT_SETTLE
    box.state(BluetoothState(device=DEVICE, connected=True, volume=60))  # later: a button of the headset
    assert box.volumes.get_volume() == 60


def test_just_after_a_connect_a_quieter_report_counts(box):
    box.state(CONNECTED)
    box.state(BluetoothState(device=DEVICE, connected=True, volume=30))  # turned down on the headset
    assert box.volumes.get_volume() == 30
    box.state(BluetoothState(device=DEVICE, connected=True, volume=70))  # late: the volume of the last time
    assert box.volumes.get_volume() == 30
    assert box.volume_commands() == [40, 30]  # set again


def test_just_after_a_connect_a_change_on_the_box_ends_the_rule(box):
    """E.g. the web page sets the volume right after the pairing: the buttons of the headset are followed at once."""
    box.state(CONNECTED)
    box.volumes.volume_up()  # a button of the box
    box.state(BluetoothState(device=DEVICE, connected=True, volume=60))  # a button of the headset
    assert box.volumes.get_volume() == 60


def test_lost_headset_pauses_and_the_speaker_takes_over(box):
    album = box.albums.album = FakeAlbum()
    box.state(CONNECTED)
    box.state(PAIRED)
    assert album.calls == ["changed", "lost"]
    assert box.device() == (None, False)
    assert box.volumes.active is VolumeProfile.SPEAKER
    box.volumes.volume_up()  # changes the speaker only
    assert box.volume_commands() == [40]
    box.state(CONNECTED)  # back
    assert album.calls == ["changed", "lost", "changed"]
    assert box.volume_commands() == [40, 40]
    box.state(BluetoothState())  # unpaired while connected
    assert album.calls[-1] == "lost"


def test_the_speaker_is_the_output_only_once_the_playback_has_paused(box):
    """Whatever starts before the playback has handled the loss - a track that was due, a button pressed just before -
    is tried on the headset that has gone and fails there (the players pause then), never on the speaker."""
    album = box.albums.album = FakeAlbum(deferred=True)
    box.state(CONNECTED)
    headset = box.device()
    box.state(PAIRED)
    assert album.calls == ["changed", "lost"]
    assert box.device() == headset  # not yet the speaker
    assert box.volumes.active is VolumeProfile.SPEAKER  # the buttons change the speaker at once - nothing plays
    album.handled()
    assert box.device() == (None, False)


def test_the_speaker_takes_over_after_a_while_if_the_playback_never_answers(box, monkeypatch):
    from tinyjukebox import headset as module

    monkeypatch.setattr(module, "SPEAKER_AFTER_LOSS", 0.05)
    album = box.albums.album = FakeAlbum(deferred=True)
    box.state(CONNECTED)
    box.state(PAIRED)
    assert wait_for(lambda: box.device() == (None, False))
    album.handled()  # late: changes nothing
    assert box.device() == (None, False)


def test_a_headset_that_comes_back_before_the_pause_keeps_the_output(box):
    album = box.albums.album = FakeAlbum(deferred=True)
    box.state(CONNECTED)
    box.state(PAIRED)
    box.state(CONNECTED)  # back before the playback handled the loss
    album.handled()  # the switch to the speaker that was asked for before is void
    assert box.device() == ("bluealsa:DEV=AC:80:0A:11:22:33,PROFILE=a2dp,VOL=40+", True)


def test_without_a_playback_the_speaker_takes_over_at_once(box):
    box.state(CONNECTED)
    box.state(PAIRED)
    assert box.device() == (None, False)


def test_name_learned_after_connecting_changes_nothing(box):
    album = box.albums.album = FakeAlbum()
    box.state(BluetoothState(device=BluetoothDevice(DEVICE.address, DEVICE.address), connected=True))
    box.state(CONNECTED)  # BlueZ knows the name now
    assert album.calls == ["changed"]
    assert box.volume_commands() == [40]


def test_another_headset_takes_over(box):
    box.state(CONNECTED)
    other = BluetoothDevice("00:11:22:33:44:55", "other")
    box.state(BluetoothState(device=other, connected=True))  # paired anew while the old one was connected
    assert box.device() == ("bluealsa:DEV=00:11:22:33:44:55,PROFILE=a2dp,VOL=40+", True)
    assert box.volume_commands() == [40, 40]


def test_search_led(box, rgbled):
    box.state(BluetoothState(pairing=True))
    assert rgbled.last() == ("overlay", Color.BLUE, Color.BLACK, 120.0)
    box.state(BluetoothState(pairing=True, device=DEVICE))  # still searching
    assert len(rgbled.commands) == 1
    box.state(BluetoothState(device=DEVICE, connected=True, pairing_result=PairingResult.PAIRED))
    assert rgbled.last() == ("overlay", Color.WHITE, None, 2.0)
    box.state(BluetoothState(pairing=True))
    box.state(BluetoothState(pairing_result=PairingResult.NOT_FOUND))
    assert rgbled.last() == ("overlay", Color.RED, Color.BLACK, 3.0)
    box.state(BluetoothState(pairing=True))
    box.state(BluetoothState(pairing_result=PairingResult.FAILED))
    assert rgbled.last() == ("overlay", Color.RED, Color.BLACK, 3.0)
    box.headset.stop()
    assert rgbled.last() == ("clear_overlay",)


def test_next_and_prev_held_start_the_search(box, pubsub):
    box.state(BluetoothState())
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
    pubsub.deliver(BUTTON_HELD, Button.NEXT)
    assert box.commands() == []
    pubsub.deliver(BUTTON_PRESSED, Button.PREV)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    assert box.commands() == [PAIR]
    pubsub.deliver(BUTTON_RELEASED, Button.NEXT)
    pubsub.deliver(BUTTON_RELEASED, Button.PREV)
    assert box.commands() == [PAIR]


@pytest.mark.parametrize("ends_hold", [BUTTON_RELEASED, BUTTON_PRESSED])
def test_buttons_held_one_after_the_other_do_not_pair(box, pubsub, ends_hold):
    pubsub.deliver(BUTTON_HELD, Button.NEXT)
    pubsub.deliver(ends_hold, Button.NEXT)  # released (a new press means the release has been missed)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    pubsub.deliver(BUTTON_HELD, Button.VOLUME_UP)
    pubsub.deliver(BUTTON_HELD, "next")  # not a button
    assert box.commands() == []


def test_commands(box):
    box.state(BluetoothState())
    box.headset.pair()
    box.headset.unpair()
    assert box.commands() == [PAIR, BluetoothCommand(BluetoothAction.UNPAIR)]


def test_pairing_without_bluetooth_shows_the_failure(box, pubsub, rgbled):
    pubsub.deliver(BUTTON_HELD, Button.NEXT)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    assert box.commands() == []
    assert rgbled.last() == ("overlay", Color.RED, Color.BLACK, 3.0)


def test_invalid_state_message_and_stop(box, pubsub, caplog):
    box.state("connected")
    assert "expected a BluetoothState" in caplog.text
    assert box.headset.state is None
    box.headset.stop()
    assert pubsub.subscribed_topics() == {}
