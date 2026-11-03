"""Input: edges → pressed/released, held after hold_timeout - payload: the configured button."""

import logging
import threading
import time
from collections.abc import Iterator

import pytest

from tests.devices.fakes import FakeGPIO
from tests.devices.support import INPUT, driver_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.drivers.input import Input
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, Button

PIN = INPUT["pin"]


@pytest.fixture
def button(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> Iterator[Input]:
    driver = Input(driver_config(INPUT, log_file), pubsub, logger)
    driver.start()
    yield driver
    if not driver.is_stopped:
        driver.stop()


def test_setup(button: Input, gpio: FakeGPIO) -> None:
    assert gpio.mode == gpio.BCM
    assert gpio.pins[PIN].direction == gpio.IN
    assert gpio.pins[PIN].pull == gpio.PUD_UP
    assert gpio.events[PIN][1] == 50  # bounce time


def test_press_and_release(button: Input, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    gpio.set_input(PIN, 0)
    gpio.set_input(PIN, 1)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.VOLUME_UP]
    assert pubsub.messages(BUTTON_RELEASED) == [Button.VOLUME_UP]
    time.sleep(0.1)
    assert pubsub.messages(BUTTON_HELD) == []  # released before hold_timeout


def test_hold(button: Input, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    gpio.set_input(PIN, 0)
    assert wait_for(lambda: pubsub.messages(BUTTON_HELD) == [Button.VOLUME_UP])
    time.sleep(0.1)
    assert pubsub.messages(BUTTON_HELD) == [Button.VOLUME_UP]  # only once
    gpio.set_input(PIN, 1)


def test_hold_repeat(pubsub: SyncPubSub, logger: logging.Logger, log_file: str, gpio: FakeGPIO) -> None:
    driver = Input(driver_config({**INPUT, "hold_repeat": True}, log_file), pubsub, logger)
    driver.start()
    gpio.set_input(PIN, 0)
    assert wait_for(lambda: len(pubsub.messages(BUTTON_HELD)) >= 3)  # again and again while pressed
    gpio.set_input(PIN, 1)
    time.sleep(0.02)  # a hold event that has just been triggered
    count = len(pubsub.messages(BUTTON_HELD))
    time.sleep(0.15)
    assert len(pubsub.messages(BUTTON_HELD)) == count  # the release ends it
    driver.stop()
    assert set(pubsub.messages(BUTTON_HELD)) == {Button.VOLUME_UP}


@pytest.mark.parametrize("repeat", [False, True])
def test_no_hold_event_after_a_lost_release(
    pubsub: SyncPubSub, logger: logging.Logger, log_file: str, gpio: FakeGPIO, repeat: bool
) -> None:
    """A release within the bounce time of the press triggers no edge - a repeating button must still stop."""
    driver = Input(driver_config({**INPUT, "hold_repeat": repeat}, log_file), pubsub, logger)
    driver.start()
    gpio.set_input(PIN, 0)
    gpio.pins[PIN].level = 1  # released without edge callback
    assert wait_for(lambda: pubsub.messages(BUTTON_RELEASED) == [Button.VOLUME_UP])
    time.sleep(0.15)
    driver.stop()
    assert pubsub.messages(BUTTON_HELD) == []
    assert pubsub.messages(BUTTON_RELEASED) == [Button.VOLUME_UP]


def test_bouncing_press_restarts_hold_timer(button: Input, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    button._callback_pin(PIN)
    gpio.set_input(PIN, 0)
    assert wait_for(lambda: len(pubsub.messages(BUTTON_HELD)) == 1)
    time.sleep(0.1)
    assert len(pubsub.messages(BUTTON_HELD)) == 1


def test_stop_cancels_hold_and_cleans_up(button: Input, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    gpio.set_input(PIN, 0)
    button.stop()
    time.sleep(0.1)
    assert pubsub.messages(BUTTON_HELD) == []
    assert PIN not in gpio.events
    assert gpio.cleaned == [PIN]


def test_an_edge_while_the_driver_stops_starts_no_hold_timer(
    button: Input, gpio: FakeGPIO, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The edge callback runs in a thread of the GPIO library: the driver may stop between reading the pin and
    starting the hold timer - a timer would read the released pin afterwards."""
    read = gpio.input

    def stop_while_reading(pin: int) -> int:
        level = read(pin)
        monkeypatch.setattr(gpio, "input", read)
        button.stop()
        return level

    gpio.pins[PIN].level = 0
    monkeypatch.setattr(gpio, "input", stop_while_reading)
    button._callback_pin(PIN)
    time.sleep(0.1)  # longer than hold_timeout
    assert pubsub.messages(BUTTON_PRESSED) == [Button.VOLUME_UP]
    assert pubsub.messages(BUTTON_HELD) == []
    assert not [thread for thread in threading.enumerate() if thread.name.endswith(".hold")]


def test_stop_waits_for_a_running_hold_event(
    button: Input, gpio: FakeGPIO, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = gpio.input
    reading, go_on = threading.Event(), threading.Event()

    def slow_read(pin: int) -> int:
        if threading.current_thread().name.endswith(".hold"):
            reading.set()
            go_on.wait(5)
        return read(pin)

    monkeypatch.setattr(gpio, "input", slow_read)
    gpio.set_input(PIN, 0)
    assert reading.wait(5)  # the hold event reads the pin
    stopping = threading.Thread(target=button.stop)
    stopping.start()
    time.sleep(0.05)
    assert gpio.cleaned == []  # the pin is not released below the hold event
    go_on.set()
    stopping.join(5)
    assert gpio.cleaned == [PIN]
    assert pubsub.messages(BUTTON_HELD) == [Button.VOLUME_UP]  # published before the driver stopped


def test_restart(button: Input, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    button.stop()
    button.start()
    gpio.set_input(PIN, 0)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.VOLUME_UP]


def test_gpio_state_command(button: Input, gpio: FakeGPIO, capsys: pytest.CaptureFixture[str]) -> None:
    button.driver_ui_commands["gpio_state"]("")
    gpio.pins[PIN].level = 0
    button.driver_ui_commands["gpio_state"]("")
    assert capsys.readouterr().out.splitlines() == ["[button] gpio 21 is open", "[button] gpio 21 is closed"]


def test_configured_button(pubsub: SyncPubSub, logger: logging.Logger, log_file: str, gpio: FakeGPIO) -> None:
    driver = Input(driver_config({**INPUT, "button": "prev"}, log_file), pubsub, logger)
    driver.start()
    gpio.set_input(PIN, 0)
    driver.stop()
    assert pubsub.messages(BUTTON_PRESSED) == [Button.PREV]


def test_state_tells_the_pin_and_the_last_event(button: Input, gpio: FakeGPIO) -> None:
    """The technical view of the web page: the level of the pin right now, next to what was published last."""
    gpio.set_input(PIN, 1)
    state = button.state()
    assert state == {
        "name": "button", "type": "input", "running": True, "button": "volume-up", "pin": PIN, "pressed": False,
        "hold_timeout": 0.05, "hold_repeat": False, "last_event": None, "last_event_at": None, "presses": 0,
    }  # fmt: skip
    before = time.time()
    gpio.set_input(PIN, 0)
    state = button.state()
    assert (state["pressed"], state["last_event"], state["presses"]) == (True, "pressed", 1)
    assert before <= state["last_event_at"] <= time.time()
    assert wait_for(lambda: button.state()["last_event"] == "held")
    gpio.set_input(PIN, 1)
    state = button.state()
    assert (state["pressed"], state["last_event"], state["presses"]) == (False, "released", 1)


def test_state_of_a_pin_whose_edges_get_lost(button: Input, gpio: FakeGPIO) -> None:
    gpio.pins[PIN].level = 0  # pressed, but no edge callback
    state = button.state()
    assert (state["pressed"], state["last_event"], state["presses"]) == (True, None, 0)


def test_state_of_a_stopped_driver(button: Input, gpio: FakeGPIO, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(pin: int) -> int:
        raise RuntimeError("the gpio channel has not been set up")

    monkeypatch.setattr(gpio, "input", unreadable)
    assert button.state()["pressed"] is None  # a pin that cannot be read does not break the view
    button.stop()
    state = button.state()
    assert (state["running"], state["pressed"]) == (False, None)
