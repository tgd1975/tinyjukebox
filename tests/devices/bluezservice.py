"""A fake BlueZ on a private D-Bus: owns ``org.bluez`` and offers what the bluetooth driver uses - object manager,
adapter, devices, agent manager and media. Runs in the event loop of the test, next to the client under test."""

import asyncio
from typing import Any

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

ADAPTER_PATH = "/org/bluez/hci0"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
PROPERTIES = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
FAILED = "org.bluez.Error.Failed"


def device_path(address: str, adapter: str = ADAPTER_PATH) -> str:
    return f"{adapter}/dev_{address.replace(':', '_')}"


class FakeBluezService:
    """``objects``: path -> interface -> properties (variants). ``calls``: (member, path, body) of every method call
    received; ``signals``: signals of the client (e.g. the playback status of its media player)."""

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self.objects: dict[str, dict[str, dict[str, Variant]]] = {
            "/org/bluez": {"org.bluez.AgentManager1": {}},
            ADAPTER_PATH: {
                ADAPTER: {
                    "Address": Variant("s", "B8:27:EB:00:00:01"),
                    "Powered": Variant("b", False),
                    "Pairable": Variant("b", False),
                    "Discoverable": Variant("b", True),
                    "Discovering": Variant("b", False),
                },
                "org.bluez.Media1": {},
            },
        }
        self.calls: list[tuple[str, str, list[Any]]] = []
        self.signals: list[Message] = []
        self.agent: tuple[str, str, str] | None = None  # (bus name of the client, path, capability)
        self.default_agent: str | None = None
        self.player: tuple[str, dict[str, Any]] | None = None
        self.discovery_filter: dict[str, Any] | None = None
        self.errors: dict[str, tuple[str, str]] = {}  # member -> (error name, text): the call fails
        self.silent: set[str] = set()  # members: the call is never answered

    @classmethod
    async def create(cls, address: str, own_name: bool = True) -> "FakeBluezService":
        bus = await MessageBus(bus_address=address).connect()
        service = cls(bus)
        bus.add_message_handler(service._on_message)
        await bus.call(
            Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                    member="AddMatch", signature="s", body=["type='signal',path_namespace='/org/tinyjukebox'"])
        )  # fmt: skip
        if own_name:
            await bus.request_name("org.bluez")
        return service

    def called(self, member: str) -> list[tuple[str, str, list[Any]]]:
        return [call for call in self.calls if call[0] == member]

    # --- changes (as BlueZ signals them) ----------------------------------------------------------------------------

    def add_device(self, address: str, adapter: str = ADAPTER_PATH, **properties: Variant) -> str:
        path = device_path(address, adapter)
        values = {"Address": Variant("s", address), "Paired": Variant("b", False), "Connected": Variant("b", False),
                  "Trusted": Variant("b", False), **properties}  # fmt: skip
        self.add_object(path, {DEVICE: values})
        return path

    def add_object(self, path: str, interfaces: dict[str, dict[str, Variant]]) -> None:
        self.objects.setdefault(path, {}).update(interfaces)
        self.bus.send(Message.new_signal("/", OBJECT_MANAGER, "InterfacesAdded", "oa{sa{sv}}", [path, interfaces]))

    def remove_object(self, path: str) -> None:
        interfaces = list(self.objects.pop(path))
        self.bus.send(Message.new_signal("/", OBJECT_MANAGER, "InterfacesRemoved", "oas", [path, interfaces]))

    def set(self, path: str, interface: str, invalidated: tuple[str, ...] = (), **properties: Variant) -> None:
        self.objects[path][interface].update(properties)
        for name in invalidated:
            self.objects[path][interface].pop(name, None)
        self.bus.send(
            Message.new_signal(
                path, PROPERTIES, "PropertiesChanged", "sa{sv}as", [interface, properties, list(invalidated)]
            )
        )

    async def call_client(self, path: str, interface: str, member: str, signature: str = "", body: Any = ()) -> Message:
        """Call the agent or the media player of the client - like BlueZ does."""
        assert self.agent is not None
        reply = await self.bus.call(
            Message(destination=self.agent[0], path=path, interface=interface, member=member, signature=signature,
                    body=list(body))
        )  # fmt: skip
        assert reply is not None
        return reply

    # --- method calls of the client -----------------------------------------------------------------------------------

    def _on_message(self, message: Message) -> Message | bool | None:
        if message.message_type == MessageType.SIGNAL and str(message.path).startswith("/org/tinyjukebox"):
            self.signals.append(message)
        if message.message_type != MessageType.METHOD_CALL:
            return None
        if message.destination not in ("org.bluez", self.bus.unique_name):
            return None
        return self._call(message)

    def _call(self, message: Message) -> Message | bool:
        member, path, body = str(message.member), str(message.path), list(message.body)
        self.calls.append((member, path, body))
        if member in self.silent:
            return True
        if member in self.errors:
            return Message.new_error(message, *self.errors[member])
        handler = getattr(self, f"_{member}", None)
        if handler is None or (path not in self.objects and path != "/"):
            return Message.new_error(message, "org.freedesktop.DBus.Error.UnknownMethod", f"{path} {member}")
        result = handler(message, path, *body)
        if isinstance(result, Message):
            return result
        return Message.new_method_return(message)

    def _GetManagedObjects(self, message: Message, path: str) -> Message:
        return Message.new_method_return(message, "a{oa{sa{sv}}}", [self.objects])

    def _Set(self, message: Message, path: str, interface: str, name: str, value: Variant) -> None:
        self.set(path, interface, (), **{name: value})

    def _RegisterAgent(self, message: Message, path: str, agent: str, capability: str) -> None:
        self.agent = (str(message.sender), agent, capability)

    def _RequestDefaultAgent(self, message: Message, path: str, agent: str) -> None:
        self.default_agent = agent

    def _UnregisterAgent(self, message: Message, path: str, agent: str) -> None:
        self.agent = None

    def _RegisterPlayer(self, message: Message, path: str, player: str, properties: dict[str, Any]) -> None:
        self.player = (player, properties)

    def _UnregisterPlayer(self, message: Message, path: str, player: str) -> None:
        self.player = None

    def _SetDiscoveryFilter(self, message: Message, path: str, discovery_filter: dict[str, Any]) -> None:
        self.discovery_filter = discovery_filter

    def _StartDiscovery(self, message: Message, path: str) -> None:
        self.set(path, ADAPTER, Discovering=Variant("b", True))

    def _StopDiscovery(self, message: Message, path: str) -> Message | None:
        if not self.objects[path][ADAPTER]["Discovering"].value:
            return Message.new_error(message, FAILED, "No discovery started")
        self.set(path, ADAPTER, Discovering=Variant("b", False))
        return None

    def _RemoveDevice(self, message: Message, path: str, device: str) -> Message | None:
        if device not in self.objects:
            return Message.new_error(message, "org.bluez.Error.DoesNotExist", "Does Not Exist")
        self.remove_object(device)
        return None

    def _Pair(self, message: Message, path: str) -> Message | None:
        if self.objects[path][DEVICE]["Paired"].value:
            return Message.new_error(message, "org.bluez.Error.AlreadyExists", "Already Exists")
        self.set(path, DEVICE, Paired=Variant("b", True))
        return None

    def _Connect(self, message: Message, path: str) -> None:
        self.set(path, DEVICE, Connected=Variant("b", True))
        self.add_object(f"{path}/sep1/fd0", {"org.bluez.MediaTransport1": {"Device": Variant("o", path)}})


async def settle(delay: float = 0.05) -> None:
    """Let the signals travel (service -> bus -> client)."""
    await asyncio.sleep(delay)
