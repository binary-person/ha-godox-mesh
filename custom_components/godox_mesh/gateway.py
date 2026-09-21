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

from ._lib.crypto import k3

from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.core import HomeAssistant, callback

from .const import MESH_PROXY_SERVICE_UUID

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
) -> str:
    """Pick the node to connect through.

    Two ways to recognise a node of this mesh, tried in that order:

    1. **A known address.** Nodes we provisioned have their BLE address on
       record; a reachable one can be connected to directly, without waiting for
       it to advertise the Network ID (a just-connected node advertises Node
       Identity for a while instead, so this is what makes failover prompt).
    2. **The Network ID advert.** Recognises *any* node on the network, even one
       this install never provisioned.

    The order is deliberately sticky. Reconnecting costs a beacon echo and two
    proxy filter PDUs, so churning between nodes as signal drifts would be worse
    than staying put.

    Parameters
    ----------
    network_key
        Mesh network key, used to recognise this network's nodes by advert.
    preferred
        The address configured on the config entry — the light this network was
        set up against.
    current
        The node currently in use, if any.
    known_macs
        Addresses of nodes known to be on this mesh (from provisioning).

    Returns
    -------
    str
        Address to connect to. Falls back to *preferred* when nothing is
        reachable, so behaviour degrades to a fixed gateway rather than refusing
        to try.
    """
    # In-range signal strengths, from adverts the manager already holds -- no
    # scan is triggered on the adapter.
    rssi = {
        info.address: info.rssi
        for info in async_discovered_service_info(hass, connectable=True)
    }
    known_in_range = sorted(
        (mac for mac in known_macs if mac in rssi),
        key=lambda mac: rssi[mac],
        reverse=True,
    )
    # Network-ID matches for anything not already covered by a known address.
    network_matches = [
        address
        for address in async_find_network_gateways(hass, network_key)
        if address not in known_in_range
    ]
    ordered = known_in_range + network_matches
    if not ordered:
        _LOGGER.debug(
            "no reachable node of this network; falling back to %s", preferred
        )
        return preferred

    if current is not None and current in ordered:
        return current
    if preferred in ordered:
        if current is not None:
            _LOGGER.debug("returning to the configured node %s", preferred)
        return preferred

    chosen = ordered[0]
    _LOGGER.info(
        "entering the mesh through %s; %s is not reachable", chosen, preferred
    )
    return chosen
