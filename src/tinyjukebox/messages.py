"""Topics and payloads of the messages within the box: drivers, state machines and web page.

The messages are delivered within the process (:class:`~tinyjukebox.core.pubsub.LocalQueue`). Payloads are passed on
unchanged - they are Python objects and must therefore be immutable. The module has no hardware dependencies; the
drivers and the state machines import it alike.
"""

import datetime
from dataclasses import dataclass, field
from enum import Enum, StrEnum

__all__ = [
    "BLUETOOTH_COMMAND",
    "BLUETOOTH_STATE",
    "BUTTON_HELD",
    "BUTTON_PRESSED",
    "BUTTON_RELEASED",
    "JOURNAL",
    "LED",
    "PLAYBACK",
    "RFID_POLL",
    "RFID_STATE",
    "RGB",
    "BluetoothAction",
    "BluetoothCommand",
    "BluetoothDevice",
    "BluetoothState",
    "Button",
    "Color",
    "JournalEvent",
    "LedPattern",
    "PairingResult",
    "PlaybackStatus",
    "RfidState",
]


class Button(StrEnum):
    """The buttons of the box and of the bluetooth headset - value: name in the config (``drivers/button``) and in
    the rest api. The box has no play/pause buttons; the headset sends them (and next/prev, volume-up/-down)."""

    VOLUME_UP = "volume-up"
    VOLUME_DOWN = "volume-down"
    NEXT = "next"
    PREV = "prev"
    PLAY = "play"
    PAUSE = "pause"


RGB = tuple[bool, bool, bool]


class Color(Enum):
    """Named colors of the RGB LED - value: (red, green, blue)."""

    BLACK = (False, False, False)
    WHITE = (True, True, True)
    RED = (True, False, False)
    GREEN = (False, True, False)
    BLUE = (False, False, True)
    YELLOW = (True, True, False)
    AQUA = (False, True, True)
    MAGENTA = (True, False, True)


@dataclass(frozen=True)
class LedPattern:
    """What the LED shows: one color, or colors that alternate with the given delays (seconds each)."""

    colors: tuple[RGB, ...]
    delays: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.colors:
            raise ValueError("a pattern needs at least one color.")
        if len(self.colors) > 1 and (len(self.delays) != len(self.colors) or min(self.delays) <= 0):
            raise ValueError(f"alternating colors need one delay > 0 per color - got {self.delays}.")

    @classmethod
    def static(cls, color: Color | RGB) -> "LedPattern":
        return cls((color.value if isinstance(color, Color) else color,))

    @classmethod
    def blink(cls, color_a: Color, color_b: Color, delay_a: float, delay_b: float) -> "LedPattern":
        return cls((color_a.value, color_b.value), (delay_a, delay_b))


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(frozen=True)
class RfidState:
    """Result of a read of the RFID reader: ``uid`` of the tag or ``None`` if no tag is present."""

    uid: str | None
    error: str | None = None  # why the reader does not work (rfid-reader cannot be started, has ended or is stuck)
    timestamp: datetime.datetime = field(default_factory=_now, compare=False)

    @property
    def present(self) -> bool:
        return self.uid is not None


RFID_STATE = "rfid/state"
""":class:`RfidState` - rfid driver (and the virtual tags of the web page) → STMOperation"""

RFID_POLL = "rfid/poll"
"""``None`` - STMOperation → rfid driver: read the reader now and publish the state in any case"""

BUTTON_PRESSED = "button/pressed"
""":class:`Button` - input drivers, bluetooth driver (headset) and the buttons of the web page → STMVolume,
STMPlayAlbum, PlayRadio"""

BUTTON_HELD = "button/held"
""":class:`Button` - input drivers, after ``hold_timeout`` seconds → Headset (next + prev held: pairing)"""

BUTTON_RELEASED = "button/released"
""":class:`Button` - input drivers → Headset"""

LED = "led"
""":class:`LedPattern` - RGBLed → rgbled driver"""

PLAYBACK = "playback"
""":class:`PlaybackStatus` - album and radio playback → bluetooth driver (reports it to the headset via AVRCP)"""


JOURNAL = "journal"
""":class:`JournalEvent` - state machines → the journal of the box (:mod:`tinyjukebox.sync`), which the library turns
into the play statistics"""


@dataclass(frozen=True)
class JournalEvent:
    """Something worth noting about what the box played: a tag was placed (``tag``), a track was played
    (``track``), the file of a track is damaged (``broken``), a button changed the playback (``control``),
    the playback ended (``end``)."""

    event: str
    fields: tuple[tuple[str, object], ...] = ()

    @classmethod
    def of(cls, event: str, **fields: object) -> "JournalEvent":
        return cls(event, tuple(fields.items()))


class PlaybackStatus(StrEnum):
    """What the box plays - the headset sends play or pause accordingly."""

    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True)
class BluetoothDevice:
    """The paired headset."""

    address: str  # e.g. "AC:80:0A:12:34:56"
    name: str


class PairingResult(StrEnum):
    """Result of a search for a headset (pairing)."""

    PAIRED = "paired"  # found, paired and (usually) connected - the old pairing has been removed
    NOT_FOUND = "not_found"  # no headset in pairing mode within the search time - the old pairing is kept
    FAILED = "failed"  # found, but pairing failed - the old pairing is kept


@dataclass(frozen=True)
class BluetoothState:
    """State of the bluetooth headset - published whenever it changes."""

    device: BluetoothDevice | None = None  # paired headset or None
    connected: bool = False  # the audio connection (A2DP) is up - the box plays on the headset
    battery: int | None = None  # % - only if the headset reports it
    volume: float | None = None  # % of the headset volume - None while unknown (e.g. not connected)
    pairing: bool = False  # a search is running
    pairing_result: PairingResult | None = None  # result of the last search
    virtual: bool = False  # virtual headset (--no-hardware) - no bluetooth involved


class BluetoothAction(StrEnum):
    PAIR = "pair"  # search a headset in pairing mode and pair it (replaces the paired one)
    UNPAIR = "unpair"  # remove the paired headset
    VOLUME = "volume"  # set the volume of the headset (``BluetoothCommand.volume``)


@dataclass(frozen=True)
class BluetoothCommand:
    action: BluetoothAction
    volume: float | None = None  # % - for VOLUME


BLUETOOTH_STATE = "bluetooth/state"
""":class:`BluetoothState` - bluetooth driver (or the virtual headset) → tinyjukebox"""

BLUETOOTH_COMMAND = "bluetooth/command"
""":class:`BluetoothCommand` - tinyjukebox → bluetooth driver (or the virtual headset)"""
