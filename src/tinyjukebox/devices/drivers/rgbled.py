"""RGB LED with three GPIO outputs: static colors and blinking between two colors."""

import logging
import threading
from collections.abc import Mapping
from typing import Any

from RPi import GPIO

from tinyjukebox.core.pubsub import PubSubClient
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.core.ui.tools import parse
from tinyjukebox.devices.baseclasses.adriver import ADriver
from tinyjukebox.devices.baseclasses.gpiolevel import closed_level
from tinyjukebox.devices.schema.rgbled import COLOR_NAMES, get_schema
from tinyjukebox.messages import LED, RGB, Color, LedPattern

__all__ = ["RGBLed"]


class RGBLed(ADriver):
    """RGB LED on three GPIO outputs, shows the :class:`~tinyjukebox.messages.LedPattern` of the messages on
    :data:`~tinyjukebox.messages.LED`: one color or alternating colors.

    Blinking starts with ``color_a`` and runs in a separate thread until the next command.

    Example::

        driver:
            type: rgbled
            pin-red: 21
            pin-green: 22
            pin-blue: 23
            initial-color: GREEN
            physical-closed-red: high     # level that switches the color on (low/high)
            physical-closed-green: high
            physical-closed-blue: high
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        pubsub_client: PubSubClient,
        logger: logging.Logger,
    ) -> None:
        super().__init__(config, pubsub_client, logger)
        self._pins = tuple(int(self._config[f"pin-{color}"]) for color in ("red", "green", "blue"))
        self._closed_levels = tuple(
            closed_level(self._config[f"physical-closed-{color}"], f"physical-closed-{color}")
            for color in ("red", "green", "blue")
        )
        self._initial_color = Color[self._config["initial-color"]]

        self._pattern_lock = threading.Lock()
        self._blink_stop = threading.Event()
        self._blink_thread: LoggerThread | None = None
        self._pattern = LedPattern.static(self._initial_color)

        self._add_ui_command("gpio_color", self._cmd_gpio_color)
        self._add_ui_command("gpio_rgb", self._cmd_gpio_rgb)
        self._add_ui_command("gpio_blink", self._cmd_gpio_blink)
        self._add_ui_command("gpio_state", self._cmd_gpio_state)

    @property
    def pattern(self) -> LedPattern:
        """The pattern that is currently shown."""
        return self._pattern

    # --- output -------------------------------------------------------------------------------------------------

    def _set_pins(self, rgb: RGB) -> None:
        for pin, closed, on in zip(self._pins, self._closed_levels, rgb, strict=True):
            GPIO.output(pin, closed if on else 1 - closed)

    def _blink(self, pattern: LedPattern, stop: threading.Event) -> None:
        index = 0
        while True:
            self._set_pins(pattern.colors[index])
            if stop.wait(pattern.delays[index]):
                return
            index = (index + 1) % len(pattern.colors)

    def _stop_blinking(self) -> None:
        if self._blink_thread is not None:
            self._blink_stop.set()
            self._blink_thread.join()
            self._blink_thread = None

    def show(self, pattern: LedPattern) -> None:
        """Show ``pattern`` - replaces the current pattern and returns immediately."""
        self._logger.info("RGBLed.show - %s", pattern)
        with self._pattern_lock:
            self._stop_blinking()
            self._pattern = pattern
            if len(pattern.colors) == 1:
                self._set_pins(pattern.colors[0])
            else:
                self._blink_stop = threading.Event()
                self._blink_thread = LoggerThread(
                    target=self._blink,
                    args=(pattern, self._blink_stop),
                    name=f"rgbled.{self._name}.blink",
                    logger=self._logger,
                    daemon=True,
                )
                self._blink_thread.start()

    def _message_handler(self, message: object) -> None:
        """Handler for :data:`~tinyjukebox.messages.LED`."""
        self._logger.info("received message %r on topic '%s'.", message, LED)
        if isinstance(message, LedPattern):
            self.show(message)
        else:
            self._logger.warning("RGBLed._message_handler - expected a LedPattern, received %r.", message)

    # --- console commands ---------------------------------------------------------------------------------------

    @staticmethod
    def _parse_color(name: str) -> Color | None:
        return Color.__members__.get(name.upper())

    def _cmd_gpio_color(self, args: str) -> None:
        """gpio_color - sets a color (BLACK, WHITE, RED, GREEN, BLUE, YELLOW, AQUA, MAGENTA): GPIO_COLOR color"""
        arguments = parse(args)
        color = self._parse_color(arguments[0]) if len(arguments) == 1 else None
        if color is None:
            print(f"Wrong arguments: {arguments}. expected 'GPIO_COLOR [{'|'.join(COLOR_NAMES)}]'.")
            return
        print(f"Setting color to: {color.name}.")
        self.show(LedPattern.static(color))

    def _cmd_gpio_rgb(self, args: str) -> None:
        """gpio_rgb - sets the rgb-led to the boolean values: GPIO_RGB Red Green Blue"""
        arguments = [argument.lower() for argument in parse(args)]
        if len(arguments) != 3 or any(argument not in ("true", "false") for argument in arguments):
            print(f"Wrong arguments: {arguments}. expected 'GPIO_RGB Red Green Blue' with values True/False.")
            return
        red, green, blue = (argument == "true" for argument in arguments)
        print(f"Setting rgb to: {red} {green} {blue}.")
        self.show(LedPattern.static((red, green, blue)))

    def _cmd_gpio_blink(self, args: str) -> None:
        """gpio_blink - alternates between two colors: GPIO_BLINK color_a color_b delay [delay_b]"""
        arguments = parse(args)
        expected = f"GPIO_BLINK [{'|'.join(COLOR_NAMES)}] [{'|'.join(COLOR_NAMES)}] delay [delay_b]"
        if len(arguments) not in (3, 4):
            print(f"Wrong arguments: {arguments}. expected '{expected}'.")
            return
        color_a, color_b = self._parse_color(arguments[0]), self._parse_color(arguments[1])
        try:
            delays = [float(argument) for argument in arguments[2:]]
        except ValueError:
            delays = []
        if color_a is None or color_b is None or not delays or min(delays) <= 0:
            print(f"Wrong arguments: {arguments}. expected '{expected}' with delays > 0.")
            return
        pattern = LedPattern.blink(color_a, color_b, delays[0], delays[-1])
        print(f"Setting blink to {pattern}.")
        self.show(pattern)

    def _cmd_gpio_state(self, args: str) -> None:
        """gpio_state - reads the state of the gpios: GPIO_STATE"""
        states = [
            f"{name} {pin} is {'on' if GPIO.input(pin) == closed else 'off'}"
            for name, pin, closed in zip(("red", "green", "blue"), self._pins, self._closed_levels, strict=True)
        ]
        print(f"[{self._name}] gpios: {', '.join(states)}.")

    # --- life cycle ---------------------------------------------------------------------------------------------

    def _driver_start(self) -> None:
        GPIO.setmode(GPIO.BCM)
        for pin in self._pins:
            GPIO.setup(pin, GPIO.OUT)
        self.show(LedPattern.static(self._initial_color))
        self._pubsub_client.subscribe(LED, self._message_handler)

    def _driver_stop(self) -> None:
        self._pubsub_client.unsubscribe(LED, self._message_handler)
        with self._pattern_lock:
            self._stop_blinking()
        for pin in self._pins:
            GPIO.cleanup(pin)

    @classmethod
    def _get_schema(cls) -> dict[str, Any]:
        return get_schema()
