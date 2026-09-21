"""The light entity's controls are driven by the node's model capabilities."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
)
from homeassistant.components.light import (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"


async def _setup(hass: HomeAssistant, radio_id: str | None) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Light", CONF_RADIO_ID: radio_id}
            ]
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.usefixtures("fake_ble")
async def test_wide_range_model_uses_its_own_cct_range(hass: HomeAssistant) -> None:
    """A wide-range light keeps its real range instead of the old hardcoded one."""
    await _setup(hass, "00B6")  # SL200 RF, 1800-10000 K
    state = hass.states.get("light.light")
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 1800
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 10000


@pytest.mark.usefixtures("fake_ble")
async def test_bicolour_model_is_colour_temperature_only(hass: HomeAssistant) -> None:
    """A model whose catalogue lists no colour mode gets no colour wheel."""
    await _setup(hass, "003F")  # SL200III Bi, modeType [cct, effects]
    state = hass.states.get("light.light")
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.COLOR_TEMP]


@pytest.mark.usefixtures("fake_ble")
async def test_full_colour_model_offers_colour_modes(hass: HomeAssistant) -> None:
    """A full-colour model gets hue/saturation and direct channels.

    This is the regression that mattered: the SL200 RF's catalogue entry lists
    HSI, RGB and xy, and the integration rendered it as a colour-temperature
    light for its whole first release -- an earlier version of this very test
    asserted that, calling the model "bicolour".
    """
    await _setup(hass, "00B6")
    state = hass.states.get("light.light")
    assert set(state.attributes[ATTR_SUPPORTED_COLOR_MODES]) == {
        ColorMode.COLOR_TEMP,
        ColorMode.HS,
        ColorMode.RGBW,
    }


@pytest.mark.usefixtures("fake_ble")
async def test_tint_control_only_on_models_with_a_range(hass: HomeAssistant) -> None:
    """Green/magenta is a separate number, and only where the model has it."""
    await _setup(hass, "00B6")  # +/-100
    tint = hass.states.get("number.light_green_magenta")
    assert tint is not None
    assert tint.attributes["min"] == -100
    assert tint.attributes["max"] == 100


@pytest.mark.usefixtures("fake_ble")
async def test_no_tint_control_without_a_range(hass: HomeAssistant) -> None:
    """A model with a 0/0 tint range gets no slider at all."""
    await _setup(hass, "003F")
    assert hass.states.get("number.light_green_magenta") is None


@pytest.mark.usefixtures("fake_ble")
async def test_daylight_model_is_brightness_only(hass: HomeAssistant) -> None:
    """A fixed-CCT light offers brightness, not a colour-temperature slider."""
    await _setup(hass, "000E")  # SL100D, 5600 K fixed
    state = hass.states.get("light.light")
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]
    assert ATTR_MIN_COLOR_TEMP_KELVIN not in state.attributes


@pytest.mark.usefixtures("fake_ble")
async def test_unknown_model_still_works(hass: HomeAssistant) -> None:
    """An unrecognised radioId falls back to a working colour-temperature light."""
    await _setup(hass, None)
    state = hass.states.get("light.light")
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.COLOR_TEMP]
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 2800
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 6500


@pytest.mark.usefixtures("fake_ble")
async def test_daylight_model_rejects_out_of_range_via_brightness_only(
    hass: HomeAssistant,
) -> None:
    """Setting a daylight light's brightness works; there is no CCT to set."""
    from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
    from homeassistant.components.light import ATTR_BRIGHTNESS

    await _setup(hass, "000E")
    await hass.services.async_call(
        "light", SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.light", ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    assert hass.states.get("light.light").state == "on"


@pytest.mark.usefixtures("fake_ble")
async def test_a_wide_range_light_can_be_commanded_across_its_range(
    hass: HomeAssistant,
) -> None:
    """A 1800-10000 K model must be commandable at both ends.

    The library once hardcoded 2800-6500 K -- the range of the two lights this
    was developed against -- which rejected valid commands for 93 of the 184
    known models.
    """
    from unittest.mock import AsyncMock

    from custom_components.godox_mesh.capabilities import capabilities_for_radio_id
    from custom_components.godox_mesh.mesh import GodoxMeshLink

    from custom_components.godox_mesh.capabilities import known_models

    wide = next(
        rid
        for rid, c in known_models().items()
        if c.min_kelvin <= 1800 and c.max_kelvin >= 10000
    )
    caps = capabilities_for_radio_id(wide)
    await _setup(hass, wide)

    for kelvin in (caps.min_kelvin, caps.max_kelvin):
        with patch.object(GodoxMeshLink, "async_set_light", new=AsyncMock()) as send:
            await hass.services.async_call(
                "light",
                "turn_on",
                {ATTR_ENTITY_ID: "light.light", ATTR_COLOR_TEMP_KELVIN: kelvin},
                blocking=True,
            )
        assert send.await_args.kwargs["kelvin"] == kelvin
