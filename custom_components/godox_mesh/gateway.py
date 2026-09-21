"""Choose which mesh node to enter the network through.

A Bluetooth Mesh network is not addressed directly: a client opens an ordinary
GATT connection to one node running the Proxy feature, and that node relays
between the connection and the mesh. Any proxy-capable node will do, which
means losing one light need not cost access to the rest of the network.

Proxy nodes advertise the network's Network ID — ``k3(net_key)`` — in the Mesh
Proxy service data, so a node can be recognised as belonging to this network
without Home Assistant having provisioned it or ever having seen it before.
"""

from __future__ import annotations

import logging
import time

from ._lib.crypto import k3

from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.core import HomeAssistant, callback

from .const import (
    DROP_PENALTY_SECONDS,
    FRESH_ADVERT_SECONDS,
    MESH_PROXY_SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)

# First byte of the Mesh Proxy service data says how the node is identifying
# itself. Only Network ID can be matched against a key we hold; Node Identity
# carries a hash that would need the per-node identity key to verify.
PROXY_ID_TYPE_NETWORK_ID = 0x00
PROXY_ID_TYPE_NODE_IDENTITY = 0x01

NETWORK_ID_LENGTH = 8
_MIN_SERVICE_DATA = 1 + NETWORK_ID_LENGTH


@callback
def async_find_network_gateways(hass: HomeAssistant, network_key: str) -> list[str]:
    """Return addresses currently advertising as proxies for this network.

    Parameters
    ----------
    hass
        Home Assistant instance, used to read the Bluetooth manager's view of
        what is currently in range.
    network_key
        The mesh network key as 32 hexadecimal characters.

    Returns
    -------
    list[str]
        BLE addresses of nodes on this network, strongest signal first. Empty
        when nothing matching is in range.
    """
    network_id = k3(bytes.fromhex(network_key))
    matches: list[tuple[int, str]] = []
    for service_info in async_discovered_service_info(hass, connectable=True):
        data = service_info.service_data.get(MESH_PROXY_SERVICE_UUID)
        if not data or len(data) < _MIN_SERVICE_DATA:
            continue
        if data[0] != PROXY_ID_TYPE_NETWORK_ID:
            continue
        if data[1 : 1 + NETWORK_ID_LENGTH] != network_id:
            continue
        matches.append((service_info.rssi, service_info.address))

    matches.sort(key=lambda match: match[0], reverse=True)
    return [address for _rssi, address in matches]


@callback
def async_select_gateway(
    hass: HomeAssistant,
    *,
    network_key: str,
    preferred: str,
    current: str | None,
    known_macs: tuple[str, ...] = (),
    fail_time: dict[str, float] | None = None,
    unstable_since: dict[str, float] | None = None,
) -> str:
    """Pick the node to connect through: the best node currently on the list.

    A node is *on the list* when it is currently reachable and has not failed to
    connect more recently than it last advertised. Two things put a node on the
    list, so a node still advertising Node Identity right after a reconnect (not
    yet the Network ID) is not missed:

    * **A known address** -- one we provisioned, recognised by MAC.
    * **The Network ID advert** -- ``k3(net_key)``, which recognises any node on
      this mesh, even one this install never provisioned.

    A failed connect takes a node off the list (its ``fail_time`` is stamped);
    only its *own* next advertisement puts it back -- proof it is alive again,
    rather than an unrelated node connecting. That one rule is the whole policy:
    it is what keeps a light that advertises but will not connect from being
    re-tried ahead of a healthy sibling, without any separate "avoid" state to
    clear.

    The list is ordered so the head is the soundest choice: the node already in
    use first (stickiness -- reconnecting costs a beacon echo and two filter
    PDUs, so do not churn as signal drifts), then freshly-heard nodes before
    ones only lingering in Home Assistant's cache after going quiet, then by
    signal strength. Which node it *is* does not matter -- a gateway is pure
    transport -- so the configured light gets no special weight beyond being the
    fallback when nothing is reachable at all.

    Parameters
    ----------
    network_key
        Mesh network key, used to recognise this network's nodes by advert.
    preferred
        The address configured on the config entry, returned only as the
        fallback when the list is empty, so behaviour degrades to a fixed
        gateway rather than refusing to try.
    current
        The node currently in use, if any -- kept when still on the list.
    known_macs
        Addresses of nodes known to be on this mesh (from provisioning).
    fail_time
        ``{address: monotonic time it last failed to connect}``. A node is off
        the list until it advertises again after that time.
    unstable_since
        ``{address: monotonic time it last dropped a connection quickly}``. Such
        a node stays *on* the list but is ranked below steadier ones, and the
        stickiness for ``current`` yields when the current node is the unstable
        one -- so a light that will not hold a connection hands over to a sibling
        rather than being reconnected to again and again.

    Returns
    -------
    str
        Address to connect to.
    """
    fail_time = fail_time or {}
    unstable_since = unstable_since or {}
    now = time.monotonic()
    # Adverts the manager already holds -- no scan is triggered on the adapter.
    seen = {
        info.address: info
        for info in async_discovered_service_info(hass, connectable=True)
    }
    on_this_mesh = set(known_macs) | set(async_find_network_gateways(hass, network_key))

    # On the list: reachable, and not failed more recently than it last
    # advertised. A node genuinely gone stops advertising, so its timestamp
    # freezes below its fail time and it stays off; one that is alive advertises
    # again and its fresher timestamp puts it back.
    candidates = [
        address
        for address, info in seen.items()
        if address in on_this_mesh and info.time > fail_time.get(address, float("-inf"))
    ]
    if not candidates:
        _LOGGER.debug(
            "no reachable node of this network; falling back to %s", preferred
        )
        return preferred

    def unstable(address: str) -> bool:
        return now - unstable_since.get(address, float("-inf")) < DROP_PENALTY_SECONDS

    # Stay put -- unless the current node is the one that keeps dropping, in
    # which case hand over to a steadier sibling.
    if current is not None and current in candidates and not unstable(current):
        return current

    # Head of the list: steady before recently-flaky, freshly-heard before
    # stale-cached, then strongest signal.
    candidates.sort(
        key=lambda a: (
            unstable(a),
            now - seen[a].time >= FRESH_ADVERT_SECONDS,
            -seen[a].rssi,
        )
    )
    chosen = candidates[0]
    if chosen != preferred:
        _LOGGER.info(
            "entering the mesh through %s; %s is not the soundest choice right now",
            chosen,
            preferred,
        )
    return chosen
