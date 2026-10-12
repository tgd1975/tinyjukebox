"""BlueZ on the system D-Bus (dbus-fast, asyncio): the objects of BlueZ as a cache that follows its signals, the calls
the headset link needs, and the two objects BlueZ calls back - the pairing agent and the media player (AVRCP).

Everything runs in the event loop of the bluetooth driver; ``on_change`` is called there after every change.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dbus_fast import BusType, Message, MessageType, Variant, unpack_variants
from dbus_fast.aio import MessageBus

from tinyjukebox.core.logging import get_child

__all__ = ["A2DP_SINK_UUID", "AgentPolicy", "Bluez", "BluezError", "DbusBluez", "Device", "PlayerCommands"]

BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
BATTERY = "org.bluez.Battery1"
TRANSPORT = "org.bluez.MediaTransport1"
MEDIA = "org.bluez.Media1"
AGENT_MANAGER = "org.bluez.AgentManager1"
AGENT = "org.bluez.Agent1"
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPERTIES = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"

AGENT_PATH = "/org/tinyjukebox/agent"
PLAYER_PATH = "/org/tinyjukebox/player"
REJECTED = "org.bluez.Error.Rejected"
PIN = "0000"  # for headsets that ask for a pin after all (Sony's default)

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
_MAJOR_AUDIO_VIDEO = 0x04  # major device class (bits 8-12 of the class of device)
_SERVICE_RENDERING_OR_AUDIO = 0x240000  # service classes rendering (bit 18) and audio (bit 21)

CALL_TIMEOUT = 10.0  # seconds for ordinary calls
SETUP_RETRY = 5.0  # seconds until the registration is tried again after BlueZ has been restarted
PAIR_TIMEOUT = 40.0
CONNECT_TIMEOUT = 25.0  # paging a headset that is switched off takes BlueZ about 5-20 s

_MATCH_RULES = (
    f"type='signal',sender='{BLUEZ}',interface='{PROPERTIES}',member='PropertiesChanged'",
    f"type='signal',sender='{BLUEZ}',interface='{OBJECT_MANAGER}'",
    f"type='signal',sender='{DBUS}',interface='{DBUS}',member='NameOwnerChanged',arg0='{BLUEZ}'",
)
_PLAYBACK_STATUS = {"playing": "Playing", "paused": "Paused", "stopped": "Stopped"}


class BluezError(Exception):
    """A call to BlueZ failed - ``name``: D-Bus error name (e.g. ``org.bluez.Error.AuthenticationFailed``)."""

    def __init__(self, name: str, text: str = "") -> None:
        super().__init__(f"{name}: {text}" if text else name)
        self.name = name


@dataclass(frozen=True)
class Device:
    """A bluetooth device as BlueZ knows it."""

    path: str
    address: str
    name: str
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    rssi: int | None = None
    uuids: tuple[str, ...] = ()
    device_class: int | None = None
    battery: int | None = None
    audio: bool = False  # an A2DP transport exists - the headset can play
    discovered: bool = False  # seen by the running (or last) search

    @property
    def is_audio_sink(self) -> bool:
        """A headset or speaker: A2DP sink among its services or - before they are known - by its device class."""
        if A2DP_SINK_UUID in self.uuids:
            return True
        if self.device_class is None:
            return False
        major = (self.device_class >> 8) & 0x1F
        return major == _MAJOR_AUDIO_VIDEO and bool(self.device_class & _SERVICE_RENDERING_OR_AUDIO)


class AgentPolicy(Protocol):
    """Decides the questions BlueZ asks the agent."""

    def allows_pairing(self, device_path: str) -> bool:
        """True for the headset that is being paired right now."""

    def allows_service(self, device_path: str) -> bool:
        """True for the headset of the box - nobody else may connect."""


class PlayerCommands(Protocol):
    def player_command(self, command: str) -> None:
        """A button of the headset: ``Play``, ``Pause``, ``PlayPause``, ``Stop``, ``Next`` or ``Previous``."""


class Bluez(Protocol):
    """What the headset link needs of BlueZ (:class:`DbusBluez`, or a fake in tests)."""

    on_change: Callable[[], None] | None

    async def connect(self, policy: AgentPolicy, player: PlayerCommands | None) -> None: ...

    async def close(self) -> None: ...

    def devices(self) -> list[Device]: ...

    async def start_discovery(self) -> None: ...

    async def stop_discovery(self) -> None: ...

    async def pair(self, path: str) -> None: ...

    async def set_trusted(self, path: str) -> None: ...

    async def connect_device(self, path: str) -> None: ...

    async def remove_device(self, path: str) -> None: ...

    def set_playback_status(self, status: str) -> None: ...


class DbusBluez:
    """:class:`Bluez` over D-Bus.

    The box is never visible (``Discoverable`` off) and its agent - without input and output ("just works") - asks
    ``policy``, which accepts nothing but the headset the box itself is pairing. With ``player`` the box registers as
    media player: BlueZ then passes the AVRCP buttons of the headset to it.
    """

    def __init__(self, logger: logging.Logger, adapter: str = "hci0", bus_address: str | None = None) -> None:
        """``bus_address``: another bus than the system bus (tests)."""
        self._logger = get_child(logger, type(self).__name__)
        self._adapter_path = f"/org/bluez/{adapter}"
        self._bus_address = bus_address
        self._bus: MessageBus | None = None
        self._objects: dict[str, dict[str, dict[str, Any]]] = {}  # path -> interface -> properties
        self._discovered: set[str] = set()
        self._discovering = False
        self._policy: AgentPolicy | None = None
        self._player: PlayerCommands | None = None
        self._playback_status = "Stopped"
        self._setup_task: asyncio.Task[None] | None = None
        self.on_change: Callable[[], None] | None = None

    # --- connection -----------------------------------------------------------------------------------------------

    async def connect(self, policy: AgentPolicy, player: PlayerCommands | None) -> None:
        """:raises BluezError: without BlueZ or the adapter. :raises Exception: without the bus."""
        self._policy, self._player = policy, player
        if self._bus_address is not None:
            bus = MessageBus(bus_address=self._bus_address)
        else:
            bus = MessageBus(bus_type=BusType.SYSTEM)
        self._bus = await bus.connect()
        bus.add_message_handler(self._on_message)
        for rule in _MATCH_RULES:
            await self._call(DBUS_PATH, DBUS, "AddMatch", "s", [rule], destination=DBUS)
        await self._setup()

    async def _setup(self) -> None:
        """Load the objects of BlueZ and register adapter settings, agent and player (also after BlueZ restarted)."""
        (objects,) = await self._call("/", OBJECT_MANAGER, "GetManagedObjects")
        self._objects = unpack_variants(objects)
        self._discovered.clear()
        if ADAPTER not in self._objects.get(self._adapter_path, {}):
            raise BluezError("org.bluez.Error.DoesNotExist", f"no bluetooth adapter {self._adapter_path}")
        # Pairable (bondable) must be on: without it the kernel pairs without bonding - the headset would be forgotten
        # with its first disconnection. Pairing from outside is kept away by the agent and by not being visible.
        for name, value in (("Powered", True), ("Pairable", True), ("Discoverable", False)):
            await self._set(self._adapter_path, ADAPTER, name, Variant("b", value))
        await self._call("/org/bluez", AGENT_MANAGER, "RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"])
        await self._call("/org/bluez", AGENT_MANAGER, "RequestDefaultAgent", "o", [AGENT_PATH])
        if self._player is not None:
            try:
                await self._call(
                    self._adapter_path, MEDIA, "RegisterPlayer", "oa{sv}", [PLAYER_PATH, self._player_properties()]
                )
            except BluezError as e:  # the headset still plays - only its buttons do not work
                self._logger.error("registering the media player (buttons of the headset) failed: %s", e)
        self._changed()

    async def close(self) -> None:
        bus, self._bus = self._bus, None
        if self._setup_task is not None:
            self._setup_task.cancel()
        if bus is None:
            return
        for path, interface, member, signature, body in (
            (self._adapter_path, MEDIA, "UnregisterPlayer", "o", [PLAYER_PATH]),
            ("/org/bluez", AGENT_MANAGER, "UnregisterAgent", "o", [AGENT_PATH]),
        ):
            with contextlib.suppress(BluezError, TimeoutError, OSError):  # BlueZ forgets both anyway
                await self._call(path, interface, member, signature, body, bus=bus, timeout=2.0)
        bus.disconnect()

    async def _call(
        self,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: Sequence[Any] = (),
        timeout: float = CALL_TIMEOUT,
        destination: str = BLUEZ,
        bus: MessageBus | None = None,
    ) -> list[Any]:
        bus = bus or self._bus
        if bus is None:
            raise BluezError("org.freedesktop.DBus.Error.Disconnected", "not connected")
        message = Message(
            destination=destination, path=path, interface=interface, member=member, signature=signature, body=list(body)
        )
        reply = await asyncio.wait_for(bus.call(message), timeout)
        if reply is None:
            raise BluezError("org.freedesktop.DBus.Error.NoReply", f"{interface}.{member}")
        if reply.message_type == MessageType.ERROR:
            raise BluezError(str(reply.error_name), str(reply.body[0]) if reply.body else "")
        return list(reply.body)

    async def _set(self, path: str, interface: str, name: str, value: Variant) -> None:
        await self._call(path, PROPERTIES, "Set", "ssv", [interface, name, value])

    # --- objects --------------------------------------------------------------------------------------------------

    def devices(self) -> list[Device]:
        audio = {
            interfaces[TRANSPORT].get("Device")
            for interfaces in self._objects.values()
            if TRANSPORT in interfaces
        }  # fmt: skip
        devices = []
        for path, interfaces in self._objects.items():
            properties = interfaces.get(DEVICE)
            if properties is None or not path.startswith(self._adapter_path + "/"):
                continue
            address = str(properties.get("Address", ""))
            devices.append(
                Device(
                    path=path,
                    address=address,
                    name=str(properties.get("Alias") or properties.get("Name") or address),
                    paired=bool(properties.get("Paired", False)),
                    trusted=bool(properties.get("Trusted", False)),
                    connected=bool(properties.get("Connected", False)),
                    rssi=properties.get("RSSI"),
                    uuids=tuple(str(uuid).lower() for uuid in properties.get("UUIDs", ())),
                    device_class=properties.get("Class"),
                    battery=interfaces.get(BATTERY, {}).get("Percentage"),
                    audio=path in audio,
                    discovered=path in self._discovered,
                )
            )
        return devices

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _on_message(self, message: Message) -> Message | bool | None:
        if message.message_type == MessageType.METHOD_CALL:
            if message.path == AGENT_PATH and message.interface == AGENT:
                return self._agent_call(message)
            if message.path == PLAYER_PATH:
                return self._player_call(message)
            return None
        if message.message_type == MessageType.SIGNAL:
            self._on_signal(message)
        return None

    def _on_signal(self, message: Message) -> None:
        """Follow the objects of BlueZ."""
        if message.member == "NameOwnerChanged" and message.interface == DBUS:
            self._bluez_restarted(str(message.body[2]))
        elif not str(message.path).startswith("/org/bluez") and message.path != "/":
            return
        elif message.member == "PropertiesChanged" and message.interface == PROPERTIES:
            interface, changed, invalidated = message.body
            properties = self._objects.setdefault(str(message.path), {}).setdefault(interface, {})
            properties.update(unpack_variants(changed))
            for name in invalidated:
                properties.pop(name, None)
            if interface == DEVICE and "RSSI" in changed and self._discovering:
                self._discovered.add(str(message.path))
            self._changed()
        elif message.member == "InterfacesAdded" and message.interface == OBJECT_MANAGER:
            path, interfaces = message.body
            self._objects.setdefault(path, {}).update(unpack_variants(interfaces))
            if self._discovering and "RSSI" in interfaces.get(DEVICE, {}):
                self._discovered.add(path)
            self._changed()
        elif message.member == "InterfacesRemoved" and message.interface == OBJECT_MANAGER:
            path, removed = message.body
            for interface in removed:
                self._objects.get(path, {}).pop(interface, None)
            if not self._objects.get(path, True):
                del self._objects[path]
                self._discovered.discard(path)
            self._changed()

    def _bluez_restarted(self, new_owner: str) -> None:
        if not new_owner:
            self._logger.warning("BlueZ has gone")
            if self._setup_task is not None:
                self._setup_task.cancel()
            self._objects = {}
            self._discovered.clear()
            self._changed()
            return
        self._logger.warning("BlueZ is back - registering again")

        async def setup() -> None:
            failure: str | None = None
            while self._bus is not None:
                try:
                    await self._setup()
                    return
                except (BluezError, TimeoutError) as e:  # e.g. the adapter is not there yet
                    if str(e) != failure:
                        failure = str(e)
                        self._logger.error("registering at BlueZ failed: %s - trying again", e)
                    await asyncio.sleep(SETUP_RETRY)

        if self._setup_task is not None:
            self._setup_task.cancel()
        self._setup_task = asyncio.get_running_loop().create_task(setup())

    # --- search / pairing -----------------------------------------------------------------------------------------

    async def start_discovery(self) -> None:
        """Search for classic bluetooth devices only (BlueALSA cannot play LE audio; headsets in pairing mode advertise
        over LE as well)."""
        self._discovered.clear()
        self._discovering = True
        await self._call(
            self._adapter_path, ADAPTER, "SetDiscoveryFilter", "a{sv}", [{"Transport": Variant("s", "bredr")}]
        )
        await self._call(self._adapter_path, ADAPTER, "StartDiscovery")

    async def stop_discovery(self) -> None:
        if not self._discovering:
            return
        self._discovering = False
        try:
            await self._call(self._adapter_path, ADAPTER, "StopDiscovery")
        except BluezError as e:
            self._logger.debug("stop_discovery - %s", e)

    async def pair(self, path: str) -> None:
        await self._call(path, DEVICE, "Pair", timeout=PAIR_TIMEOUT)

    async def set_trusted(self, path: str) -> None:
        await self._set(path, DEVICE, "Trusted", Variant("b", True))

    async def connect_device(self, path: str) -> None:
        await self._call(path, DEVICE, "Connect", timeout=CONNECT_TIMEOUT)

    async def remove_device(self, path: str) -> None:
        """Remove the device and its pairing (it is disconnected as well)."""
        await self._call(self._adapter_path, ADAPTER, "RemoveDevice", "o", [path])

    # --- agent (called by BlueZ) ----------------------------------------------------------------------------------

    def _agent_call(self, message: Message) -> Message:
        member = message.member
        device = str(message.body[0]) if message.body else ""
        policy = self._policy
        self._logger.info("agent - %s %s", member, device)
        if member in ("Release", "Cancel", "DisplayPinCode", "DisplayPasskey"):
            return Message.new_method_return(message)
        if member == "AuthorizeService":
            allowed = policy is not None and policy.allows_service(device)
        else:
            allowed = policy is not None and policy.allows_pairing(device)
        if not allowed:
            self._logger.warning("agent - %s of %s rejected", member, device)
            return Message.new_error(message, REJECTED, "not the headset of the box")
        if member == "RequestPinCode":
            return Message.new_method_return(message, "s", [PIN])
        if member == "RequestPasskey":
            return Message.new_method_return(message, "u", [int(PIN)])
        if member in ("RequestConfirmation", "RequestAuthorization", "AuthorizeService"):
            return Message.new_method_return(message)
        return Message.new_error(message, "org.freedesktop.DBus.Error.UnknownMethod", str(member))

    # --- media player (called by BlueZ: AVRCP buttons of the headset) -----------------------------------------------

    def _player_properties(self) -> dict[str, Variant]:
        return {
            "PlaybackStatus": Variant("s", self._playback_status),
            "LoopStatus": Variant("s", "None"),
            "Rate": Variant("d", 1.0),
            "Shuffle": Variant("b", False),
            "Metadata": Variant("a{sv}", {}),
            "Position": Variant("x", 0),
            "MinimumRate": Variant("d", 1.0),
            "MaximumRate": Variant("d", 1.0),
            "CanGoNext": Variant("b", True),
            "CanGoPrevious": Variant("b", True),
            "CanPlay": Variant("b", True),
            "CanPause": Variant("b", True),
            "CanSeek": Variant("b", False),
            "CanControl": Variant("b", True),
        }

    def _player_call(self, message: Message) -> Message:
        member = str(message.member)
        if message.interface == PROPERTIES:
            properties = self._player_properties()
            if member == "GetAll":
                return Message.new_method_return(message, "a{sv}", [properties])
            if member == "Get" and message.body[1] in properties:
                return Message.new_method_return(message, "v", [properties[message.body[1]]])
            if member == "Set":
                return Message.new_method_return(message)  # shuffle, loop, ... - not supported, not an error
            return Message.new_error(message, "org.freedesktop.DBus.Error.InvalidArgs", member)
        if message.interface == PLAYER:
            self._logger.info("player - %s", member)
            if self._player is not None and member in ("Play", "Pause", "PlayPause", "Stop", "Next", "Previous"):
                self._player.player_command(member)
            return Message.new_method_return(message)
        return Message.new_error(message, "org.freedesktop.DBus.Error.UnknownInterface", str(message.interface))

    def set_playback_status(self, status: str) -> None:
        """Tell the headset (via BlueZ) what the box plays: ``playing``, ``paused`` or ``stopped`` - it then sends
        the matching button."""
        playback_status = _PLAYBACK_STATUS.get(status, "Stopped")
        if playback_status == self._playback_status:
            return
        self._playback_status = playback_status
        if self._bus is not None and self._player is not None:
            changed = {"PlaybackStatus": Variant("s", playback_status)}
            self._bus.send(
                Message.new_signal(PLAYER_PATH, PROPERTIES, "PropertiesChanged", "sa{sv}as", [PLAYER, changed, []])
            )
