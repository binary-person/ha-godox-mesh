"""Select controls for Godox lights: fan speed, and lighting gels.

Fan speed
---------

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

from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_CCT_RANGE_CHANGED
from .mesh import GodoxMeshLink
from .models import GodoxConfigEntry, GodoxNode, GodoxRuntimeData

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GodoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the select controls each node's model actually has."""
    data = entry.runtime_data
    entry_address = entry.unique_id or entry.data[CONF_ADDRESS]
    entities: list[SelectEntity] = []
    for node in data.nodes:
        caps = node.capabilities
        if caps.fan_modes:
            entities.append(GodoxFanModeSelect(data.link, node, entry_address))
        if caps.color_chips:
            entities.append(GodoxColorChipSelect(data, node, entry_address))
        if caps.control_modes:
            entities.append(
                GodoxControlModeSelect(data, node, entry_address, axis="mode")
            )
        if caps.frequencies:
            entities.append(
                GodoxControlModeSelect(data, node, entry_address, axis="frequency")
            )
        if caps.smoothness_modes:
            entities.append(GodoxSmoothnessSelect(data, node, entry_address))
        if caps.has_selfie_cct:
            entities.append(GodoxCctRangeSelect(data, node, entry_address))
    async_add_entities(entities)


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


class GodoxColorChipSelect(SelectEntity, RestoreEntity):
    """Lighting gel the light emulates.

    Godox lights can reproduce a lighting gel from a built-in catalogue, and
    the command names one by brand and number rather than by colour -- so the
    list has to come from Godox's own gel catalogue, which is fetched by
    ``reverse-artifacts/refresh.py`` and compiled into ``color_chips_data.json``.
    Which catalogue version a model uses, and therefore which frame it takes,
    is in the capability table.

    Selecting a gel puts the light into gel mode, which replaces whatever
    colour or colour temperature it was showing. Nothing reports it back, so
    this is an ``assumed_state`` control that restores across restarts.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "color_chip"
    _attr_assumed_state = True

    def __init__(
        self, data: GodoxRuntimeData, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the gel control from this model's own catalogue."""
        self._data = data
        self._node = node
        self._chips = node.capabilities.color_chips
        self._attr_options = [chip.label for chip in self._chips]
        node_id = f"{entry_address}_{node.address:04x}"
        self._light_unique_id = node_id
        self._attr_unique_id = f"{node_id}_color_chip"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == data.link.proxy_node_address
                else set()
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last gel, since the light cannot report it."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            self._data.color_chips[self._node.address] = last_state.state

    async def async_select_option(self, option: str) -> None:
        """Send the selected gel to the light."""
        chip = self._node.capabilities.color_chip_by_label(option)
        if chip is None:
            raise HomeAssistantError(f"{option!r} is not a gel this model has")
        caps = self._node.capabilities
        await self._data.link.async_set_color_chip(
            self._node.address,
            brand=chip.brand,
            number=chip.number,
            sub_brand=chip.sub_brand,
            version=caps.color_chip_version,
            # Brightness follows the light, so picking a gel does not also
            # change how bright the room is.
            brightness_pct=self._data.brightness_pct.get(self._node.address, 100.0),
        )
        self._attr_current_option = option
        self._data.color_chips[self._node.address] = option
        self.async_write_ha_state()


class GodoxControlModeSelect(SelectEntity, RestoreEntity):
    """Output profile, or the mains frequency it is tuned against.

    Two entities, one command. ``changeControlModeParam`` carries the mode and
    the frequency in a single frame, which is why the catalogue lists the same
    ten models under ``controlMode`` and ``frequency`` -- so each control
    records its half and sends the pair.

    *Normal* / *Low End* / *Highspeed* trade flicker-free output at high shutter
    speeds against dimming range at the bottom end. The frequency tunes the PWM
    so it does not beat against a camera's shutter.
    """

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        data: GodoxRuntimeData,
        node: GodoxNode,
        entry_address: str,
        *,
        axis: str,
    ) -> None:
        """Initialize one half of the control-mode pair."""
        self._data = data
        self._node = node
        self._axis = axis
        caps = node.capabilities
        self._options = caps.control_modes if axis == "mode" else caps.frequencies
        self._attr_translation_key = (
            "control_mode" if axis == "mode" else "mains_frequency"
        )
        self._attr_options = [option.name for option in self._options]
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = f"{node_id}_control_{axis}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == data.link.proxy_node_address
                else set()
            ),
        )

    def _pair(self) -> tuple[int, int]:
        """The node's (mode, frequency), defaulting to each list's first entry."""
        default = (
            self._node.capabilities.control_modes[0].code
            if self._node.capabilities.control_modes
            else 0,
            self._node.capabilities.frequencies[0].code
            if self._node.capabilities.frequencies
            else 0,
        )
        return self._data.control_mode.get(self._node.address, default)

    async def async_added_to_hass(self) -> None:
        """Restore the last selection, since nothing reports it back."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state not in self._attr_options:
            return
        self._attr_current_option = last_state.state
        option = next(o for o in self._options if o.name == last_state.state)
        mode, frequency = self._pair()
        self._data.control_mode[self._node.address] = (
            (option.code, frequency) if self._axis == "mode" else (mode, option.code)
        )

    async def async_select_option(self, option: str) -> None:
        """Record this half and send the pair."""
        chosen = self._node.capabilities.option_by_name(self._options, option)
        if chosen is None:
            raise HomeAssistantError(f"{option!r} is not available on this model")
        mode, frequency = self._pair()
        if self._axis == "mode":
            mode = chosen.code
        else:
            frequency = chosen.code
        self._data.control_mode[self._node.address] = (mode, frequency)
        await self._data.link.async_set_control_mode(
            self._node.address, mode=mode, frequency=frequency
        )
        self._attr_current_option = option
        self.async_write_ha_state()


class GodoxSmoothnessSelect(SelectEntity, RestoreEntity):
    """How the light ramps between levels.

    *Smooth* fades; *OFF* steps immediately, which is what an automation
    driving the light from a sensor usually wants.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "smoothness"
    _attr_assumed_state = True

    def __init__(
        self, data: GodoxRuntimeData, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the smoothness control."""
        self._data = data
        self._node = node
        self._options = node.capabilities.smoothness_modes
        self._attr_options = [option.name for option in self._options]
        node_id = f"{entry_address}_{node.address:04x}"
        self._attr_unique_id = f"{node_id}_smoothness"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == data.link.proxy_node_address
                else set()
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last selection, since nothing reports it back."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in self._attr_options:
            self._attr_current_option = last_state.state

    async def async_select_option(self, option: str) -> None:
        """Send the selected smoothness to the light."""
        chosen = self._node.capabilities.option_by_name(self._options, option)
        if chosen is None:
            raise HomeAssistantError(f"{option!r} is not available on this model")
        await self._data.link.async_set_smoothness(self._node.address, chosen.code)
        self._attr_current_option = option
        self.async_write_ha_state()


#: Names for the two colour-temperature ranges. "Selfie" is Godox's own word
#: for the narrower one, kept so it matches the vendor app.
RANGE_NORMAL = "Normal"
RANGE_SELFIE = "Selfie"


class GodoxCctRangeSelect(SelectEntity, RestoreEntity):
    """Which of a light's two colour-temperature ranges is in use.

    The MA5R and MA5R Plus carry a second, narrower range alongside their main
    one, reached by its own command rather than by ``0xF0``. A Home Assistant
    light has exactly one colour-temperature range, so rather than a second
    light entity this swaps the bounds the existing one advertises -- the
    slider re-scales, and the light entity picks the matching command.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cct_range"
    _attr_assumed_state = True
    _attr_options = [RANGE_NORMAL, RANGE_SELFIE]

    def __init__(
        self, data: GodoxRuntimeData, node: GodoxNode, entry_address: str
    ) -> None:
        """Initialize the range selector."""
        self._data = data
        self._node = node
        self._attr_current_option = RANGE_NORMAL
        node_id = f"{entry_address}_{node.address:04x}"
        self._light_unique_id = node_id
        self._attr_unique_id = f"{node_id}_cct_range"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            connections=(
                {(dr.CONNECTION_BLUETOOTH, entry_address)}
                if node.address == data.link.proxy_node_address
                else set()
            ),
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Both ranges, so the choice says what it will do before you make it."""
        caps = self._node.capabilities
        return {
            "normal_range_kelvin": [caps.min_kelvin, caps.max_kelvin],
            "selfie_range_kelvin": [
                caps.selfie_min_kelvin,
                caps.selfie_max_kelvin,
            ],
        }

    async def async_added_to_hass(self) -> None:
        """Restore the last range, since nothing reports it back."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        if last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            self._data.selfie[self._node.address] = (
                last_state.state == RANGE_SELFIE
            )

    async def async_select_option(self, option: str) -> None:
        """Swap the range the light advertises and re-send on the new command."""
        if option not in self._attr_options:
            raise HomeAssistantError(f"{option!r} is not a colour-temperature range")
        self._data.selfie[self._node.address] = option == RANGE_SELFIE
        self._attr_current_option = option
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass,
            SIGNAL_CCT_RANGE_CHANGED.format(node_id=self._light_unique_id),
        )
