"""VirtualHeadset: the headset of a box without hardware - same messages as the bluetooth driver."""

import logging
from collections.abc import Iterator

import pytest

from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.bluetooth.virtual import VIRTUAL_DEVICE, VirtualHeadset
from tinyjukebox.messages import (
    BLUETOOTH_COMMAND,
    BLUETOOTH_STATE,
    BUTTON_PRESSED,
    BluetoothAction,
    BluetoothCommand,
    BluetoothState,
    Button,
    PairingResult,
)

PAIR = BluetoothCommand(BluetoothAction.PAIR)
CONNECTED = BluetoothState(device=VIRTUAL_DEVICE, connected=True, battery=80, pairing_result=PairingResult.PAIRED,
                           virtual=True)  # fmt: skip


@pytest.fixture
def headset(pubsub: SyncPubSub, logger: logging.Logger) -> Iterator[VirtualHeadset]:
    headset = VirtualHeadset(pubsub, logger, search_time=0.05)
    headset.start()
    yield headset
    headset.stop()


@pytest.fixture
def paired(headset: VirtualHeadset, pubsub: SyncPubSub) -> VirtualHeadset:
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: headset.state.connected)
    return headset


def test_pairing(headset: VirtualHeadset, pubsub: SyncPubSub) -> None:
    assert pubsub.messages(BLUETOOTH_STATE) == [BluetoothState(virtual=True)]
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)  # searching already
    assert headset.state == BluetoothState(virtual=True, pairing=True)
    assert wait_for(lambda: headset.state == CONNECTED)
    assert len(pubsub.messages(BLUETOOTH_STATE)) == 3


def test_volume_switch_and_unpair(paired: VirtualHeadset, pubsub: SyncPubSub) -> None:
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 40))
    assert paired.state.volume == 40
    paired.switch(False)  # switched off: the connection is gone
    state = paired.state
    assert (state.connected, state.volume, state.battery) == (False, None, None)
    assert state.device == VIRTUAL_DEVICE
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 50))  # not connected
    assert paired.state.volume is None
    paired.switch(True)
    assert paired.state.connected
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.UNPAIR))
    assert paired.state == BluetoothState(virtual=True, pairing_result=PairingResult.PAIRED)
    paired.switch(True)  # not paired - nothing to connect
    assert not paired.state.connected


def test_buttons(paired: VirtualHeadset, pubsub: SyncPubSub) -> None:
    pubsub.publish(BLUETOOTH_COMMAND, BluetoothCommand(BluetoothAction.VOLUME, 40))
    for button in (Button.PLAY, Button.PAUSE, Button.NEXT, Button.PREV):
        paired.press(button)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.PLAY, Button.PAUSE, Button.NEXT, Button.PREV]
    paired.press(Button.VOLUME_UP)  # like absolute volume: the headset changes its volume itself
    assert paired.state.volume == 46
    paired.press(Button.VOLUME_DOWN)
    paired.press(Button.VOLUME_DOWN)
    assert paired.state.volume == 34
    assert len(pubsub.messages(BUTTON_PRESSED)) == 4
    paired.switch(False)
    paired.press(Button.NEXT)  # a headset that is switched off sends nothing
    assert len(pubsub.messages(BUTTON_PRESSED)) == 4


def test_console(headset: VirtualHeadset, pubsub: SyncPubSub, capsys: pytest.CaptureFixture[str]) -> None:
    switch, button = headset.ui_commands["virtual_headset"], headset.ui_commands["virtual_headset_button"]
    switch("on")
    assert "not paired" in capsys.readouterr().out
    button("next")
    assert "not connected" in capsys.readouterr().out
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    assert wait_for(lambda: headset.state.connected)
    button("next")
    assert "pressed next" in capsys.readouterr().out
    assert pubsub.messages(BUTTON_PRESSED) == [Button.NEXT]
    button("eject")
    switch("maybe")
    assert capsys.readouterr().out.count("expected") == 2
    switch("off")
    assert "switched off" in capsys.readouterr().out
    assert not headset.state.connected


def test_stop_ends_a_search(pubsub: SyncPubSub, logger: logging.Logger) -> None:
    headset = VirtualHeadset(pubsub, logger, search_time=30)
    headset.start()
    pubsub.publish(BLUETOOTH_COMMAND, PAIR)
    pubsub.publish(BLUETOOTH_COMMAND, "pair")  # not a command
    headset.stop()
    assert pubsub.subscribed_topics() == {}
