from __future__ import annotations

from typing import Any

import pytest

from godox_mesh_bt.client import MESH_PROXY_DATA_OUT_UUID, ProxyClient, GodoxMeshClient


@pytest.mark.asyncio
async def test_client_connects_and_disconnects(
    fake_client_factory: tuple[Any, list[Any]],
    no_device_resolver: Any,
) -> None:
    factory, clients = fake_client_factory
    client = GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=no_device_resolver,
    )

    await client.connect()
    assert client.is_connected is True
    assert clients[0].is_connected is True

    await client.disconnect()
    assert client.is_connected is False
    assert clients[0].is_connected is False


@pytest.mark.asyncio
async def test_client_resolves_live_ble_device_before_connecting(
    fake_client_factory: tuple[Any, list[Any]],
) -> None:
    factory, clients = fake_client_factory
    live_device = object()
    resolved_addresses: list[str] = []

    async def resolver(address: str) -> object:
        resolved_addresses.append(address)
        return live_device

    client = GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=resolver,
    )

    await client.connect()

    assert resolved_addresses == ["device-id"]
    assert clients[0].address is live_device


@pytest.mark.asyncio
async def test_client_falls_back_to_address_when_resolver_finds_nothing(
    fake_client_factory: tuple[Any, list[Any]],
) -> None:
    factory, clients = fake_client_factory

    async def resolver(address: str) -> object | None:
        return None

    client = GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=resolver,
    )

    await client.connect()

    assert clients[0].address == "device-id"


@pytest.mark.asyncio
async def test_client_supports_async_context_manager(
    fake_client_factory: tuple[Any, list[Any]],
    no_device_resolver: Any,
) -> None:
    factory, clients = fake_client_factory

    async with GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=no_device_resolver,
    ) as client:
        assert client.is_connected is True

    assert clients[0].is_connected is False


@pytest.mark.asyncio
async def test_write_raw_sends_payload_to_characteristic(
    fake_client_factory: tuple[Any, list[Any]],
    no_device_resolver: Any,
) -> None:
    factory, clients = fake_client_factory
    client = GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=no_device_resolver,
    )
    await client.connect()

    await client.write_raw("char-id", bytes.fromhex("010203"), response=False)

    assert clients[0].writes == [("char-id", b"\x01\x02\x03", False)]


@pytest.mark.asyncio
async def test_write_raw_requires_connection(
    fake_client_factory: tuple[Any, list[Any]],
) -> None:
    factory, _ = fake_client_factory
    client = GodoxMeshClient("device-id", client_factory=factory)

    with pytest.raises(RuntimeError, match="client is not connected"):
        await client.write_raw("char-id", b"\x01", response=False)


@pytest.mark.asyncio
async def test_notification_subscription_plumbing(
    fake_client_factory: tuple[Any, list[Any]],
    no_device_resolver: Any,
) -> None:
    factory, clients = fake_client_factory
    client = GodoxMeshClient(
        "device-id",
        client_factory=factory,
        device_resolver=no_device_resolver,
    )
    await client.connect()

    received: list[bytes] = []

    def callback(data: bytes) -> None:
        received.append(data)

    await client.start_notify("notify-char", callback)
    stored_callback = clients[0].started_notifications[0][1]
    stored_callback("mock-char", bytearray(b"\x0a"))
    await client.stop_notify("notify-char")

    assert received == [b"\x0a"]
    assert clients[0].stopped_notifications == ["notify-char"]


@pytest.mark.asyncio
async def test_proxy_client_stop_notify_uses_proxy_characteristic(
    fake_client_factory: tuple[Any, list[Any]],
) -> None:
    factory, clients = fake_client_factory
    client = ProxyClient(
        "device-id",
        client_factory=factory,
    )
    await client.connect()

    await client.start_notify(lambda _data: None)
    await client.stop_notify()

    assert clients[0].started_notifications[0][0] == "00002ade-0000-1000-8000-00805f9b34fb"
    assert clients[0].stopped_notifications == ["00002ade-0000-1000-8000-00805f9b34fb"]


@pytest.mark.asyncio
async def test_start_notify_requires_connection(
    fake_client_factory: tuple[Any, list[Any]],
) -> None:
    factory, _ = fake_client_factory
    client = GodoxMeshClient("device-id", client_factory=factory)

    with pytest.raises(RuntimeError, match="client is not connected"):
        await client.start_notify("notify-char", lambda _data: None)


class TestProxyClientReconnect:
    """A dropped link must be fully re-established, not half-reused.

    A BLE connection can vanish without ``disconnect`` ever being called — the
    light loses power, a Bluetooth proxy reboots, the device walks out of
    range. The next connect builds a new underlying client, and the proxy
    session state must follow it.
    """

    @pytest.mark.asyncio
    async def test_notifications_resubscribe_after_a_silent_drop(self) -> None:
        clients: list[Any] = []

        def factory(address: str) -> Any:
            client = _NotifyingFakeClient()
            clients.append(client)
            return client

        proxy = ProxyClient("AA:BB", client_factory=factory)
        await proxy.connect()
        await proxy.start_notify(lambda pdu: None)
        assert clients[0].subscriptions == [MESH_PROXY_DATA_OUT_UUID]

        # The link dies without disconnect() ever being called.
        clients[0].is_connected = False

        await proxy.connect()
        await proxy.start_notify(lambda pdu: None)

        assert len(clients) == 2
        assert clients[1].subscriptions == [MESH_PROXY_DATA_OUT_UUID], (
            "the replacement connection was never subscribed, so the device's "
            "beacon and proxy acknowledgements would never arrive"
        )

    @pytest.mark.asyncio
    async def test_callbacks_do_not_accumulate_across_reconnects(self) -> None:
        clients: list[Any] = []

        def factory(address: str) -> Any:
            client = _NotifyingFakeClient()
            clients.append(client)
            return client

        proxy = ProxyClient("AA:BB", client_factory=factory)
        seen: list[bytes] = []

        for _ in range(3):
            await proxy.connect()
            # The controller registers a fresh closure on every connect.
            await proxy.start_notify(lambda pdu: seen.append(pdu))
            clients[-1].is_connected = False

        clients[-1].is_connected = True
        clients[-1].emit(b"\x01beacon")

        assert len(seen) == 1, f"notification delivered {len(seen)} times"


class _NotifyingFakeClient:
    """Fake Bleak client that records subscriptions and can emit notifications."""

    def __init__(self) -> None:
        self.is_connected = False
        self.mtu_size = 247
        self.subscriptions: list[str] = []
        self._callback: Any = None

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def start_notify(self, characteristic: str, callback: Any) -> None:
        self.subscriptions.append(characteristic)
        self._callback = callback

    async def stop_notify(self, characteristic: str) -> None:
        self.subscriptions.remove(characteristic)
        self._callback = None

    def emit(self, payload: bytes) -> None:
        self._callback("char", bytearray(payload))
