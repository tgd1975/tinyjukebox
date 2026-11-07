"""Bluetooth driver: one paired headset, search and pairing, reconnect, volume and buttons - on a fake BlueZ."""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest

from tests.devices.fakes import FakeBluez, FakeMixer
from tests.devices.support import BLUETOOTH, driver_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.bluetooth.bluez import BluezError
from tinyjukebox.devices.bluetooth.link import button_of
from tinyjukebox.devices.drivers import bluetooth
from tinyjukebox.devices.drivers.bluetooth import Bluetooth
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_PRESSED,
    PLAYBACK,
    BluetoothAction,
    BluetoothCommand,
    BluetoothDevice,
    BluetoothState,
    Button,
    PairingResult,
    PlaybackStatus,
)

HEADSET = "AC:80:0A:11:22:33"
OTHER = "00:11:22:33:44:55"
PHONE = "10:20:30:40:50:60"
PHONE_CLASS = 0x5A020C
PAIR = BluetoothCommand(BluetoothAction.PAIR)
UNPAIR = BluetoothCommand(BluetoothAction.UNPAIR)


@pytest.fixture
def bluez() -> FakeBluez:
    return FakeBluez()


@pytest.fixture
def mixers() -> list[FakeMixer]:
    return []


Start = Callable[..., Bluetooth]


@pytest.fixture
def start(
    pubsub: SyncPubSub, logger: logging.Logger, log_file: str, bluez: FakeBluez, mixers: list[FakeMixer]
) -> Iterator[Start]:
    """Starts the driver (short times: reconnect every 0.05 s, 3 pages of a headset that is not there, search 0.4 s,
    collecting 0.1 s)."""
    drivers: list[Bluetooth] = []

    def mixer(address: str) -> FakeMixer:
        mixers.append(FakeMixer(address))
        return mixers[-1]

    def factory(search_time: float = 0.4, reconnect_attempts: int = 3, **config: Any) -> Bluetooth:
        driver = Bluetooth(driver_config({**BLUETOOTH, **config}, log_file), pubsub, logger, bluez, mixer)
        driver.reconnect_interval = 0.05
        driver.reconnect_attempts = reconnect_attempts
        driver.search_time = search_time
        driver.collect_time = 0.1
        driver.retry_interval = 0.05
        drivers.append(driver)
        driver.start()
        return driver

    yield factory
    for driver in drivers:
        if not driver.is_stopped:
            driver.stop()


def states(pubsub: SyncPubSub) -> list[Any]:
    return pubsub.messages(BLUETOOTH_STATE)


def last(pubsub: SyncPubSub) -> BluetoothState:
    assert wait_for(lambda: states(pubsub))
    state = states(pubsub)[-1]
    assert isinstance(state, BluetoothState)
    return state


def paired_headset(bluez: FakeBluez, in_range: bool = True) -> None:
    bluez.set_device(bluez.device(HEADSET, paired=True, trusted=True))
    if in_range:
        bluez.in_range.add(bluez.path(HEADSET))


# --- state / reconnect ------------------------------------------------------------------------------------------------


def test_without_paired_headset(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    start()
    assert last(pubsub) == BluetoothState()
    assert bluez.player is not None  # the box is registered as media player
    time.sleep(0.15)
    assert bluez.called("connect_device") == []
    assert states(pubsub) == [BluetoothState()]  # only changes are published


def test_paired_headset_is_connected_as_soon_as_it_is_reachable(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, mixers: list[FakeMixer]
) -> None:
    paired_headset(bluez, in_range=False)
    start(reconnect_attempts=100)
    assert last(pubsub) == BluetoothState(device=BluetoothDevice(HEADSET, "WH-CH530"))
    assert wait_for(lambda: len(bluez.called("connect_device")) >= 2)  # tries again
    assert not last(pubsub).connected
    bluez.in_range.add(bluez.path(HEADSET))  # switched on
    assert wait_for(lambda: last(pubsub).connected)
    assert wait_for(lambda: mixers and mixers[0].watching)
    assert mixers[0].address == HEADSET
    tries = len(bluez.called("connect_device"))
    time.sleep(0.15)
    assert len(bluez.called("connect_device")) == tries  # connected - no more attempts


def test_a_headset_that_is_not_there_is_paged_only_a_few_times(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez
) -> None:
    """Each page holds the radio chip the Pi 3 shares with the WLAN: after a few attempts the box waits for the
    headset, which connects itself when it is switched on."""
    paired_headset(bluez, in_range=False)
    start()
    assert wait_for(lambda: len(bluez.called("connect_device")) == 3)
    time.sleep(0.25)
    assert len(bluez.called("connect_device")) == 3  # no more pages
    bluez.update(HEADSET, connected=True, audio=True)  # switched on: it connects itself
    assert wait_for(lambda: last(pubsub).connected)


def test_a_lost_headset_is_paged_a_few_times_again(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    paired_headset(bluez)
    start()
    assert wait_for(lambda: last(pubsub).connected)
    bluez.in_range.clear()
    tries = len(bluez.called("connect_device"))
    bluez.update(HEADSET, connected=False, audio=False)  # out of range
    assert wait_for(lambda: not last(pubsub).connected)
    assert wait_for(lambda: len(bluez.called("connect_device")) == tries + 3)
    time.sleep(0.25)
    assert len(bluez.called("connect_device")) == tries + 3
    bluez.in_range.add(bluez.path(HEADSET))
    bluez.update(HEADSET, connected=True, audio=True)  # back: it connects itself
    assert wait_for(lambda: last(pubsub).connected)


def test_a_connection_without_audio_is_completed_again_and_again(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez
) -> None:
    """Connected, but without audio transport: Connect needs no page, the attempts are not limited."""
    bluez.set_device(bluez.device(HEADSET, paired=True, trusted=True, connected=True))
    bluez.connect_error = BluezError("org.bluez.Error.Failed", "a2dp not ready")
    start()
    assert wait_for(lambda: len(bluez.called("connect_device")) >= 6)
    assert not last(pubsub).connected


def test_connected_means_that_the_headset_can_play(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    bluez.set_device(bluez.device(HEADSET, paired=True, connected=True))  # connected, but no audio transport (yet)
    start()
    assert last(pubsub).device is not None
    assert not last(pubsub).connected
    bluez.update(HEADSET, audio=True, battery=85)
    assert wait_for(lambda: last(pubsub).connected)
    assert last(pubsub).battery == 85


def test_volume(start: Start, pubsub: SyncPubSub, bluez: FakeBluez, mixers: list[FakeMixer]) -> None:
    paired_headset(bluez)
    start()
    assert wait_for(lambda: mixers and mixers[0].watching)
    assert last(pubsub).volume is None  # unknown until the mixer reports it
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 40))
    assert wait_for(lambda: last(pubsub).volume == 40)
    assert mixers[0].volumes == [40]
    mixers[0].report(52)  # volume button of the headset
    assert wait_for(lambda: last(pubsub).volume == 52)
    # the headset goes: the mixer is not watched any more, the volume is unknown
    bluez.update(HEADSET, connected=False, audio=False)
    bluez.in_range.clear()
    assert wait_for(lambda: not last(pubsub).connected)
    assert last(pubsub).volume is None
    assert wait_for(lambda: not mixers[0].watching)
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 60))
    time.sleep(0.05)
    assert mixers[0].volumes == [40]


def test_volume_is_set_as_soon_as_the_mixer_is_there(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, mixers: list[FakeMixer], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The box sets the start value right when the headset connects - the mixer of BlueALSA may come a moment later."""
    from tinyjukebox.devices.bluetooth import link

    monkeypatch.setattr(link, "VOLUME_RETRY_INTERVAL", 0.02)
    paired_headset(bluez, in_range=False)
    start()
    assert wait_for(lambda: last(pubsub).device is not None)
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 40))  # no mixer yet
    bluez.in_range.add(bluez.path(HEADSET))
    assert wait_for(lambda: last(pubsub).volume == 40)
    mixers[0].watching = False  # the report of the mixer is missed: the volume the driver has set is known anyway
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 42))
    assert wait_for(lambda: last(pubsub).volume == 42)
    mixers[0].watching = True
    mixers[0].failures = 3
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 45))
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 50))  # the latest one wins
    assert wait_for(lambda: last(pubsub).volume == 50)
    assert mixers[0].volumes == [40, 42, 50]
    mixers[0].failures = 1000  # the mixer never works: given up, not retried forever
    monkeypatch.setattr(link, "VOLUME_RETRIES", 3)
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 60))
    assert wait_for(lambda: mixers[0].failures == 997)
    time.sleep(0.1)
    assert mixers[0].failures == 997


def test_headset_removed_or_paired_outside_the_box(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    paired_headset(bluez)
    start()
    assert wait_for(lambda: last(pubsub).connected)
    bluez.remove(HEADSET)  # e.g. bluetoothctl remove
    assert wait_for(lambda: last(pubsub) == BluetoothState())
    bluez.set_device(bluez.device(OTHER, "other", paired=True))  # e.g. bluetoothctl pair
    assert wait_for(lambda: last(pubsub).device == BluetoothDevice(OTHER, "other"))


def test_several_paired_devices_at_the_start(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, caplog: pytest.LogCaptureFixture
) -> None:
    bluez.set_device(bluez.device(PHONE, "phone", paired=True, device_class=PHONE_CLASS))
    paired_headset(bluez, in_range=False)
    start()
    assert last(pubsub).device == BluetoothDevice(HEADSET, "WH-CH530")  # the audio output
    assert "2 paired devices" in caplog.text


# --- search and pairing -----------------------------------------------------------------------------------------------


def test_pairing_takes_the_audio_output_with_the_strongest_signal(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez
) -> None:
    paired_headset(bluez, in_range=False)  # the old headset - switched off
    start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: last(pubsub).pairing)
    assert wait_for(lambda: bluez.discovering)
    new, far = "11:11:11:11:11:11", "22:22:22:22:22:22"
    bluez.in_range.add(bluez.path(new))
    bluez.set_device(bluez.device(far, "far", rssi=-80, discovered=True))
    bluez.set_device(bluez.device(PHONE, "phone", rssi=-20, discovered=True, device_class=PHONE_CLASS))
    bluez.set_device(bluez.device("33:33:33:33:33:33", "seen before", rssi=-10))  # not in pairing mode now
    bluez.set_device(bluez.device(new, "new", rssi=-45, discovered=True, device_class=None,
                                  uuids=("0000110b-0000-1000-8000-00805f9b34fb",)))  # fmt: skip
    assert wait_for(lambda: not last(pubsub).pairing)
    assert last(pubsub) == BluetoothState(
        device=BluetoothDevice(new, "new"), connected=True, pairing_result=PairingResult.PAIRED
    )
    # paired without searching meanwhile, the agent accepted exactly this headset
    assert bluez.called("pair") == [("pair", bluez.path(new), False, True)]
    assert bluez.called("set_trusted") == [("set_trusted", bluez.path(new))]
    # the old pairing has been removed - after the new one was complete
    names = [call[0] for call in bluez.calls]
    assert bluez.called("remove_device") == [("remove_device", bluez.path(HEADSET))]
    assert names.index("remove_device") > names.index("set_trusted")
    assert not bluez.discovering
    assert bluez.policy.allows_service(bluez.path(new))
    assert not bluez.policy.allows_service(bluez.path(PHONE))
    assert not bluez.policy.allows_pairing(bluez.path(new))  # the search is over


def test_search_finds_nothing(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    paired_headset(bluez, in_range=False)
    start(search_time=0.15)
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)  # a search is running already
    assert wait_for(lambda: last(pubsub).pairing)
    bluez.set_device(bluez.device(PHONE, "phone", rssi=-20, discovered=True, device_class=PHONE_CLASS))
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.NOT_FOUND)
    assert last(pubsub) == BluetoothState(
        device=BluetoothDevice(HEADSET, "WH-CH530"), pairing_result=PairingResult.NOT_FOUND
    )  # the old pairing stays
    assert len(bluez.called("start_discovery")) == 1
    assert len(bluez.called("stop_discovery")) == 1
    assert bluez.called("pair") == []
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)  # a new search is possible
    assert wait_for(lambda: len(bluez.called("start_discovery")) == 2)
    assert wait_for(lambda: last(pubsub).pairing_result is None)


def test_search_waits_until_bluez_can_search(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.g. BlueZ still tries to reach the old headset, which is switched off."""
    from tinyjukebox.devices.bluetooth import link

    monkeypatch.setattr(link, "DISCOVERY_RETRY", 0.02)
    bluez.busy = 2
    start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: bluez.discovering)
    assert len(bluez.called("start_discovery")) == 3
    bluez.in_range.add(bluez.path(HEADSET))
    bluez.set_device(bluez.device(HEADSET, rssi=-40, discovered=True))
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.PAIRED)


def test_search_that_can_never_start_fails(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tinyjukebox.devices.bluetooth import link

    monkeypatch.setattr(link, "DISCOVERY_RETRY", 0.05)
    bluez.busy = 1000
    start(search_time=0.2)
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.FAILED)
    assert not last(pubsub).pairing


def test_failed_pairing_keeps_the_old_headset(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, caplog: pytest.LogCaptureFixture
) -> None:
    paired_headset(bluez)
    bluez.pair_error = BluezError("org.bluez.Error.AuthenticationFailed", "Authentication Failed")
    start()
    assert wait_for(lambda: last(pubsub).connected)
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: bluez.discovering)
    bluez.set_device(bluez.device(OTHER, "other", rssi=-40, discovered=True))
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.FAILED)
    assert last(pubsub).device == BluetoothDevice(HEADSET, "WH-CH530")
    assert last(pubsub).connected
    assert bluez.called("remove_device") == []
    assert "pairing failed: org.bluez.Error.AuthenticationFailed" in caplog.text


def test_paired_but_not_connected_counts_as_paired(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: bluez.discovering)
    bluez.set_device(bluez.device(HEADSET, rssi=-40, discovered=True))  # pairs, but does not answer the connect
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.PAIRED)
    assert not last(pubsub).connected
    tries = len(bluez.called("connect_device"))
    assert wait_for(lambda: len(bluez.called("connect_device")) == tries + 3)  # tried again after the pairing
    bluez.update(HEADSET, connected=True, audio=True)  # then it connects itself
    assert wait_for(lambda: last(pubsub).connected)


def test_the_same_headset_can_be_paired_again(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    """E.g. after the headset has been reset: BlueZ still has the old pairing."""
    paired_headset(bluez, in_range=False)
    start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: bluez.discovering)
    bluez.in_range.add(bluez.path(HEADSET))
    bluez.update(HEADSET, rssi=-40, discovered=True)
    assert wait_for(lambda: bluez.called("remove_device"))  # the old pairing goes first ...
    assert bluez.called("pair") == []
    bluez.set_device(bluez.device(HEADSET, rssi=-40, discovered=True))  # ... the search finds it anew
    assert wait_for(lambda: last(pubsub).pairing_result is PairingResult.PAIRED)
    assert last(pubsub).connected
    assert len(bluez.called("pair")) == 1


def test_unpair(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    start()
    pubsub.publish(BLUETOOTH_COMMAND, UNPAIR)  # nothing paired
    time.sleep(0.05)
    assert bluez.called("remove_device") == []
    paired_headset(bluez)
    assert wait_for(lambda: last(pubsub).connected)
    pubsub.publish(BLUETOOTH_COMMAND, UNPAIR)
    assert wait_for(lambda: last(pubsub) == BluetoothState())
    assert bluez.called("remove_device") == [("remove_device", bluez.path(HEADSET))]


# --- buttons of the headset -------------------------------------------------------------------------------------------


def test_buttons_of_the_headset(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    start()
    for command in ("Next", "Previous", "Play", "Pause", "Stop", "PlayPause", "Rewind"):
        bluez.press(command)
    assert wait_for(lambda: len(pubsub.messages(BUTTON_PRESSED)) == 6)
    assert pubsub.messages(BUTTON_PRESSED) == [
        Button.NEXT, Button.PREV, Button.PLAY, Button.PAUSE, Button.PAUSE, Button.PLAY,
    ]  # fmt: skip
    # the headset is told what plays - play/pause switches accordingly
    pubsub.publish(PLAYBACK, PlaybackStatus.PLAYING)
    assert wait_for(lambda: bluez.playback_status == "playing")
    bluez.press("PlayPause")
    assert wait_for(lambda: pubsub.messages(BUTTON_PRESSED)[-1] == Button.PAUSE)


def test_play_pause_toggle(start: Start, pubsub: SyncPubSub, bluez: FakeBluez) -> None:
    start(**{"play-pause-toggle": True})
    pubsub.publish(PLAYBACK, PlaybackStatus.PLAYING)
    assert wait_for(lambda: bluez.playback_status == "playing")
    bluez.press("Play")  # the headset sends play for both
    assert wait_for(lambda: pubsub.messages(BUTTON_PRESSED) == [Button.PAUSE])
    pubsub.publish(PLAYBACK, PlaybackStatus.PAUSED)
    assert wait_for(lambda: bluez.playback_status == "paused")
    bluez.press("Play")
    assert wait_for(lambda: pubsub.messages(BUTTON_PRESSED) == [Button.PAUSE, Button.PLAY])


def test_button_of() -> None:
    assert button_of("VolumeUp", PlaybackStatus.STOPPED, False) is Button.VOLUME_UP
    assert button_of("VolumeDown", PlaybackStatus.STOPPED, True) is Button.VOLUME_DOWN
    assert button_of("Pause", PlaybackStatus.PAUSED, True) is Button.PLAY
    assert button_of("Pause", PlaybackStatus.PAUSED, False) is Button.PAUSE
    assert button_of("Eject", PlaybackStatus.PLAYING, True) is None


class FakeInput:
    instances: ClassVar[list["FakeInput"]] = []

    def __init__(self, on_command: Callable[[str], object], logger: logging.Logger) -> None:
        self.on_command = on_command
        self.running = False
        FakeInput.instances.append(self)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def test_buttons_via_the_input_device(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeInput.instances.clear()
    monkeypatch.setattr(bluetooth, "AvrcpInput", FakeInput)
    driver = start(avrcp="input")
    reader = FakeInput.instances[0]
    assert reader.running
    assert bluez.player is None  # no media player: BlueZ passes the buttons to the input device
    pubsub.publish(PLAYBACK, PlaybackStatus.PLAYING)
    assert wait_for(lambda: bluez.playback_status == "playing")
    for command in ("Next", "PlayPause", "VolumeUp", "Eject"):
        reader.on_command(command)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.NEXT, Button.PAUSE, Button.VOLUME_UP]
    driver.stop()
    assert not reader.running


# --- life cycle -------------------------------------------------------------------------------------------------------


def test_bluez_not_available_yet(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, caplog: pytest.LogCaptureFixture
) -> None:
    """E.g. the service bluetooth starts after the box - the driver tries again and the box runs meanwhile."""
    bluez.unavailable = 3
    paired_headset(bluez)
    start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)  # dropped
    pubsub.publish(PLAYBACK, PlaybackStatus.PLAYING)  # the album starts meanwhile
    assert wait_for(lambda: states(pubsub) and last(pubsub).connected)
    assert len(bluez.called("connect")) == 4
    assert bluez.playback_status == "playing"  # the headset is told what plays - also by the new connection
    bluez.press("PlayPause")
    assert wait_for(lambda: pubsub.messages(BUTTON_PRESSED) == [Button.PAUSE])
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "Bluetooth - not available" in r.message]
    assert len(errors) == 1  # the same reason is reported once
    assert not last(pubsub).pairing


def test_an_error_nobody_expected_is_reported_and_tried_again(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, an exception of another type ended the thread of the driver for the rest of the run, and the health
    check still took the driver for running."""
    reported: list[str] = []
    monkeypatch.setattr("tinyjukebox.core.unexpected.report", lambda error, where: reported.append(where))
    bluez.connect_exception = KeyError("Adapter1")
    paired_headset(bluez)
    driver = start()
    assert wait_for(lambda: states(pubsub) and last(pubsub).connected)
    assert reported == ["bluetooth driver"]
    assert driver.healthy()


def test_stop(
    start: Start, pubsub: SyncPubSub, bluez: FakeBluez, mixers: list[FakeMixer], capsys: pytest.CaptureFixture[str]
) -> None:
    paired_headset(bluez)
    driver = start()
    assert wait_for(lambda: mixers and mixers[0].watching)
    driver.driver_ui_commands["state"]("")
    assert f"{HEADSET} 'WH-CH530' paired: True, connected: True, audio: True" in capsys.readouterr().out
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: bluez.discovering)
    driver.stop()  # also in the middle of a search
    assert bluez.closed
    assert not bluez.discovering
    assert not mixers[0].watching
    assert pubsub.subscribed_topics() == {}
    count = len(states(pubsub))
    bluez.set_device(bluez.device(OTHER, paired=True))
    assert len(states(pubsub)) == count
    driver.driver_ui_commands["state"]("")
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)  # nobody listens


def test_invalid_messages(start: Start, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture) -> None:
    start()
    pubsub.publish(BLUETOOTH_COMMAND, "pair")
    pubsub.publish(PLAYBACK, "playing")
    assert "cannot process 'pair'" in caplog.text
