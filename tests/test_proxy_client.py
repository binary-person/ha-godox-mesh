from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from godox_mesh_bt.client import ProxyClient


@pytest.mark.asyncio
async def test_proxy_client_connect_disconnect() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.is_connected = False

    def factory(addr: str) -> MagicMock:
        assert addr == address
        return mock_client

    client = ProxyClient(address, client_factory=factory)
    await client.connect()
    
    mock_client.connect.assert_called_once()
    
    # Simulate connection
    mock_client.is_connected = True
    assert client.is_connected is True
    
    await client.disconnect()
    mock_client.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_proxy_client_start_notify() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    notifications: list[bytes] = []
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.is_connected = False

    client = ProxyClient(address, client_factory=lambda _addr: mock_client)
    await client.connect()
    mock_client.is_connected = True

    await client.start_notify(lambda data: notifications.append(data))

    callback = mock_client.start_notify.call_args.args[1]
    callback("mock-char", bytearray(b"\x01\x02"))

    mock_client.start_notify.assert_called_once()
    assert notifications == [b"\x01\x02"]


@pytest.mark.asyncio
async def test_proxy_client_write_complete_message() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    proxy_write_char = "00002add-0000-1000-8000-00805f9b34fb"
    # Network PDU (without Proxy Header)
    network_pdu = b"\x38\x00\x11\x22"
    
    mock_client = MagicMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True

    client = ProxyClient(address, client_factory=MagicMock(return_value=mock_client))
    client._client = mock_client
    
    # We should have a higher-level method or handle it in write_proxy
    # Actually, pack_proxy_network_pdu already prepends 0x00
    # Let's say write_proxy takes the whole Proxy PDU
    full_pdu = b"\x00" + network_pdu
    await client.write_proxy(full_pdu)
    
    mock_client.write_gatt_char.assert_called_once_with(
        proxy_write_char, full_pdu, response=False
    )
