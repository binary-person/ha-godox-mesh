"""Light platform for Godox Bluetooth Mesh lights."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_ADDRESS, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import (
    BRIGHTNESS_SCALE,
    CONF_POLL_CCT,
    CONF_READBACK,
    DOMAIN,
    EFFECT_OFF,
    SIGNAL_EFFECT_CHANGED,
    MANUFACTURER,
)
from .models import GodoxConfigEntry, GodoxNode, GodoxRuntimeData

# Every command shares one BLE connection and one mesh sequence counter, so
# service calls must not overlap.
_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

DEFAULT_KELVIN = 5600


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one light entity per configured mesh node."""
    data = entry.runtime_data
    # Identity comes from the config entry, never from the live connection.
    # The node used to enter the mesh changes as lights come and go; letting
    # entity and device identity follow it would rename everything on failover
    # and take the user's history and automations with it.
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    polling = bool(entry.options.get(CONF_READBACK))
    # Default on: most lights report colour temperature correctly.
    poll_cct = entry.options.get(CONF_POLL_CCT, True)
    async_add_entities(
        (
            GodoxLight(
                data, node, entry_address, polling=polling, poll_cct=poll_cct
            )
            for node in data.nodes
        ),
        update_before_add=polling,
    )


class GodoxLight(LightEntity, RestoreEntity):
    """A Godox mesh light addressed by unicast address.

    Brightness and colour temperature can be polled from the light on stock
    firmware, when the user opts in; effect and effect speed never can, so those
    always show what was last commanded. State is restored across restarts so
    that a brightness change does not first have to guess a starting point.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(
        self,
        data: GodoxRuntimeData,
        node: GodoxNode,
        entry_address: str,
        *,
        polling: bool = False,
        poll_cct: bool = True,
    ) -> None:
        """Initialize the light."""
        self._data = data
        self._link = data.link
        self._node = node
        # Brightness readback works on stock firmware, so a polled light shows
        # real state rather than what was last commanded.
        self._attr_should_poll = polling
        self._attr_assumed_state = not polling
        self._poll_cct = poll_cct
        caps = node.capabilities
        # Controls come from the model's capabilities, not a hardcoded range: a
        # fixed-daylight light is brightness-only; a bi-colour light exposes its
        # own colour-temperature range.
        mode = caps.color_mode
        self._attr_supported_color_modes = {mode}
        self._attr_color_mode = mode
        if mode is ColorMode.COLOR_TEMP:
            self._attr_min_color_temp_kelvin = caps.min_kelvin
            self._attr_max_color_temp_kelvin = caps.max_kelvin
        # Effects are per-model too, and named: the catalogue says which ones a
        # light ships, so an SL200III Bi offers Lightning and Candle rather
        # than a fixed list of "Effect 3" across the whole range. A model with
        # no known effects advertises no effect support at all.
        self._effects = caps.effects
        if self._effects:
            self._attr_supported_features = LightEntityFeature.EFFECT
            # Home Assistant offers no other way to clear an effect, so the
            # list leads with an explicit off entry. Selecting it sends a
            # colour-temperature command, which the protocol treats as leaving
            # effect mode.
            self._attr_effect_list = [EFFECT_OFF] + [e.label for e in self._effects]
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = node_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == self._link.proxy_node_address
                else set()
            ),
            manufacturer=MANUFACTURER,
            model=caps.name or node.model,
            # Which of the three Telink mesh radios this light uses. Purely
            # informational, but it is the thing you need to know before doing
            # anything with its firmware.
            hw_version=caps.chip,
            name=node.name,
        )
        self._attr_is_on = False
        self._attr_brightness = 255
        if mode is ColorMode.COLOR_TEMP:
            self._attr_color_temp_kelvin = self._clamp_kelvin(DEFAULT_KELVIN)
        self._attr_effect = None

    async def async_added_to_hass(self) -> None:
        """Restore the last commanded state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        self._attr_is_on = last_state.state == STATE_ON
        if (brightness := last_state.attributes.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = int(brightness)
        if (
            ColorMode.COLOR_TEMP in self._attr_supported_color_modes
            and (kelvin := last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        ):
            self._attr_color_temp_kelvin = self._clamp_kelvin(int(kelvin))
        # Restore by resolving the name rather than matching the list, so a
        # state saved before effects were annotated with their speed count
        # still comes back as the same effect.
        restored_effect = last_state.attributes.get(ATTR_EFFECT)
        if restored_effect:
            effect = self._node.capabilities.effect_by_name(restored_effect)
            if effect is not None:
                self._attr_effect = effect.label
                # Share it, or the speed control comes back bound to the
                # model's widest range rather than this effect's.
                self._data.current_effect[self._node.address] = effect.label
                async_dispatcher_send(
                    self.hass,
                    SIGNAL_EFFECT_CHANGED.format(node_id=self._attr_unique_id),
                )

    def _effect_symbol(self, name: str) -> int:
        """Map a displayed effect name back to this model's wire symbol."""
        effect = self._node.capabilities.effect_by_name(name)
        if effect is None:
            raise HomeAssistantError(f"{name!r} is not a supported effect")
        return effect.symbol

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, and apply brightness, colour temperature or effect."""
        if (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            # Home Assistant does not clamp to the entity's advertised range,
            # and the library rejects anything outside it.
            self._attr_color_temp_kelvin = self._clamp_kelvin(int(kelvin))
            # Colour temperature and effects are mutually exclusive in the
            # protocol: a 0xF0 command takes the light out of effect mode.
            self._attr_effect = None
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = int(brightness)
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            self._attr_effect = None if effect == EFFECT_OFF else effect

        if not self._attr_is_on:
            await self._link.async_turn_on(self._node.address)

        brightness_pct = math.ceil(
            brightness_to_value(BRIGHTNESS_SCALE, self._attr_brightness or 255)
        )
        if self._attr_effect is not None:
            effect = self._node.capabilities.effect_by_name(self._attr_effect)
            # Speed comes from the separate number entity, clamped to what this
            # particular effect accepts -- they differ within one model.
            speed = min(
                self._data.effect_speeds.get(self._node.address, 0),
                effect.speed_max if effect else 0,
            )
            await self._link.async_set_effect(
                self._node.address,
                effect=self._effect_symbol(self._attr_effect),
                brightness_pct=brightness_pct,
                speed=speed,
            )
        else:
            caps = self._node.capabilities
            await self._link.async_set_light(
                self._node.address,
                brightness_pct=brightness_pct,
                kelvin=self._attr_color_temp_kelvin or caps.min_kelvin,
                min_kelvin=caps.min_kelvin,
                max_kelvin=caps.max_kelvin,
            )
        self._attr_is_on = True
        self.async_write_ha_state()
        # Tell the speed control which effect is running, so it can show that
        # effect's range rather than the model's widest.
        self._data.current_effect[self._node.address] = self._attr_effect
        async_dispatcher_send(
            self.hass, SIGNAL_EFFECT_CHANGED.format(node_id=self._attr_unique_id)
        )

    async def async_update(self) -> None:
        """Poll the light for its live state.

        What the light reports is shown as-is. Most lights report both fields
        accurately; a few send a colour temperature that is not their real
        setting after it is changed on the light's own controls. Rather than
        guess which is which from the wire format -- two attempts at that were
        wrong, and a wrong guess silently discards a good value with no way for
        the user to override it -- colour temperature can simply be switched
        off per entry.
        """
        try:
            status = await self._link.async_request_status(self._node.address)
        except HomeAssistantError as err:
            _LOGGER.debug("status poll for %s failed: %s", self._node.name, err)
            return
        if status.brightness:
            self._attr_brightness = value_to_brightness(
                BRIGHTNESS_SCALE, status.brightness
            )
            self._attr_is_on = True
        elif status.brightness == 0:
            self._attr_is_on = False
        if status.cct is not None and self._poll_cct:
            self._attr_color_temp_kelvin = self._clamp_kelvin(status.cct)

    def _clamp_kelvin(self, kelvin: int) -> int:
        """Clamp a colour temperature into this model's accepted range."""
        caps = self._node.capabilities
        return max(caps.min_kelvin, min(caps.max_kelvin, kelvin))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._link.async_turn_off(self._node.address)
        self._attr_is_on = False
        self.async_write_ha_state()




