"""The Home Assistant BLE client wrapper: its connection-error behaviour.

These are the paths a "my light won't connect" report exercises -- the device
being out of range, a connection that silently drops, and a write attempted
before connecting. The rest of the class is thin passthrough to
``bleak-retry-connector`` and is left to that library.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from custom_components.godox_mesh.ble import DeviceNotFound, HomeAssistantBleakClient

RESOLVE = "custom_components.godox_mesh.ble.bluetooth.async_ble_device_from_address"


def _client() -> HomeAssistantBleakClient:
    return HomeAssistantBleakClient(MagicMock(), "AA:BB:CC:DD:EE:FF", "Light")


async def test_connect_raises_device_not_found_when_out_of_range() -> None:
    """The most common failure: the Bluetooth manager cannot see the light."""
    client = _client()
    with patch(RESOLVE, return_value=None), pytest.raises(DeviceNotFound) as err:
        await client.connect()
    # The message has to name the light, since that is all a bug report shows.
    assert "Light" in str(err.value)
    assert "AA:BB:CC:DD:EE:FF" in str(err.value)


def test_dropped_connection_reads_as_not_connected() -> None:
    """A silently dropped connection must read as disconnected at once.

    Proxy writes carry no GATT response, so without the disconnect callback a
    write into a dead connection would look like it succeeded.
    """
    client = _client()
    underlying = MagicMock()
    underlying.is_connected = True
    client._client = underlying
    assert client.is_connected is True

    client._on_disconnected(underlying)
    assert client.is_connected is False


def test_an_unsolicited_drop_reports_to_on_drop() -> None:
    """A connection dropping on its own tells the link, with the address."""
    dropped: list[str] = []
    client = HomeAssistantBleakClient(
        MagicMock(), "AA:BB:CC:DD:EE:FF", "Light", on_drop=dropped.append
    )
    client._on_disconnected(MagicMock())
    assert dropped == ["AA:BB:CC:DD:EE:FF"]


async def test_a_deliberate_close_does_not_report_a_drop() -> None:
    """Closing the connection ourselves must not look like an unreliable node."""
    dropped: list[str] = []
    client = HomeAssistantBleakClient(
        MagicMock(), "AA:BB:CC:DD:EE:FF", "Light", on_drop=dropped.append
    )
    underlying = MagicMock()
    underlying.disconnect = _AsyncNoop()
    client._client = underlying

    # A real stack fires the disconnect callback during our own disconnect(); it
    # must be suppressed because _closing was set first.
    async def disconnect_and_fire(*_a: object, **_k: object) -> None:
        client._on_disconnected(underlying)

    underlying.disconnect = disconnect_and_fire
    await client.disconnect()

    assert dropped == []


async def test_write_before_connect_raises_device_not_found() -> None:
    """Writing without a connection is a clear error, not an AttributeError."""
    client = _client()
    with pytest.raises(DeviceNotFound):
        await client.write_gatt_char("char-uuid", b"\x00")


async def test_disconnect_clears_the_client_and_drop_flag() -> None:
    """After disconnect the wrapper is reusable: no client, drop flag cleared."""
    client = _client()
    underlying = MagicMock()
    underlying.disconnect = _AsyncNoop()
    client._client = underlying
    client._dropped = True

    await client.disconnect()

    assert client._client is None
    assert client._dropped is False
    assert client.is_connected is False


class _AsyncNoop:
    """A minimal awaitable stand-in for an async method."""

    async def __call__(self, *args: object, **kwargs: object) -> None:
        return None
