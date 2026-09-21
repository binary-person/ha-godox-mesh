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
    last_seen: float = 0.0,
):
    """An advertisement from a provisioned node running the Proxy feature.

    ``last_seen`` is the monotonic timestamp of the advert; pass a recent one
    (``time.monotonic() - age``) to exercise the freshness ranking.
    """
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
        time=last_seen,
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


async def test_strongest_node_wins_when_no_current(
    hass: HomeAssistant, monkeypatch
) -> None:
    """With no current gateway, the strongest reachable node is chosen.

    The configured light gets no special weight -- a gateway is pure transport,
    so which node the mesh is entered through does not matter.
    """
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [_proxy_advert("AA:other", rssi=-30), _proxy_advert(ADDRESS)],
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == "AA:other"


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


# --- freshness ranking ---------------------------------------------------


async def test_a_freshly_heard_node_beats_a_stale_configured_one(
    hass: HomeAssistant, monkeypatch
) -> None:
    """An off configured light lingering in the cache yields to a live sibling.

    This is the failover-speed win: the preferred node was last heard minutes
    ago (it is probably off), while a sibling is advertising now, so selection
    hops straight to the live sibling instead of stalling a full connect timeout
    on the preferred.
    """
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert(ADDRESS, rssi=-30, last_seen=now - 300),  # stale, strong
            _proxy_advert("AA:live", rssi=-80, last_seen=now - 1),  # fresh, weak
        ],
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == "AA:live"


async def test_configured_node_gets_no_special_weight(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Among equally-fresh nodes the strongest wins, configured or not."""
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:other", rssi=-30, last_seen=now - 1),  # fresh, strong
            _proxy_advert(ADDRESS, rssi=-80, last_seen=now - 1),  # fresh, weak
        ],
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == "AA:other"


async def test_a_stale_node_is_still_used_when_it_is_all_there_is(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Freshness only ranks; a stale-cached node is still tried, never excluded.

    A node that is genuinely connected stops advertising and would look stale,
    so freshness must never remove a candidate outright.
    """
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert(ADDRESS, last_seen=now - 999)]
    )
    chosen = async_select_gateway(
        hass, network_key=NET_KEY, preferred=ADDRESS, current=None
    )
    assert chosen == ADDRESS


async def test_a_stronger_node_wins_whether_known_or_not(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The strongest reachable node wins; a known MAC gets no priority.

    Knowing a node's MAC only widens the list (it catches a node advertising
    Node Identity rather than the Network ID); it does not make a weaker node a
    better gateway than a stronger one.
    """
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
    assert chosen == "AA:unknown"


async def test_a_failed_node_is_off_the_list_until_it_advertises_again(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The whole policy in one test: a failed node rejoins only on its own advert.

    While its last advert predates its failure it is excluded (dropped from the
    list); once it advertises again -- proof it is alive -- it is back, with no
    separate state to clear.
    """
    import time

    now = time.monotonic()

    # Its last advert is older than when it failed: off the list, so selection
    # has nothing and degrades to the fallback.
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert(ADDRESS, last_seen=now - 5)]
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred="PP:PP",
        current=None,
        fail_time={ADDRESS: now},
    )
    assert chosen == "PP:PP"

    # A newer advert than the failure puts it back on the list.
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert(ADDRESS, last_seen=now + 1)]
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred="PP:PP",
        current=None,
        fail_time={ADDRESS: now},
    )
    assert chosen == ADDRESS


# --- instability ranking -------------------------------------------------


async def test_a_recently_dropped_node_sinks_below_a_steady_one(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A node that will not hold is ranked below a steadier one, weaker or not."""
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:flaky", rssi=-30),  # stronger, but just dropped
            _proxy_advert("AA:steady", rssi=-80),  # weaker, steady
        ],
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred=ADDRESS,
        current=None,
        unstable_since={"AA:flaky": now},
    )
    assert chosen == "AA:steady"


async def test_stickiness_yields_for_a_current_that_keeps_dropping(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The point of the fix: do not reconnect to the node that just dropped."""
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY,
        lambda *a, **k: [
            _proxy_advert("AA:flaky", rssi=-30),
            _proxy_advert("AA:steady", rssi=-80),
        ],
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred=ADDRESS,
        current="AA:flaky",
        unstable_since={"AA:flaky": now},
    )
    assert chosen == "AA:steady"


async def test_a_flaky_node_is_still_used_when_it_is_the_only_one(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The penalty only ranks; the sole node is still used, not abandoned."""
    import time

    now = time.monotonic()
    monkeypatch.setattr(
        DISCOVERY, lambda *a, **k: [_proxy_advert("AA:flaky", rssi=-30)]
    )
    chosen = async_select_gateway(
        hass,
        network_key=NET_KEY,
        preferred=ADDRESS,
        current="AA:flaky",
        unstable_since={"AA:flaky": now},
    )
    assert chosen == "AA:flaky"
