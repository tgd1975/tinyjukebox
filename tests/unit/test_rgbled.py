"""RGBLed publishes the patterns of the rgbled driver and remembers the state for the web page."""

import time

from tests.support import wait_for
from tinyjukebox.messages import LED, Color, LedPattern
from tinyjukebox.rgbled import RGBLed


def test_set_color(pubsub, logger):
    led = RGBLed(logger, pubsub)
    led.set_color(Color.MAGENTA)
    assert pubsub.messages(LED) == [LedPattern.static(Color.MAGENTA)]
    assert led.get_state() == {"color": "MAGENTA"}


def test_alternate_and_blink(pubsub, logger):
    led = RGBLed(logger, pubsub)
    led.set_alternate_colors(Color.YELLOW, Color.RED, 1)
    assert led.get_state() == {"color_a": "YELLOW", "color_b": "RED", "delay": 1}
    led.set_blink_color(Color.BLUE, 0.5)
    assert pubsub.messages(LED) == [
        LedPattern.blink(Color.YELLOW, Color.RED, 1, 1),
        LedPattern.blink(Color.BLUE, Color.BLACK, 0.5, 0.5),
    ]
    assert led.get_state() == {"color_a": "BLUE", "color_b": "BLACK", "delay": 0.5}


def test_overlay_hides_the_patterns_until_it_is_cleared(pubsub, logger):
    led = RGBLed(logger, pubsub)
    led.set_color(Color.BLUE)
    led.show_overlay(Color.BLUE, Color.BLACK, 0.2)  # e.g. searching a headset
    assert led.get_state() == {"color_a": "BLUE", "color_b": "BLACK", "delay": 0.2}
    led.set_color(Color.GREEN)  # the state machines go on - not shown now
    led.set_alternate_colors(Color.WHITE, Color.BLUE, 1)
    assert led.get_state() == {"color_a": "BLUE", "color_b": "BLACK", "delay": 0.2}
    led.clear_overlay()
    led.clear_overlay()  # no overlay: nothing happens
    assert led.get_state() == {"color_a": "WHITE", "color_b": "BLUE", "delay": 1}
    assert pubsub.messages(LED) == [
        LedPattern.static(Color.BLUE),
        LedPattern.blink(Color.BLUE, Color.BLACK, 0.2, 0.2),
        LedPattern.blink(Color.WHITE, Color.BLUE, 1, 1),  # the last pattern of the state machines
    ]


def test_overlay_expires(pubsub, logger):
    led = RGBLed(logger, pubsub)
    led.set_color(Color.GREEN)
    led.show_overlay(Color.WHITE, duration=0.05)
    assert led.get_state() == {"color": "WHITE"}
    assert wait_for(lambda: led.get_state() == {"color": "GREEN"})
    assert pubsub.messages(LED) == [LedPattern.static(c) for c in (Color.GREEN, Color.WHITE, Color.GREEN)]


def test_new_overlay_replaces_the_old_one(pubsub, logger):
    led = RGBLed(logger, pubsub)
    led.show_overlay(Color.BLUE, Color.BLACK, 0.2, duration=0.05)  # search ...
    led.show_overlay(Color.RED, Color.BLACK, 0.2, duration=30)  # ... failed
    time.sleep(0.15)  # the timer of the first overlay does not end the second one
    assert led.get_state() == {"color_a": "RED", "color_b": "BLACK", "delay": 0.2}
    led.clear_overlay()
    assert led.get_state() is None  # no pattern has been set yet
    assert len(pubsub.messages(LED)) == 2
