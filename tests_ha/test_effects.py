"""Tests for exposing the vendor protocol's effects through the light entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh._lib.protocol import EFFECT_IDS
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    LightEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

ENTITY = "light.key_light"
BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"


@pytest.fixture
async def entry(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN, title="Key Light", unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [
            {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None}]},
    )
    e.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    return e


async def test_an_unknown_model_falls_back_to_a_numbered_set(
    hass: HomeAssistant, entry
) -> None:
    """With no model configured, offer the fallback set rather than nothing."""
    state = hass.states.get(ENTITY)

    assert state.attributes[ATTR_SUPPORTED_FEATURES] & LightEntityFeature.EFFECT
    effects = state.attributes[ATTR_EFFECT_LIST]
    # The list leads with an explicit off entry.
    assert effects[0] == "Off"
    assert len(effects) == len(EFFECT_IDS) + 1
    assert "Effect 2" not in effects


async def test_selecting_an_effect_sends_the_effect_command(
    hass: HomeAssistant, entry
) -> None:
    link = entry.runtime_data.link
    with patch.object(link, "async_set_effect", AsyncMock()) as set_effect:
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: ENTITY, ATTR_EFFECT: "Effect 4", ATTR_BRIGHTNESS: 128},
            blocking=True,
        )

    set_effect.assert_awaited_once()
    assert set_effect.await_args.args[0] == 2          # node address
    assert set_effect.await_args.kwargs["effect"] == 4
    assert set_effect.await_args.kwargs["brightness_pct"] == 51
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] == "Effect 4"


async def test_setting_colour_temperature_clears_the_effect(
    hass: HomeAssistant, entry
) -> None:
    """The two are mutually exclusive in the protocol, so the UI must follow."""
    from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN

    link = entry.runtime_data.link
    with patch.object(link, "async_set_effect", AsyncMock()):
        await hass.services.async_call(
            "light", "turn_on", {ATTR_ENTITY_ID: ENTITY, ATTR_EFFECT: "Effect 4"},
            blocking=True,
        )
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] == "Effect 4"

    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: ENTITY, ATTR_COLOR_TEMP_KELVIN: 4000},
        blocking=True,
    )

    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] is None


async def test_an_unknown_effect_name_is_rejected(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises((HomeAssistantError, ValueError)):
        await hass.services.async_call(
            "light", "turn_on", {ATTR_ENTITY_ID: ENTITY, ATTR_EFFECT: "Nonsense"},
            blocking=True,
        )


@pytest.fixture
async def known_model_entry(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
    """An entry whose node is a known model, so effects are named."""
    from custom_components.godox_mesh.const import CONF_RADIO_ID

    e = MockConfigEntry(
        domain=DOMAIN, title="Key Light", unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [
            {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_RADIO_ID: "003F"}]},
    )
    e.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    return e


async def test_a_known_model_offers_named_effects(
    hass: HomeAssistant, known_model_entry
) -> None:
    """An SL200III Bi offers Lightning and Candle, not "Effect 3"."""
    effects = hass.states.get(ENTITY).attributes[ATTR_EFFECT_LIST]

    # Multi-speed effects say how many; single-speed ones stay plain, so the
    # annotation carries information rather than decorating everything.
    assert "Lightning (2 gears)" in effects
    assert "Candle" in effects
    assert not any(name.startswith("Effect ") for name in effects[1:])


async def test_a_named_effect_sends_the_models_wire_symbol(
    hass: HomeAssistant, known_model_entry
) -> None:
    """'Lightning' is catalogue id 4, so wire symbol 3."""
    from custom_components.godox_mesh.mesh import GodoxMeshLink

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as set_effect:
        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_ENTITY_ID: ENTITY, ATTR_EFFECT: "Lightning", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    assert set_effect.await_args.kwargs["effect"] == 3
