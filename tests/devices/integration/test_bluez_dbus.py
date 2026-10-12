"""DbusBluez with real D-Bus messages: a private dbus-daemon and a fake BlueZ (tests/devices/bluezservice.py) - the
bluetooth of the machine is never touched. Skipped without dbus-daemon."""

import asyncio
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from dbus_fast import MessageType, Variant

from tests.devices.bluezservice import ADAPTER, ADAPTER_PATH, DEVICE, FakeBluezService, device_path, settle
from tinyjukebox.devices.bluetooth import bluez as bluez_module
from tinyjukebox.devices.bluetooth.bluez import A2DP_SINK_UUID, AGENT_PATH, PLAYER_PATH, BluezError, DbusBluez, Device

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="needs dbus-daemon")

HEADSET = "AC:80:0A:11:22:33"
AGENT = "org.bluez.Agent1"
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPERTIES = "org.freedesktop.DBus.Properties"


@pytest.fixture(scope="module")
def bus_address() -> Iterator[str]:
    folder = tempfile.mkdtemp(prefix="bus")  # short: the path of a unix socket is limited to ~100 characters
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address", f"--address=unix:dir={folder}"],
        stdout=subprocess.PIPE,
    )
    assert daemon.stdout is not None
    try:
        yield daemon.stdout.readline().decode().strip()
    finally:
        daemon.terminate()
        daemon.wait()
        daemon.stdout.close()
        shutil.rmtree(folder, ignore_errors=True)


class Policy:
    """The headset link as agent policy and player - records the commands."""

    def __init__(self) -> None:
        self.pairing: set[str] = set()
        self.services: set[str] = set()
        self.commands: list[str] = []

    def allows_pairing(self, device_path: str) -> bool:
        return device_path in self.pairing

    def allows_service(self, device_path: str) -> bool:
        return device_path in self.services

    def player_command(self, command: str) -> None:
        self.commands.append(command)


Scenario = Callable[[FakeBluezService, DbusBluez, Policy], Awaitable[None]]


def run(bus_address: str, logger: logging.Logger, scenario: Scenario, player: bool = True) -> None:
    """Run ``scenario`` with a fake BlueZ and a connected client in one event loop."""

    async def main() -> None:
        service = await FakeBluezService.create(bus_address)
        client = DbusBluez(logger, bus_address=bus_address)
        policy = Policy()
        try:
            await client.connect(policy, policy if player else None)
            await scenario(service, client, policy)
        finally:
            await client.close()
            service.bus.disconnect()

    asyncio.run(asyncio.wait_for(main(), 20))


def find(client: DbusBluez, address: str) -> Device:
    return next(device for device in client.devices() if device.address == address)


def test_connect_sets_up_adapter_agent_and_player(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        adapter = service.objects[ADAPTER_PATH][ADAPTER]
        assert adapter["Powered"].value is True
        assert adapter["Pairable"].value is True  # bondable: otherwise the pairing would not be kept
        assert adapter["Discoverable"].value is False  # nobody sees the box - and the agent rejects strangers
        assert service.agent is not None
        assert service.agent[1:] == (AGENT_PATH, "NoInputNoOutput")
        assert service.default_agent == AGENT_PATH
        assert service.player is not None
        path, properties = service.player
        assert path == PLAYER_PATH
        assert properties["PlaybackStatus"].value == "Stopped"
        assert properties["CanGoNext"].value is True
        assert client.devices() == []
        await client.close()
        await settle()
        assert service.agent is None  # unregistered
        assert service.player is None

    run(bus_address, logger, scenario)


def test_without_player_and_without_media_interface(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        assert service.player is None  # avrcp: input - BlueZ passes the buttons to the input device
        assert service.agent is not None

    run(bus_address, logger, scenario, player=False)


def test_devices_follow_the_signals_of_bluez(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        changes: list[int] = []
        client.on_change = lambda: changes.append(1)
        path = service.add_device(
            HEADSET, Alias=Variant("s", "WH-CH530"), Name=Variant("s", "LE_WH-CH530"), Class=Variant("u", 0x240404),
            UUIDs=Variant("as", [A2DP_SINK_UUID.upper()]), Paired=Variant("b", True), Trusted=Variant("b", True),
        )  # fmt: skip
        service.add_device("00:00:00:00:00:02", "/org/bluez/hci1")  # of another adapter
        await settle()
        assert client.devices() == [
            Device(path=path, address=HEADSET, name="WH-CH530", paired=True, trusted=True, uuids=(A2DP_SINK_UUID,),
                   device_class=0x240404)
        ]  # fmt: skip
        assert find(client, HEADSET).is_audio_sink
        assert changes
        # connected, with battery and - a moment later - the a2dp transport
        service.set(path, DEVICE, Connected=Variant("b", True), RSSI=Variant("n", -50))
        service.add_object(path, {"org.bluez.Battery1": {"Percentage": Variant("y", 70)}})
        await settle()
        device = find(client, HEADSET)
        assert (device.connected, device.battery, device.audio, device.rssi) == (True, 70, False, -50)
        assert not device.discovered  # no search is running
        service.add_object(f"{path}/sep1/fd0", {"org.bluez.MediaTransport1": {"Device": Variant("o", path)}})
        await settle()
        assert find(client, HEADSET).audio
        service.remove_object(f"{path}/sep1/fd0")
        service.set(path, DEVICE, invalidated=("RSSI",), Connected=Variant("b", False))
        await settle()
        device = find(client, HEADSET)
        assert (device.connected, device.audio, device.rssi) == (False, False, None)
        service.remove_object(path)
        await settle()
        assert client.devices() == []

    run(bus_address, logger, scenario)


def test_device_without_name_and_services(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        service.add_device(HEADSET)
        service.add_device("10:20:30:40:50:60", Class=Variant("u", 0x5A020C))  # a phone
        await settle()
        assert find(client, HEADSET).name == HEADSET
        assert not find(client, HEADSET).is_audio_sink  # unknown so far
        assert not find(client, "10:20:30:40:50:60").is_audio_sink

    run(bus_address, logger, scenario)


def test_search_finds_only_what_answers_now(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        known = service.add_device(HEADSET, RSSI=Variant("n", -40))  # seen by an earlier search
        await settle()
        await client.stop_discovery()  # no search yet: nothing to stop
        assert service.called("StopDiscovery") == []
        await client.start_discovery()
        assert service.discovery_filter is not None
        assert service.discovery_filter["Transport"].value == "bredr"  # classic bluetooth only
        assert service.objects[ADAPTER_PATH][ADAPTER]["Discovering"].value is True
        assert not find(client, HEADSET).discovered
        service.add_device("00:11:22:33:44:55", RSSI=Variant("n", -60))  # new
        service.set(known, DEVICE, RSSI=Variant("n", -45))  # found again
        await settle()
        assert find(client, HEADSET).discovered
        assert find(client, "00:11:22:33:44:55").discovered
        await client.stop_discovery()
        assert service.objects[ADAPTER_PATH][ADAPTER]["Discovering"].value is False
        await client.start_discovery()  # a new search starts from scratch
        assert not find(client, HEADSET).discovered
        service.objects[ADAPTER_PATH][ADAPTER]["Discovering"] = Variant("b", False)
        await client.stop_discovery()  # BlueZ: "No discovery started" - does not matter

    run(bus_address, logger, scenario)


def test_pair_trust_connect_remove(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        path = service.add_device(HEADSET)
        await settle()
        await client.pair(path)
        await client.set_trusted(path)
        await client.connect_device(path)
        await settle()
        device = find(client, HEADSET)
        assert (device.paired, device.trusted, device.connected, device.audio) == (True, True, True, True)
        with pytest.raises(BluezError, match="Already Exists") as error:
            await client.pair(path)
        assert error.value.name == "org.bluez.Error.AlreadyExists"
        service.errors["Connect"] = ("org.bluez.Error.Failed", "br-connection-page-timeout")
        with pytest.raises(BluezError, match="br-connection-page-timeout"):
            await client.connect_device(path)
        await client.remove_device(path)
        await settle()
        assert client.devices() == []
        with pytest.raises(BluezError, match="Does Not Exist"):
            await client.remove_device(path)

    run(bus_address, logger, scenario)


def test_call_without_answer_times_out(
    bus_address: str, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bluez_module, "PAIR_TIMEOUT", 0.1)

    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        path = service.add_device(HEADSET)
        service.silent.add("Pair")
        with pytest.raises(TimeoutError):
            await client.pair(path)
        assert service.called("Pair")

    run(bus_address, logger, scenario)


def test_agent_accepts_only_the_headset_of_the_box(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        headset, stranger = device_path(HEADSET), device_path("66:66:66:66:66:66")
        policy.pairing.add(headset)
        policy.services.add(headset)

        async def agent(member: str, signature: str, *body: Any) -> Any:
            reply = await service.call_client(AGENT_PATH, AGENT, member, signature, body)
            return reply.error_name if reply.message_type == MessageType.ERROR else reply.body

        assert await agent("RequestPinCode", "o", headset) == ["0000"]
        assert await agent("RequestPasskey", "o", headset) == [0]
        assert await agent("RequestConfirmation", "ou", headset, 123456) == []
        assert await agent("RequestAuthorization", "o", headset) == []
        assert await agent("AuthorizeService", "os", headset, A2DP_SINK_UUID) == []
        assert await agent("DisplayPinCode", "os", headset, "0000") == []
        assert await agent("DisplayPasskey", "ouq", headset, 0, 0) == []
        assert await agent("Cancel", "") == []
        assert await agent("Release", "") == []
        rejected: list[tuple[str, str, list[Any]]] = [
            ("RequestPinCode", "o", [stranger]),
            ("RequestPasskey", "o", [stranger]),
            ("RequestConfirmation", "ou", [stranger, 1]),
            ("RequestAuthorization", "o", [stranger]),
            ("AuthorizeService", "os", [stranger, A2DP_SINK_UUID]),
        ]
        for member, signature, body in rejected:
            assert await agent(member, signature, *body) == "org.bluez.Error.Rejected"
        policy.pairing.clear()  # the search is over: the headset may connect, but nobody pairs
        assert await agent("RequestAuthorization", "o", headset) == "org.bluez.Error.Rejected"
        assert await agent("AuthorizeService", "os", headset, A2DP_SINK_UUID) == []
        policy.pairing.add(headset)
        assert await agent("Dance", "o", headset) == "org.freedesktop.DBus.Error.UnknownMethod"

    run(bus_address, logger, scenario)


def test_media_player_gets_the_buttons_and_reports_the_status(bus_address: str, logger: logging.Logger) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        for member in ("Play", "Pause", "PlayPause", "Stop", "Next", "Previous", "Seek"):
            signature, body = ("x", [5]) if member == "Seek" else ("", [])
            reply = await service.call_client(PLAYER_PATH, PLAYER, member, signature, body)
            assert reply.message_type == MessageType.METHOD_RETURN
        assert policy.commands == ["Play", "Pause", "PlayPause", "Stop", "Next", "Previous"]
        reply = await service.call_client(PLAYER_PATH, PROPERTIES, "GetAll", "s", [PLAYER])
        assert reply.body[0]["PlaybackStatus"].value == "Stopped"
        client.set_playback_status("playing")
        client.set_playback_status("playing")  # unchanged: no second signal
        await settle()
        assert [(s.member, s.body[0], s.body[1]["PlaybackStatus"].value) for s in service.signals] == [
            ("PropertiesChanged", PLAYER, "Playing")
        ]
        reply = await service.call_client(PLAYER_PATH, PROPERTIES, "Get", "ss", [PLAYER, "PlaybackStatus"])
        assert reply.body[0].value == "Playing"
        reply = await service.call_client(
            PLAYER_PATH, PROPERTIES, "Set", "ssv", [PLAYER, "Shuffle", Variant("b", True)]
        )
        assert reply.message_type == MessageType.METHOD_RETURN
        reply = await service.call_client(PLAYER_PATH, PROPERTIES, "Get", "ss", [PLAYER, "Nothing"])
        assert reply.message_type == MessageType.ERROR
        reply = await service.call_client(PLAYER_PATH, "org.mpris.MediaPlayer2", "Quit")
        assert reply.error_name == "org.freedesktop.DBus.Error.UnknownInterface"
        client.set_playback_status("paused")
        client.set_playback_status("nonsense")
        await settle()
        assert [s.body[1]["PlaybackStatus"].value for s in service.signals] == ["Playing", "Paused", "Stopped"]

    run(bus_address, logger, scenario)


def test_bluez_restart(bus_address: str, logger: logging.Logger, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        service.add_device(HEADSET, Paired=Variant("b", True))
        await settle()
        assert len(client.devices()) == 1
        changes: list[int] = []
        client.on_change = lambda: changes.append(1)
        await service.bus.release_name("org.bluez")  # bluetoothd stops ...
        await settle()
        assert client.devices() == []
        assert changes
        service.agent = service.player = None
        service.objects[ADAPTER_PATH][ADAPTER]["Discoverable"] = Variant("b", True)
        await service.bus.request_name("org.bluez")  # ... and is back
        await settle(0.2)
        assert service.agent is not None  # registered again
        assert service.player is not None
        assert service.objects[ADAPTER_PATH][ADAPTER]["Discoverable"].value is False
        assert len(client.devices()) == 1
        assert "BlueZ is back" in caplog.text

    run(bus_address, logger, scenario)


def test_bluez_restart_with_an_adapter_that_comes_later(
    bus_address: str, logger: logging.Logger, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a restart of bluetoothd the adapter may not be there at once - the registration is tried again."""
    monkeypatch.setattr(bluez_module, "SETUP_RETRY", 0.05)

    async def scenario(service: FakeBluezService, client: DbusBluez, policy: Policy) -> None:
        await service.bus.release_name("org.bluez")
        await settle()
        service.agent = service.player = None
        adapter = service.objects.pop(ADAPTER_PATH)
        await service.bus.request_name("org.bluez")
        await settle(0.2)
        assert service.agent is None  # nothing to register at yet
        service.objects[ADAPTER_PATH] = adapter
        await settle(0.2)
        assert service.agent is not None
        assert service.player is not None
        errors = [r for r in caplog.records if "registering at BlueZ failed" in r.message]
        assert len(errors) == 1  # the reason is reported once

    run(bus_address, logger, scenario)


def test_player_that_cannot_be_registered(
    bus_address: str, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    async def main() -> None:
        service = await FakeBluezService.create(bus_address)
        service.errors["RegisterPlayer"] = ("org.bluez.Error.NotSupported", "Operation is not supported")
        client = DbusBluez(logger, bus_address=bus_address)
        try:
            await client.connect(Policy(), Policy())  # the headset still plays - only its buttons do not work
            assert service.agent is not None
        finally:
            await client.close()
            service.bus.disconnect()

    asyncio.run(main())
    assert "registering the media player (buttons of the headset) failed" in caplog.text


def test_without_adapter(bus_address: str, logger: logging.Logger) -> None:
    async def main() -> None:
        service = await FakeBluezService.create(bus_address)
        client = DbusBluez(logger, adapter="hci7", bus_address=bus_address)
        try:
            with pytest.raises(BluezError, match="no bluetooth adapter /org/bluez/hci7"):
                await client.connect(Policy(), None)
        finally:
            await client.close()
            service.bus.disconnect()

    asyncio.run(main())


def test_without_bluez(bus_address: str, logger: logging.Logger) -> None:
    async def main() -> None:
        client = DbusBluez(logger, bus_address=bus_address)
        try:
            with pytest.raises(BluezError, match=r"org\.freedesktop\.DBus\.Error\.ServiceUnknown"):
                await client.connect(Policy(), None)  # nobody owns org.bluez
            with pytest.raises(BluezError):
                await client.start_discovery()
        finally:
            await client.close()
        with pytest.raises(BluezError, match="not connected"):
            await client.pair("/org/bluez/hci0/dev_x")
        client.set_playback_status("playing")  # no bus: nothing to send
        await client.close()  # closed already

    asyncio.run(main())
