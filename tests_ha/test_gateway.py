"""Tests for choosing which mesh node to connect through.

A Bluetooth Mesh network is entered through any node running the Proxy
feature. Those nodes advertise the network's Network ID — ``k3(net_key)`` — in
the Mesh Proxy service data, which is what lets Home Assistant recognise a
node as belonging to this network without having provisioned it itself.
"""

from __future__ import annotations

import pytest
from custom_components.godox_mesh.const import MESH_PROXY_SERVICE_UUID
from custom_components.godox_mesh.gateway import (
    PROXY_ID_TYPE_NETWORK_ID,
    PROXY_ID_TYPE_NODE_IDENTITY,
    async_find_network_gateways,
    async_select_gateway,
)
from custom_components.godox_mesh._lib.crypto import k3
from homeassistant.core import HomeAssistant

from tests_ha.conftest import ADDRESS, MESH_STATE

NET_KEY = MESH_STATE["network_key"]
OTHER_NET_KEY = "aa" * 16
DISCOVERY = "custom_components.godox_mesh.gateway.async_discovered_service_info"


def _proxy_advert(
    address: str,
    net_key: str = NET_KEY,
    *,
    id_type: int = PROXY_ID_TYPE_NETWORK_ID,
    rssi: int = -60,
    service_data: dict | None = None,
):
    """An advertisement from a provisioned node running the Proxy feature."""
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    if service_data is None:
        service_data = {
            MESH_PROXY_SERVICE_UUID: bytes([id_type]) + k3(bytes.fromhex(net_key))
        }
    device = BLEDevice(address, "GD_LED", {})
    advertisement = AdvertisementData(
        local_name="GD_LED",
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[MESH_PROXY_SERVICE_UUID],
        tx_power=None,
        rssi=rssi,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name="GD_LED",
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[MESH_PROXY_SERVICE_UUID],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=None,
    )


async def test_finds_nodes_on_this_network(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert("AA:1"), _proxy_advert("AA:2")]
    )
    assert async_find_network_gateways(hass, NET_KEY) == ["AA:1", "AA:2"]


async def test_ignores_nodes_on_a_different_network(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Someone else's mesh must never be treated as a way into ours."""
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [_proxy_advert("AA:1"), _proxy_advert("BB:1", OTHER_NET_KEY)],
    )
    assert async_find_network_gateways(hass, NET_KEY) == ["AA:1"]


async def test_ignores_node_identity_adverts(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Node Identity carries a hash, not the Network ID — it cannot be matched."""
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:1", id_type=PROXY_ID_TYPE_NODE_IDENTITY),
        ],
    )
    assert async_find_network_gateways(hass, NET_KEY) == []


@pytest.mark.parametrize(
    "service_data",
    [
        {},
        {MESH_PROXY_SERVICE_UUID: b""},
        {MESH_PROXY_SERVICE_UUID: b"\x00short"},
        {"0000180f-0000-1000-8000-00805f9b34fb": b"\x00" + b"\x11" * 8},
    ],
)
async def test_ignores_malformed_or_unrelated_adverts(
    hass: HomeAssistant, monkeypatch, service_data
) -> None:
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert("AA:1", service_data=service_data)]
    )
    assert async_find_network_gateways(hass, NET_KEY) == []


async def test_strongest_signal_first(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:far", rssi=-90),
            _proxy_advert("AA:near", rssi=-40),
        ],
    )
    assert async_find_network_gateways(hass, NET_KEY)[0] == "AA:near"


# --- selection policy ----------------------------------------------------


async def test_keeps_the_current_gateway(hass: HomeAssistant, monkeypatch) -> None:
    """Stickiness: do not churn between nodes as signal strength drifts."""
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [_proxy_advert("AA:other", rssi=-30), _proxy_advert(ADDRESS)],
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current="AA:other"
    )
    assert chosen == "AA:other"


async def test_prefers_the_entry_light_when_no_current(
    hass: HomeAssistant, monkeypatch
) -> None:
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [_proxy_advert("AA:other", rssi=-30), _proxy_advert(ADDRESS)],
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == ADDRESS


async def test_falls_back_to_another_node_when_the_entry_light_is_gone(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The whole point: the network stays reachable without light number one."""
    monkeypatch.setattr(DISCOVERY, lambda *a, **k: [_proxy_advert("AA:other")])
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=ADDRESS
    )
    assert chosen == "AA:other"


async def test_falls_back_to_the_entry_light_when_nothing_advertises(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Degrade to today's behaviour rather than refusing to try.

    If these lights turn out not to advertise the Network ID, selection must
    quietly return the configured address so nothing gets worse.
    """
    monkeypatch.setattr(DISCOVERY, lambda *a, **k: [])
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == ADDRESS


async def test_a_known_node_is_used_even_without_a_network_advert(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A node we provisioned is reachable by address even when it is not
    advertising the Network ID (e.g. still on Node Identity after a reconnect).

    This is why the entry light being off can fail over promptly: the sibling's
    address is known, so it need not wait to be recognised by advert.
    """
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [_proxy_advert("AA:known", service_data={})],
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred=ADDRESS,
        current=None,
        known_macs=("AA:known",),
    )
    assert chosen == "AA:known"


async def test_known_address_beats_the_network_scan(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A known node in range is chosen over an unknown Network-ID match."""
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:known", service_data={}, rssi=-70),
            _proxy_advert("AA:unknown", rssi=-40),  # advertises the network ID
        ],
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred=ADDRESS,
        current=None,
        known_macs=("AA:known",),
    )
    assert chosen == "AA:known"
