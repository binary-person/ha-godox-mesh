"""Fan speed control for Godox lights that have a controllable fan.

Roughly a third of the mesh range exposes fan speeds over the vendor protocol
(sub-command ``0xF5``). Which speeds a model offers, and the wire code for each,
come from the capability table rather than from per-model code here.

The fan cannot be read back: no status record reports it. So this is an
``assumed_state`` control that shows the last speed Home Assistant selected,
restored across restarts.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .mesh import GodoxMeshLink
from .models import GodoxConfigEntry, GodoxNode

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a fan-speed control for each node whose model has one."""
    data = entry.runtime_data
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    async_add_entities(
        GodoxFanModeSelect(data.link, node, entry_address)
        for node in data.nodes
        if node.capabilities.fan_modes
    )


class GodoxFanModeSelect(SelectEntity, RestoreEntity):
    """Cooling fan speed of a Godox light."""

    _attr_has_entity_name = True
    _attr_translation_key = "fan_mode"
    _attr_assumed_state = True

    def __init__(
        self, link: GodoxMeshLink, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the fan-speed control."""
        self._link = link
        self._node = node
        self._modes = node.capabilities.fan_modes
        self._attr_options = [mode.name for mode in self._modes]
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = f"{node_id}_fan_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == link.proxy_node_address
                else set()
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last selected speed, since the light cannot report it."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in self._attr_options:
            self._attr_current_option = last_state.state

    async def async_select_option(self, option: str) -> None:
        """Send the selected fan speed to the light."""
        mode = self._node.capabilities.fan_mode_by_name(option)
        if mode is None:
            raise HomeAssistantError(f"{option!r} is not a supported fan mode")
        await self._link.async_set_fan_mode(self._node.address, mode.code)
        self._attr_current_option = option
        self.async_write_ha_state()
