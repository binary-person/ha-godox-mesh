"""Fan speed select: present only for models with controllable fan speeds."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
)
from custom_components.godox_mesh.mesh import GodoxMeshLink
from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
ENTITY = "select.light_fan_speed"

# M600D has controllable fan speeds; the SL200III Bi has a fan but no control.
WITH_FAN = "000A"
WITHOUT_FAN = "003F"


def _entry(radio_id: str) -> MockConfigEntry:
    return MockConfigEntry(
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


async def _setup(hass: HomeAssistant, radio_id: str) -> MockConfigEntry:
    entry = _entry(radio_id)
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_model_without_fan_control_gets_no_entity(hass: HomeAssistant) -> None:
    """A fan the protocol cannot drive must not appear as a control."""
    await _setup(hass, WITHOUT_FAN)
    assert hass.states.get(ENTITY) is None


@pytest.mark.usefixtures("fake_ble")
async def test_fan_model_lists_its_own_speeds(hass: HomeAssistant) -> None:
    """The options come from the model, not from one list shared by all."""
    await _setup(hass, WITH_FAN)

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes[ATTR_OPTIONS] == ["Silent", "Automatic", "Low", "High"]


@pytest.mark.usefixtures("fake_ble")
async def test_selecting_a_speed_sends_its_wire_code(hass: HomeAssistant) -> None:
    """'High' is wire code 3, which is not its position in the list."""
    await _setup(hass, WITH_FAN)

    with patch.object(
        GodoxMeshLink, "async_set_fan_mode", new=AsyncMock()
    ) as set_fan:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: ENTITY, ATTR_OPTION: "High"},
            blocking=True,
        )

    set_fan.assert_awaited_once_with(2, 3)
    assert hass.states.get(ENTITY).state == "High"


@pytest.mark.usefixtures("fake_ble")
async def test_medium_speed_uses_code_four(hass: HomeAssistant) -> None:
    """Code 4 sits between Low (2) and High (3); it was missing from the library."""
    await _setup(hass, "004B")  # MG1200Bi: Silent / Automatic / Medium / High

    assert "Medium" in hass.states.get(ENTITY).attributes[ATTR_OPTIONS]

    with patch.object(
        GodoxMeshLink, "async_set_fan_mode", new=AsyncMock()
    ) as set_fan:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: ENTITY, ATTR_OPTION: "Medium"},
            blocking=True,
        )

    set_fan.assert_awaited_once_with(2, 4)
