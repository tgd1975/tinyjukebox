"""DriverManager and DriverFactory: creation, shared client, life cycle, console commands."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from tests.devices.fakes import FakeGPIO
from tests.devices.support import INPUT, RGBLED
from tests.devices.support import RFID as RFID_CONFIG
from tests.support import SyncPubSub
from tinyjukebox.devices.drivermanager.driverfactory import DriverFactory
from tinyjukebox.devices.drivermanager.drivermanager import DriverManager
from tinyjukebox.devices.drivers import get_driver_class
from tinyjukebox.devices.drivers.input import Input
from tinyjukebox.devices.drivers.rfid import RFID
from tinyjukebox.devices.drivers.rgbled import RGBLed
from tinyjukebox.messages import BUTTON_PRESSED, LED, Button, Color, LedPattern


def manager_config(log_file: str, *drivers: dict[str, Any]) -> dict[str, Any]:
    return {
        "logger": {"log-level": "DEBUG", "log-file": log_file},
        "drivers": list(drivers),
    }


def active(driver: dict[str, Any], is_active: bool = True, **changes: Any) -> dict[str, Any]:
    return {**driver, **changes, "active": is_active}


BOX = [active(RFID_CONFIG), active(INPUT), active(RGBLED), active(INPUT, False, name="unused")]


@pytest.fixture
def manager(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> Iterator[DriverManager]:
    instance = DriverManager(manager_config(log_file, *BOX), pubsub, logger)
    yield instance
    if not instance.is_stopped:
        instance.stop()


def test_creates_active_drivers(manager: DriverManager, pubsub: SyncPubSub) -> None:
    drivers = manager.drivers
    assert list(drivers) == ["rfid", "button", "led"]
    assert [type(d) for d in drivers.values()] == [RFID, Input, RGBLed]
    assert all(d._pubsub_client is pubsub for d in drivers.values())
    assert all(not d._manage_ui for d in drivers.values())
    assert drivers["button"].config["name"] == "button"  # each driver gets its own section


def test_start_and_stop_all(manager: DriverManager, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    manager.start()
    assert all(not d.is_stopped for d in manager.drivers.values())
    gpio.set_input(INPUT["pin"], 0)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.VOLUME_UP]
    pubsub.publish(LED, LedPattern.static(Color.BLACK))
    manager.stop()
    assert all(d.is_stopped for d in manager.drivers.values())
    assert pubsub.subscribed_topics() == {}


def test_driver_console_commands_are_prefixed(manager: DriverManager) -> None:
    commands = set(manager._ui_commands)
    assert {"button_gpio_state", "led_gpio_blink"} <= commands
    assert {"list_drivers", "start_driver", "stop_driver", "restart_driver", "reload_driver"} <= commands
    assert "button_debug_threads" not in commands


def test_unique_names() -> None:
    drivers = {"a": None, "a_0": None}
    assert DriverFactory.get_unique_name(drivers, "My Driver") == "mydriver"
    assert DriverFactory.get_unique_name(drivers, "A") == "a_1"


def test_duplicate_names(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> None:
    config = manager_config(log_file, active(INPUT), active(INPUT, pin=5))
    assert list(DriverManager(config, pubsub, logger).drivers) == ["button", "button_0"]


def test_unknown_driver_type(logger: logging.Logger) -> None:
    with pytest.raises(ValueError, match="laser"):
        get_driver_class("laser")
    factory = DriverFactory(SyncPubSub(logger), logger)
    with pytest.raises(ValueError, match="laser"):
        factory.create_driver({"type": "laser", "name": "x", "active": True})


def test_restart_and_reload(manager: DriverManager, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    manager.start()
    manager.restart_driver("led")
    assert not manager.drivers["led"].is_stopped
    old = manager.drivers["button"]
    manager.reload_driver("button")
    new = manager.drivers["button"]
    assert new is not old
    assert old.is_stopped
    assert not new.is_stopped
    gpio.set_input(INPUT["pin"], 0)
    assert pubsub.messages(BUTTON_PRESSED) == [Button.VOLUME_UP]  # exactly once - old driver is gone


def test_reload_stopped_driver(manager: DriverManager) -> None:
    manager.reload_driver("led")
    assert not manager.drivers["led"].is_stopped


# --- console --------------------------------------------------------------------------------------------------------


@pytest.fixture
def answer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    answers: list[str] = []
    monkeypatch.setattr("tinyjukebox.devices.drivermanager.drivermanager.get_yes_no", lambda: answers.pop(0))
    return answers


def test_console_start_stop(manager: DriverManager, answer: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    commands = manager._ui_commands
    led = manager.drivers["led"]
    commands["stop_driver"]("led")  # not running
    answer.append("n")
    commands["start_driver"]("led")
    assert led.is_stopped
    answer.append("y")
    commands["start_driver"]("led")
    assert not led.is_stopped
    commands["start_driver"]("led")  # already running
    answer.append("y")
    commands["restart_driver"]("led")
    assert not led.is_stopped
    answer.append("y")
    commands["stop_driver"]("led")
    assert led.is_stopped
    out = capsys.readouterr().out
    assert "[led] is not running" in out
    assert "[led] is already running" in out
    assert "restarting [led]: [led] type:RGBLed is_stopped:False" in out


def test_console_reload(manager: DriverManager, answer: list[str]) -> None:
    old = manager.drivers["led"]
    answer.append("y")
    manager._ui_commands["reload_driver"]("led")
    assert manager.drivers["led"] is not old


def test_driver_commands_reach_the_reloaded_instance(manager: DriverManager, answer: list[str]) -> None:
    """The command line of the box keeps the commands it got at the start - they must not stay with the old one."""
    manager.start()
    color = manager._ui_commands["led_gpio_color"]
    assert str(color.__doc__).startswith("gpio_color - sets a color")  # the help text
    answer.append("y")
    manager._ui_commands["reload_driver"]("led")
    color("BLUE")
    led = manager.drivers["led"]
    assert isinstance(led, RGBLed)
    assert led.pattern == LedPattern.static(Color.BLUE)


def test_console_wrong_ids(manager: DriverManager, capsys: pytest.CaptureFixture[str]) -> None:
    manager._ui_commands["stop_driver"]("")
    manager._ui_commands["stop_driver"]("laser")
    assert capsys.readouterr().out.splitlines() == ["missing mandatory parameter id (see 'list_drivers').",
                                                    "unknown id 'laser'"]  # fmt: skip


def test_console_list_drivers(
    manager: DriverManager, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("tinyjukebox.devices.drivermanager.drivermanager.more", print)
    manager._ui_commands["list_drivers"]("")
    assert "button: [button] type:Input is_stopped:True" in capsys.readouterr().out


def test_failed_start_stops_the_started_drivers(
    manager: DriverManager, pubsub: SyncPubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise OSError("gpio busy")

    monkeypatch.setattr(manager.drivers["led"], "_driver_start", broken)
    with pytest.raises(OSError, match="gpio busy"):
        manager.start()
    assert manager.is_stopped
    assert all(d.is_stopped for d in manager.drivers.values())
    assert pubsub.subscribed_topics() == {}


def test_states_of_all_drivers(manager: DriverManager) -> None:
    states = manager.states()
    assert [(state["id"], state["type"], state["running"]) for state in states] == [
        ("rfid", "rfid", False),
        ("button", "input", False),
        ("led", "rgbled", False),
    ]
    manager.start()
    assert all(state["running"] for state in manager.states())
