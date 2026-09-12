"""Battery sensor for battery-powered Godox lights.

Godox does not implement the standard Bluetooth Mesh battery model, so charge
comes over their vendor protocol, from the same family of status records as
brightness. It answers on **stock** firmware -- no patch is involved. The sensor
is created when the user has enabled polling for the entry AND the model is
battery-powered.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_ADDRESS, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BATTERY_POLL_SECONDS, CONF_READBACK, DOMAIN
from .mesh import GodoxMeshLink
from .models import GodoxConfigEntry, GodoxNode

PARALLEL_UPDATES = 1
SCAN_INTERVAL = timedelta(seconds=BATTERY_POLL_SECONDS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a battery sensor for each battery-powered node, when readback is on."""
    if not entry.options.get(CONF_READBACK):
        return
    data = entry.runtime_data
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    async_add_entities(
        (
            GodoxBatterySensor(data.link, node, entry_address)
            for node in data.nodes
            if node.capabilities.has_battery
        ),
        update_before_add=True,
    )


class GodoxBatterySensor(SensorEntity):
    """Battery charge of a Godox light, polled over the vendor protocol."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self, link: GodoxMeshLink, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the battery sensor."""
        self._link = link
        self._node = node
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = f"{node_id}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == link.proxy_node_address
                else set()
            ),
        )

    async def async_update(self) -> None:
        """Poll the light for its battery charge.

        A light that does not answer -- one momentarily unreachable, or a
        model that does not implement the record -- leaves the value
        unavailable rather than raising.
        """
        try:
            self._attr_native_value = await self._link.async_request_battery(
                self._node.address
            )
        except HomeAssistantError:
            self._attr_native_value = None
