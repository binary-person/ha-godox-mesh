"""The Godox Bluetooth Mesh integration."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_NAME, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_MESH,
    CONF_MODEL,
    CONF_RADIO_ID,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)
from .mesh import GodoxMeshLink
from .mesh_state_input import mesh_state_from_dict
from .models import GodoxConfigEntry, GodoxNode, GodoxRuntimeData
from .store import GodoxSequenceStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: GodoxConfigEntry) -> bool:
    """Set up Godox Bluetooth Mesh from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    if bluetooth.async_ble_device_from_address(hass, address, connectable=True) is None:
        raise ConfigEntryNotReady(
            f"Could not find Godox light with address {address}. It may be powered "
            "off or out of range of every Bluetooth adapter and proxy."
        )

    state = mesh_state_from_dict(entry.data[CONF_MESH])
    store = GodoxSequenceStore(hass, entry.entry_id)
    sequence_number = await store.async_load(state.sequence_number)

    link = GodoxMeshLink(
        hass,
        address=address,
        name=entry.title,
        state=state,
        store=store,
        sequence_number=sequence_number,
    )

    nodes = _nodes_from_entry(entry)
    entry.runtime_data = GodoxRuntimeData(link=link, store=store, nodes=nodes)
    _async_prune_devices(hass, entry, nodes)

    async def _async_stop(_event: Event) -> None:
        """Close the proxy connection cleanly on Home Assistant shutdown."""
        await link.async_shutdown()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GodoxConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.link.async_shutdown()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: GodoxConfigEntry) -> None:
    """Clean up storage and allow the device to be discovered again."""
    await GodoxSequenceStore(hass, entry.entry_id).async_remove()
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GodoxConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow a light to be deleted from its device page.

    Deleting the device is only half the job: the node would come back on the
    next reload unless it is dropped from the options too.
    """
    removed = {
        int(identifier.rsplit("_", 1)[1], 16)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and "_" in identifier
    }
    remaining = [
        node
        for node in entry.options.get(CONF_NODES, [])
        if node[CONF_NODE_ADDRESS] not in removed
    ]
    if remaining != list(entry.options.get(CONF_NODES, [])):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_NODES: remaining}
        )
    return True


def _async_prune_devices(
    hass: HomeAssistant, entry: GodoxConfigEntry, nodes: list[GodoxNode]
) -> None:
    """Drop devices for nodes that are no longer configured.

    Without this, removing a light leaves an unavailable entity and an orphaned
    device behind, which look like a broken integration rather than a removal.
    """
    registry = dr.async_get(hass)
    current = {_node_device_id(entry, node.address) for node in nodes}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            domain == DOMAIN and identifier in current
            for domain, identifier in device.identifiers
        ):
            continue
        _LOGGER.debug("removing device for unconfigured node: %s", device.name)
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _node_device_id(entry: GodoxConfigEntry, node_address: int) -> str:
    """Return the device registry identifier for one node."""
    return f"{entry.unique_id or entry.data[CONF_ADDRESS]}_{node_address:04x}"


async def _async_update_listener(hass: HomeAssistant, entry: GodoxConfigEntry) -> None:
    """Reload when the node list changes so entities match the options."""
    await hass.config_entries.async_reload(entry.entry_id)


def _nodes_from_entry(entry: GodoxConfigEntry) -> list[GodoxNode]:
    """Build the node list from entry options, falling back to the mesh state."""
    configured = entry.options.get(CONF_NODES)
    if not configured:
        return [
            GodoxNode(address=entry.data[CONF_MESH]["node_address"], name=entry.title)
        ]
    return [
        GodoxNode(
            address=node[CONF_NODE_ADDRESS],
            name=node.get(CONF_NAME) or entry.title,
            model=node.get(CONF_MODEL),
            radio_id=node.get(CONF_RADIO_ID),
        )
        for node in configured
    ]
