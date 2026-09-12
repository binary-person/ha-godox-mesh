"""Tests for the Godox light platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)
from custom_components.godox_mesh._lib import GodoxController
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    CONF_ADDRESS,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

ENTITY = "light.key_light"


@pytest.fixture
def mock_commands():
    """Patch the library's command methods, leaving connection handling real."""
    with (
        patch.object(GodoxController, "set_params", AsyncMock()) as set_params,
        patch.object(GodoxController, "power_on", AsyncMock()) as power_on,
        patch.object(GodoxController, "power_off", AsyncMock()) as power_off,
    ):
        yield {"set_params": set_params, "power_on": power_on, "power_off": power_off}


async def test_light_entity_advertises_cct_support(
    hass: HomeAssistant, setup_entry
) -> None:
    """The light reports a single colour-temperature mode and the device's range."""
    state = hass.states.get(ENTITY)

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.COLOR_TEMP]
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 2800
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 6500
    # Nothing reports back, so Home Assistant must show the state as assumed.
    assert state.attributes[ATTR_ASSUMED_STATE] is True


async def test_turn_on_sends_brightness_and_kelvin(
    hass: HomeAssistant, setup_entry, mock_commands
) -> None:
    """Full brightness maps to 100 percent on the device's scale."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, ATTR_BRIGHTNESS: 255, ATTR_COLOR_TEMP_KELVIN: 4000},
        blocking=True,
    )

    # The model's colour-temperature range goes with the command: the library
    # bounds by the protocol otherwise, so a wide-range light is not clamped to
    # the range of whichever light this was developed against.
    mock_commands["set_params"].assert_awaited_once_with(
        brightness=100, cct=4000, dst=2, min_kelvin=2800, max_kelvin=6500
    )
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_brightness_is_scaled_to_percent(
    hass: HomeAssistant, setup_entry, mock_commands
) -> None:
    """Mid brightness lands inside the device's 1-100 range, never 0."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    percent = mock_commands["set_params"].await_args.kwargs["brightness"]
    assert 1 <= percent <= 100
    assert percent == 51


async def test_lowest_brightness_never_reaches_zero(
    hass: HomeAssistant, setup_entry, mock_commands
) -> None:
    """Brightness 1 must not become 0 percent, which the device reads as off."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, ATTR_BRIGHTNESS: 1},
        blocking=True,
    )

    assert mock_commands["set_params"].await_args.kwargs["brightness"] == 1


@pytest.mark.parametrize(
    ("requested", "expected"), [(1000, 2800), (2800, 2800), (6500, 6500), (9000, 6500)]
)
async def test_kelvin_is_clamped_to_the_device_range(
    hass: HomeAssistant, setup_entry, mock_commands, requested, expected
) -> None:
    """Home Assistant does not clamp for us and the library rejects out-of-range."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, ATTR_COLOR_TEMP_KELVIN: requested},
        blocking=True,
    )

    assert mock_commands["set_params"].await_args.kwargs["cct"] == expected


async def test_turn_on_with_no_arguments_reuses_last_values(
    hass: HomeAssistant, setup_entry, mock_commands
) -> None:
    """A bare turn_on re-sends what was last commanded rather than guessing."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 3200},
        blocking=True,
    )
    await hass.services.async_call(
        "light", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    mock_commands["set_params"].reset_mock()

    await hass.services.async_call(
        "light", SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )

    assert mock_commands["set_params"].await_args.kwargs["cct"] == 3200
    assert mock_commands["set_params"].await_args.kwargs["brightness"] == 51


async def test_turn_off(hass: HomeAssistant, setup_entry, mock_commands) -> None:
    """Turning off sends the power-off command to the right node."""
    await hass.services.async_call(
        "light", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )

    mock_commands["power_off"].assert_awaited_once_with(dst=2)
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_multiple_nodes_share_one_connection(
    hass: HomeAssistant, fake_ble, mock_commands
) -> None:
    """Two lights on one mesh network fan out by unicast address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None},
                {CONF_NODE_ADDRESS: 3, CONF_NAME: "Fill Light", CONF_MODEL: None},
            ]
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get("light.key_light") is not None
        assert hass.states.get("light.fill_light") is not None

        await hass.services.async_call(
            "light",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.fill_light", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    assert mock_commands["set_params"].await_args.kwargs["dst"] == 3
    # One shared proxy connection, not one per light.
    assert len(fake_ble) == 1
