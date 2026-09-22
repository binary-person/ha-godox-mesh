"""Light platform for Godox Bluetooth Mesh lights."""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_ADDRESS, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.color import brightness_to_value, value_to_brightness

from ._lib.protocol import RGB16_MAX, RGB8_MAX, RGB_TYPE_RGBW, RGB_TYPE_RGBWW
from .const import (
    BRIGHTNESS_SCALE,
    DOMAIN,
    EFFECT_OFF,
    FAILED_POLLS_BEFORE_UNAVAILABLE,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    SIGNAL_EFFECT_CHANGED,
    SIGNAL_EFFECT_SPEED_CHANGED,
    SIGNAL_CCT_RANGE_CHANGED,
    SIGNAL_TINT_CHANGED,
    SIGNAL_XY_CHANGED,
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
    # Readback/poll settings are per-node now (on GodoxNode); the light reads
    # them itself and drives its own poll timer, so nothing entry-wide here.
    async_add_entities(
        GodoxLight(data, node, entry_address) for node in data.nodes
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
    ) -> None:
        """Initialize the light."""
        self._data = data
        self._link = data.link
        self._node = node
        # Readback is per-light and driven by a timer (see async_added_to_hass),
        # not Home Assistant's poll loop -- so a light without it shows what was
        # last commanded (assumed state).
        self._attr_assumed_state = not node.readback
        # Consecutive failed polls. A polled light that stops answering is shown
        # unavailable after a few; an un-polled light never polls, so it has no
        # availability signal and stays available (the default).
        self._poll_failures = 0
        self._poll_cct = node.poll_cct
        caps = node.capabilities
        # Controls come from the model's capabilities, not a hardcoded range: a
        # fixed-daylight light is brightness-only, a bi-colour light exposes its
        # own colour-temperature range, and a full-colour light additionally
        # gets hue/saturation and, where the model has them, direct channels.
        modes = caps.color_modes_for(use_xy=node.use_xy)
        mode = caps.color_mode if ColorMode.XY not in modes else (
            ColorMode.COLOR_TEMP if ColorMode.COLOR_TEMP in modes else ColorMode.XY
        )
        self._attr_supported_color_modes = modes
        self._attr_color_mode = mode
        self._supports_cct = ColorMode.COLOR_TEMP in modes
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
        if self._supports_cct:
            self._attr_color_temp_kelvin = self._clamp_kelvin(DEFAULT_KELVIN)
        if ColorMode.HS in modes:
            self._attr_hs_color = (0.0, 0.0)
        if ColorMode.XY in modes:
            # D65, the same neutral the vendor app's xy screen opens on.
            self._attr_xy_color = (0.3127, 0.3290)
        self._attr_effect = None

    async def async_added_to_hass(self) -> None:
        """Restore the last commanded state, and follow tint changes."""
        await super().async_added_to_hass()
        if self._node.capabilities.has_tint:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_TINT_CHANGED.format(node_id=self._attr_unique_id),
                    self._tint_changed,
                )
            )
        if self._node.capabilities.has_selfie_cct:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_CCT_RANGE_CHANGED.format(node_id=self._attr_unique_id),
                    self._cct_range_changed,
                )
            )
        if ColorMode.XY in self._attr_supported_color_modes:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_XY_CHANGED.format(node_id=self._attr_unique_id),
                    self._coordinate_changed,
                )
            )
        if self._effects:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_EFFECT_SPEED_CHANGED.format(node_id=self._attr_unique_id),
                    self._effect_speed_changed,
                )
            )
        if self._node.readback:
            # Poll on a per-light timer rather than Home Assistant's platform
            # loop, so each light can have its own interval. Poll once now for
            # an immediate value (this replaces update_before_add).
            await self.async_update()
            interval = timedelta(
                seconds=max(
                    MIN_POLL_INTERVAL,
                    min(MAX_POLL_INTERVAL, self._node.poll_interval),
                )
            )
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._async_interval_poll, interval
                )
            )
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
        # Colour has to come back too, and with the mode that produced it: a
        # light restored into COLOR_TEMP after the user left it on a colour
        # would jump back to white on the next brightness change.
        if (
            ColorMode.HS in self._attr_supported_color_modes
            and (hs := last_state.attributes.get(ATTR_HS_COLOR)) is not None
        ):
            self._attr_hs_color = (float(hs[0]), float(hs[1]))
        if (
            ColorMode.RGBW in self._attr_supported_color_modes
            and (rgbw := last_state.attributes.get(ATTR_RGBW_COLOR)) is not None
        ):
            self._attr_rgbw_color = tuple(int(c) for c in rgbw)  # type: ignore[assignment]
        if (
            ColorMode.RGBWW in self._attr_supported_color_modes
            and (rgbww := last_state.attributes.get(ATTR_RGBWW_COLOR)) is not None
        ):
            self._attr_rgbww_color = tuple(int(c) for c in rgbww)  # type: ignore[assignment]
        if (
            ColorMode.XY in self._attr_supported_color_modes
            and (xy := last_state.attributes.get(ATTR_XY_COLOR)) is not None
        ):
            self._attr_xy_color = (float(xy[0]), float(xy[1]))
            self._data.xy[self._node.address] = self._attr_xy_color
        restored_mode = last_state.attributes.get(ATTR_COLOR_MODE)
        if restored_mode in {m.value for m in self._attr_supported_color_modes}:
            self._attr_color_mode = ColorMode(restored_mode)
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

    @callback
    def _coordinate_changed(self) -> None:
        """Re-send the xy frame when one of the coordinate sliders moves.

        The same signal is what tells the sliders to follow a colour set on the
        light, so it comes back here after this entity fired it. Comparing
        against what this entity already holds is what stops that becoming a
        loop: a pair this light just sent needs no resending.
        """
        if not self._attr_is_on:
            return
        stored = self._data.xy.get(self._node.address)
        if stored is None or stored == self._attr_xy_color:
            return
        self._attr_xy_color = stored
        self._attr_color_mode = ColorMode.XY
        self._attr_effect = None
        self.async_write_ha_state()
        self.hass.async_create_task(self._async_send_color(self._brightness_pct()))

    @callback
    def _tint_changed(self) -> None:
        """Re-send the colour-temperature frame so a new tint takes effect."""
        if not self._attr_is_on or self._attr_color_mode is not ColorMode.COLOR_TEMP:
            return
        self.hass.async_create_task(self._async_send_color(self._brightness_pct()))

    @callback
    def _effect_speed_changed(self) -> None:
        """Re-send the running effect so a new speed takes effect immediately.

        Speed rides the effect frame, so -- like tint -- it applies only when
        that frame is sent again; without this the slider would do nothing until
        the effect was picked afresh.
        """
        if not self._attr_is_on or self._attr_effect is None:
            return
        self.hass.async_create_task(self._async_send_effect(self._brightness_pct()))

    def _effect_symbol(self, name: str) -> int:
        """Map a displayed effect name back to this model's wire symbol."""
        effect = self._node.capabilities.effect_by_name(name)
        if effect is None:
            raise HomeAssistantError(f"{name!r} is not a supported effect")
        return effect.symbol

    @property
    def _selfie(self) -> bool:
        """Whether this light is currently in its selfie range."""
        return bool(self._data.selfie.get(self._node.address))

    @property
    def min_color_temp_kelvin(self) -> int:
        """Low bound of whichever colour-temperature range is selected.

        Two models carry a second, narrower range reached by its own command.
        Home Assistant reads the bounds out of ``capability_attributes`` on
        every state write rather than caching them at registration, so swapping
        them at runtime works and the slider re-scales.
        """
        caps = self._node.capabilities
        if self._selfie and caps.has_selfie_cct:
            return caps.selfie_min_kelvin
        return caps.min_kelvin

    @property
    def max_color_temp_kelvin(self) -> int:
        """High bound of whichever colour-temperature range is selected."""
        caps = self._node.capabilities
        if self._selfie and caps.has_selfie_cct:
            return caps.selfie_max_kelvin
        return caps.max_kelvin

    @callback
    def _cct_range_changed(self) -> None:
        """Re-clamp and re-send after a switch between the two ranges."""
        self._attr_color_temp_kelvin = self._clamp_kelvin(
            self._attr_color_temp_kelvin or self.min_color_temp_kelvin
        )
        self._attr_color_mode = ColorMode.COLOR_TEMP
        self._attr_effect = None
        self.async_write_ha_state()
        if self._attr_is_on:
            self.hass.async_create_task(
                self._async_send_color(self._brightness_pct())
            )

    def _brightness_pct(self) -> float:
        """Brightness as the percentage the wire carries.

        Home Assistant's 0-255 is finer than whole percent but coarser than the
        tenths the protocol can encode, so on a model that accepts tenths the
        value is kept fractional rather than rounded up to the next percent.
        Models that only take whole percent still round up, which is what they
        did before: it guarantees a non-zero brightness never becomes an off.
        """
        raw = brightness_to_value(BRIGHTNESS_SCALE, self._attr_brightness or 255)
        if self._node.capabilities.brightness_steps == 1000:
            return round(raw, 1)
        return float(math.ceil(raw))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, and apply brightness, colour or effect.

        The protocol has one command per colour mode and they are mutually
        exclusive: sending any of them takes the light out of whichever mode it
        was in, effects included. So exactly one is chosen here, from whichever
        attribute the service call carried.
        """
        if (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            # Home Assistant does not clamp to the entity's advertised range,
            # and the library rejects anything outside it.
            self._attr_color_temp_kelvin = self._clamp_kelvin(int(kelvin))
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_effect = None
        if (hs_color := kwargs.get(ATTR_HS_COLOR)) is not None:
            self._attr_hs_color = (float(hs_color[0]), float(hs_color[1]))
            self._attr_color_mode = ColorMode.HS
            self._attr_effect = None
        if (rgbw := kwargs.get(ATTR_RGBW_COLOR)) is not None:
            self._attr_rgbw_color = tuple(int(c) for c in rgbw)  # type: ignore[assignment]
            self._attr_color_mode = ColorMode.RGBW
            self._attr_effect = None
        if (xy_color := kwargs.get(ATTR_XY_COLOR)) is not None:
            self._attr_xy_color = (float(xy_color[0]), float(xy_color[1]))
            self._data.xy[self._node.address] = self._attr_xy_color
            self._attr_color_mode = ColorMode.XY
            self._attr_effect = None
        if (rgbww := kwargs.get(ATTR_RGBWW_COLOR)) is not None:
            self._attr_rgbww_color = tuple(int(c) for c in rgbww)  # type: ignore[assignment]
            self._attr_color_mode = ColorMode.RGBWW
            self._attr_effect = None
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = int(brightness)
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            self._attr_effect = None if effect == EFFECT_OFF else effect

        if not self._attr_is_on:
            await self._link.async_turn_on(self._node.address)

        brightness_pct = self._brightness_pct()
        # The gel control's command carries brightness too, so it needs to know
        # what the light is at or picking a gel would jump it to full.
        self._data.brightness_pct[self._node.address] = brightness_pct
        if self._attr_effect is not None:
            await self._async_send_effect(brightness_pct)
        else:
            await self._async_send_color(brightness_pct)
        self._attr_is_on = True
        # A successful command is proof the light is reachable, so reset the
        # failed-poll strike count -- this keeps a light that answers commands
        # but is slow to answer a status poll from drifting to unavailable. It
        # cannot revive an already-unavailable entity: Home Assistant drops
        # service calls to those before they reach here, so recovery from
        # unavailable is via a successful poll.
        self._mark_reachable()
        self.async_write_ha_state()
        # Tell the speed control which effect is running, so it can show that
        # effect's range rather than the model's widest.
        self._data.current_effect[self._node.address] = self._attr_effect
        async_dispatcher_send(
            self.hass, SIGNAL_EFFECT_CHANGED.format(node_id=self._attr_unique_id)
        )
        if self._attr_color_mode is ColorMode.XY:
            # The coordinate sliders read the shared pair, so they have to be
            # told when a colour set on the light -- or on the colour wheel --
            # moved it underneath them.
            async_dispatcher_send(
                self.hass, SIGNAL_XY_CHANGED.format(node_id=self._attr_unique_id)
            )

    async def _async_send_effect(self, brightness_pct: float) -> None:
        """Send the current effect at the current speed."""
        if self._attr_effect is None:
            return
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
            effect_version=self._node.capabilities.effect_version,
        )

    async def _async_send_color(self, brightness_pct: float) -> None:
        """Send whichever colour command matches the light's current mode."""
        caps = self._node.capabilities
        mode = self._attr_color_mode

        if mode is ColorMode.HS and self._attr_hs_color is not None:
            hue, saturation = self._attr_hs_color
            # The wire units are Home Assistant's own: degrees and percent.
            await self._link.async_set_hsi(
                self._node.address,
                hue=round(hue),
                saturation=round(saturation),
                brightness_pct=brightness_pct,
            )
            return

        if mode is ColorMode.XY and self._attr_xy_color is not None:
            x, y = self._attr_xy_color
            self._data.xy[self._node.address] = (x, y)
            await self._link.async_set_xy(
                self._node.address, x=x, y=y, brightness_pct=brightness_pct
            )
            return

        if mode is ColorMode.RGBW and self._attr_rgbw_color is not None:
            red, green, blue, white = self._attr_rgbw_color
            await self._async_send_channels(
                (red, green, blue), (white, 0, 0), RGB_TYPE_RGBW, brightness_pct
            )
            return

        if mode is ColorMode.RGBWW and self._attr_rgbww_color is not None:
            red, green, blue, cold, warm = self._attr_rgbww_color
            await self._async_send_channels(
                (red, green, blue), (cold, warm, 0), RGB_TYPE_RGBWW, brightness_pct
            )
            return

        kelvin = self._attr_color_temp_kelvin or self.min_color_temp_kelvin
        if self._selfie and caps.has_selfie_cct:
            # The selfie range has its own command; the 0xF0 frame drives the
            # main range only.
            await self._link.async_set_selfie_cct(
                self._node.address, brightness_pct=brightness_pct, kelvin=kelvin
            )
            return
        await self._link.async_set_light(
            self._node.address,
            brightness_pct=brightness_pct,
            kelvin=kelvin,
            min_kelvin=self.min_color_temp_kelvin,
            max_kelvin=self.max_color_temp_kelvin,
            gm=self._data.tints.get(self._node.address, 0),
            supports_gm=caps.has_tint,
        )

    async def _async_send_channels(
        self,
        rgb: tuple[int, int, int],
        extra: tuple[int, int, int],
        rgb_type: int,
        brightness_pct: float,
    ) -> None:
        """Send direct channel values in whichever format this model takes.

        Home Assistant always hands over 0-255 per channel. Models whose
        ``rgbDisplay`` is 1 or 2 take sixteen-bit values scaled to 0-1000
        instead, so those are rescaled here rather than in the protocol layer,
        which stays a faithful record of the wire format.
        """
        caps = self._node.capabilities
        wide = caps.rgb_display != 0
        if wide:
            scale = RGB16_MAX / RGB8_MAX
            rgb = tuple(round(c * scale) for c in rgb)  # type: ignore[assignment]
            extra = tuple(round(c * scale) for c in extra)  # type: ignore[assignment]
        red, green, blue = rgb
        await self._link.async_set_rgbw(
            self._node.address,
            red=red,
            green=green,
            blue=blue,
            white=extra[0],
            brightness_pct=brightness_pct,
            wide=wide,
            rgb_type=rgb_type,
            extra=extra,
        )

    def _mark_reachable(self) -> None:
        """Record that the light just answered, clearing any unavailability."""
        self._poll_failures = 0
        self._attr_available = True

    async def _async_interval_poll(self, _now: object = None) -> None:
        """Timer callback: poll, then publish. should_poll is off, so the
        write is ours to make."""
        await self.async_update()
        self.async_write_ha_state()

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
            # A light answers its own status request over the mesh, so a run of
            # no-answers means it is off or out of range -- show it unavailable.
            self._poll_failures += 1
            if self._poll_failures >= FAILED_POLLS_BEFORE_UNAVAILABLE:
                self._attr_available = False
            _LOGGER.debug("status poll for %s failed: %s", self._node.name, err)
            return
        self._mark_reachable()
        if status.brightness:
            self._attr_brightness = value_to_brightness(
                BRIGHTNESS_SCALE, status.brightness
            )
            self._attr_is_on = True
        elif status.brightness == 0:
            self._attr_is_on = False
        # Only meaningful while the light is actually in colour-temperature
        # mode; the 0xA0 record's second byte is the effect symbol otherwise,
        # and the library has already declined to read it as Kelvin.
        if (
            status.cct is not None
            and self._poll_cct
            and self._attr_color_mode is ColorMode.COLOR_TEMP
        ):
            self._attr_color_temp_kelvin = self._clamp_kelvin(status.cct)

    def _clamp_kelvin(self, kelvin: int) -> int:
        """Clamp a colour temperature into the range currently selected."""
        return max(
            self.min_color_temp_kelvin, min(self.max_color_temp_kelvin, kelvin)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._link.async_turn_off(self._node.address)
        self._attr_is_on = False
        self._mark_reachable()
        self.async_write_ha_state()




