"""Effect speed for Godox lights whose effects have more than one speed.

The vendor protocol carries an effect's speed in the third data byte of the
``0xF3`` command, alongside the effect symbol and brightness. Home Assistant's
light platform has no concept of effect speed, so it is a separate `number`
entity: set the speed, then pick an effect, and the light runs it at that speed.

Only models with at least one multi-speed effect get the control. How many
steps an effect offers is per-effect (the catalogue calls it ``gear``), so the
entity's maximum is the highest any of the model's effects accepts; selecting a
speed above what the current effect supports is harmless, the firmware clamps.

Effects differ in how many speeds they accept -- most take none at all -- so the
entity publishes the ones that respond to it as attributes. Without that the
slider looks broken on the majority of effects, which simply ignore it.

Like the fan, speed cannot be read back — no status record reports it — so this
is an ``assumed_state`` control that restores its last value across restarts.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL_EFFECT_CHANGED
from .models import GodoxConfigEntry, GodoxNode, GodoxRuntimeData

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create an effect-speed control for each node whose effects have speeds."""
    data = entry.runtime_data
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    async_add_entities(
        GodoxEffectSpeedNumber(data, node, entry_address)
        for node in data.nodes
        if node.capabilities.max_effect_speed > 0
    )


class GodoxEffectSpeedNumber(NumberEntity, RestoreEntity):
    """Speed the light runs its effects at."""

    _attr_has_entity_name = True
    _attr_translation_key = "effect_speed"
    _attr_assumed_state = True
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_step = 1

    def __init__(
        self, data: GodoxRuntimeData, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the effect-speed control."""
        self._data = data
        self._node = node
        self._attr_native_value = data.effect_speeds.get(node.address, 0)
        node_id = f"{entry_address}_{node.address:04x}"
        # The light entity's unique id, which keys the effect-changed signal.
        self._light_unique_id = node_id
        self._attr_unique_id = f"{node_id}_effect_speed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == self._data.link.proxy_node_address
                else set()
            ),
        )

    @property
    def native_max_value(self) -> float:
        """The number of speeds the *running* effect accepts.

        Each effect has its own count -- often one, meaning the speed is
        ignored -- so a fixed maximum taken from the model as a whole tells the
        user nothing about the effect they just chose. Following the running
        effect makes the control describe itself: the slider collapses to a
        single position on an effect that has no speeds, and widens on one that
        does. Falls back to the model's widest before any effect is chosen.
        """
        running = self._data.current_effect.get(self._node.address)
        if running:
            effect = self._node.capabilities.effect_by_name(running)
            if effect is not None:
                return effect.speed_max
        return self._node.capabilities.max_effect_speed

    async def async_added_to_hass(self) -> None:
        """Restore the last speed, and follow the light's effect changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EFFECT_CHANGED.format(node_id=self._light_unique_id),
                self._effect_changed,
            )
        )
        await self._restore()

    @callback
    def _effect_changed(self) -> None:
        """Re-publish, so the slider's range matches the new effect.

        The range can shrink -- switching from a three-speed effect to a
        one-speed one -- and a value left above the new maximum renders as a
        slider pushed past its own end. Clamp it, and record the clamp so the
        next effect command sends what the control is showing.
        """
        ceiling = self.native_max_value
        if self._attr_native_value is not None and self._attr_native_value > ceiling:
            self._attr_native_value = ceiling
            self._data.effect_speeds[self._node.address] = int(ceiling)
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Which of this model's effects the speed actually applies to.

        Godox gives each effect its own number of speed steps and most have
        one, so a single slider cannot describe the model on its own. Listing
        the responsive effects here is what makes it usable: set the speed,
        then pick one of these.
        """
        responsive = {
            effect.name: effect.speed_max + 1
            for effect in self._node.capabilities.effects
            if effect.speed_max > 0
        }
        return {
            "applies_to_effects": sorted(responsive),
            "speeds_per_effect": responsive,
            "ignored_by_other_effects": True,
        }

    async def _restore(self) -> None:
        """Restore the last speed, since the light cannot report it."""
        if (last_state := await self.async_get_last_state()) is None:
            return
        try:
            restored = int(float(last_state.state))
        except (TypeError, ValueError):
            return
        if 0 <= restored <= self._attr_native_max_value:
            self._attr_native_value = restored
            self._data.effect_speeds[self._node.address] = restored

    async def async_set_native_value(self, value: float) -> None:
        """Record the speed the next effect command should use."""
        speed = int(value)
        self._data.effect_speeds[self._node.address] = speed
        self._attr_native_value = speed
        self.async_write_ha_state()
