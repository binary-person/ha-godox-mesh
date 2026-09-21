"""Recognition of a motorised accessory bolted to a Godox light.

Seven models in the mesh range carry ``attachmentSupport`` in Godox's
catalogue: they can have a motorised barndoor or Fresnel attached, and this
toggle is whether the light responds to it. It is the one part of the vendor
app's accessory surface that addresses the *light* rather than the accessory's
own motors -- ``GodoxOrderType.Light`` register 7 -- which is why it belongs
here and the rest does not.

Like the fan and the gels, nothing reports it back, so this is an
``assumed_state`` switch that restores its last value across restarts.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_ADDRESS, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .models import GodoxConfigEntry, GodoxNode, GodoxRuntimeData

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create an accessory-recognition switch for each node that supports one."""
    data = entry.runtime_data
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    async_add_entities(
        GodoxAttachmentSwitch(data, node, entry_address)
        for node in data.nodes
        if node.capabilities.attachment
    )


class GodoxAttachmentSwitch(SwitchEntity, RestoreEntity):
    """Whether the light responds to an attached motorised accessory."""

    _attr_has_entity_name = True
    _attr_translation_key = "attachment"
    _attr_assumed_state = True

    def __init__(
        self, data: GodoxRuntimeData, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the accessory-recognition switch."""
        self._data = data
        self._node = node
        self._attr_is_on = False
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = f"{node_id}_attachment"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == data.link.proxy_node_address
                else set()
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last state, since the light cannot report it."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let the light respond to its accessory."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the light responding to its accessory."""
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        await self._data.link.async_set_motion_recognize(
            self._node.address, enabled
        )
        self._attr_is_on = enabled
        self.async_write_ha_state()
