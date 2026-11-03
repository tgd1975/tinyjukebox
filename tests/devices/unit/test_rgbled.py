"""RGBLed: LedPattern messages → pins, blinking in a separate thread."""

import logging
import threading
import time
from collections.abc import Iterator

import pytest

from tests.devices.fakes import FakeGPIO
from tests.devices.support import RGBLED, driver_config
from tests.support import SyncPubSub, wait_for
from tinyjukebox.devices.drivers.rgbled import RGBLed
from tinyjukebox.devices.schema.rgbled import COLOR_NAMES
from tinyjukebox.messages import LED, Color, LedPattern

RED, GREEN, BLUE = RGBLED["pin-red"], RGBLED["pin-green"], RGBLED["pin-blue"]


@pytest.fixture
def led(pubsub: SyncPubSub, logger: logging.Logger, log_file: str) -> Iterator[RGBLed]:
    driver = RGBLed(driver_config(RGBLED, log_file), pubsub, logger)
    driver.start()
    yield driver
    if not driver.is_stopped:
        driver.stop()


def shown(gpio: FakeGPIO) -> tuple[bool, bool, bool]:
    """Logical colors (red is low active in the test config)."""
    return gpio.level(RED) == 0, gpio.level(GREEN) == 1, gpio.level(BLUE) == 1


def blink_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("rgbled.")]


def test_colors_match_schema_and_tinyjukebox() -> None:
    assert [c.name for c in Color] == COLOR_NAMES


def test_initial_color(led: RGBLed, gpio: FakeGPIO) -> None:
    assert all(gpio.pins[pin].direction == gpio.OUT for pin in (RED, GREEN, BLUE))
    assert shown(gpio) == Color.RED.value


@pytest.mark.parametrize(
    ("pattern", "rgb"),
    [
        (LedPattern.static(Color.MAGENTA), (True, False, True)),
        (LedPattern.static(Color.BLACK), (False, False, False)),
        (LedPattern.static((False, True, True)), (False, True, True)),
    ],
)
def test_static_patterns(led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub, pattern: LedPattern, rgb: tuple) -> None:
    pubsub.publish(LED, pattern)
    assert shown(gpio) == rgb
    assert led.pattern == pattern


def test_blink_symmetric(led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    pubsub.publish(LED, LedPattern.blink(Color.BLUE, Color.YELLOW, 0.02, 0.02))
    assert led.pattern == LedPattern.blink(Color.BLUE, Color.YELLOW, 0.02, 0.02)
    seen: set[tuple[bool, bool, bool]] = set()

    def both_colors_seen() -> bool:
        seen.add(shown(gpio))
        return seen >= {Color.BLUE.value, Color.YELLOW.value}

    assert wait_for(both_colors_seen)
    assert len(blink_threads()) == 1


def test_blink_asymmetric_starts_with_color_a(led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    pubsub.publish(LED, LedPattern.blink(Color.GREEN, Color.BLACK, 5, 0.01))
    assert wait_for(lambda: shown(gpio) == Color.GREEN.value)
    time.sleep(0.05)
    assert shown(gpio) == Color.GREEN.value  # still within delay_a


def test_new_command_stops_blinking(led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    pubsub.publish(LED, LedPattern.blink(Color.BLUE, Color.RED, 0.01, 0.01))
    pubsub.publish(LED, LedPattern.blink(Color.WHITE, Color.RED, 0.01, 0.01))
    assert len(blink_threads()) == 1
    pubsub.publish(LED, LedPattern.static(Color.AQUA))
    assert blink_threads() == []
    time.sleep(0.03)
    assert shown(gpio) == Color.AQUA.value


def test_stop_ends_blinking(led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub) -> None:
    pubsub.publish(LED, LedPattern.blink(Color.BLUE, Color.RED, 0.01, 0.01))
    led.stop()
    assert blink_threads() == []
    assert not pubsub.subscribed(LED)
    assert sorted(p for p in gpio.cleaned if p is not None) == sorted([RED, GREEN, BLUE])


def test_a_pattern_dispatched_while_the_driver_stops_is_not_shown(led: RGBLed, gpio: FakeGPIO) -> None:
    """The message may already be on its way to the handler when the driver unsubscribes - its pins are released
    afterwards: a blink thread would write to them."""
    led.stop()
    led._message_handler(LedPattern.blink(Color.BLUE, Color.RED, 0.01, 0.01))
    time.sleep(0.05)
    assert blink_threads() == []
    assert led.pattern == LedPattern.static(Color.RED)  # the initial color: the last pattern before the stop
    led.start()
    led._message_handler(LedPattern.static(Color.AQUA))  # shown again after a new start
    assert shown(gpio) == Color.AQUA.value


@pytest.mark.parametrize("payload", [None, "RED", {"color": "RED"}, Color.RED])
def test_other_payloads_are_ignored(
    led: RGBLed, gpio: FakeGPIO, pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture, payload: object
) -> None:
    pubsub.publish(LED, payload)
    assert shown(gpio) == Color.RED.value
    assert "expected a LedPattern" in caplog.text


@pytest.mark.parametrize(
    ("colors", "delays"),
    [((), ()), (((True, True, True), (False, False, False)), (1,)), ((Color.RED.value,) * 2, (1, 0))],
)
def test_invalid_patterns_are_rejected(colors: tuple, delays: tuple) -> None:
    with pytest.raises(ValueError, match="color"):
        LedPattern(colors, delays)


def test_commands(led: RGBLed, gpio: FakeGPIO, capsys: pytest.CaptureFixture[str]) -> None:
    commands = led.driver_ui_commands
    assert set(commands) == {"gpio_color", "gpio_rgb", "gpio_blink", "gpio_state"}
    commands["gpio_color"]("magenta")
    assert shown(gpio) == Color.MAGENTA.value
    commands["gpio_rgb"]("true false False")
    assert shown(gpio) == (True, False, False)
    commands["gpio_state"]("")
    assert "red 22 is on, green 27 is off, blue 17 is off" in capsys.readouterr().out
    commands["gpio_blink"]("green black 0.01")
    assert led.pattern == LedPattern.blink(Color.GREEN, Color.BLACK, 0.01, 0.01)
    commands["gpio_blink"]("green black 1 2")
    assert led.pattern == LedPattern.blink(Color.GREEN, Color.BLACK, 1, 2)
    capsys.readouterr()

    for name, args in [
        ("gpio_color", "pink"),
        ("gpio_color", ""),
        ("gpio_rgb", "true false"),
        ("gpio_rgb", "yes no maybe"),
        ("gpio_blink", "red blue"),
        ("gpio_blink", "red pink 1"),
        ("gpio_blink", "red blue fast"),
        ("gpio_blink", "red blue 0"),
    ]:
        commands[name](args)
    assert capsys.readouterr().out.count("Wrong arguments") == 8
    assert led.pattern == LedPattern.blink(Color.GREEN, Color.BLACK, 1, 2)


def test_state_tells_the_pattern_and_the_lit_lights(led: RGBLed, pubsub: SyncPubSub) -> None:
    """The technical view of the web page: the pattern, and what the three pins say right now."""
    assert led.state() == {
        "name": "led", "type": "rgbled", "running": True, "pins": {"red": RED, "green": GREEN, "blue": BLUE},
        "colors": ["RED"], "delays": [], "lit": {"red": True, "green": False, "blue": False},
    }  # fmt: skip
    pubsub.publish(LED, LedPattern.static(Color.AQUA))
    assert led.state()["lit"] == {"red": False, "green": True, "blue": True}
    pubsub.publish(LED, LedPattern.blink(Color.GREEN, Color.BLACK, 5, 0.5))
    state = led.state()
    assert (state["colors"], state["delays"]) == (["GREEN", "BLACK"], [5, 0.5])
    led.stop()
    state = led.state()
    assert (state["running"], state["lit"]) == (False, None)
