"""Battery sensor: gated behind readback opt-in and a battery-powered model."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    CONF_READBACK,
    DOMAIN,
)
from custom_components.godox_mesh.mesh import GodoxMeshLink
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"


def _entry(readback: bool, radio_id: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Light", CONF_RADIO_ID: radio_id}
            ],
            CONF_READBACK: readback,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.usefixtures("fake_ble")
async def test_no_battery_sensor_without_readback(hass: HomeAssistant) -> None:
    """A battery model with readback off gets no battery sensor."""
    await _setup(hass, _entry(readback=False, radio_id="0089"))  # MA5R, has battery
    assert hass.states.get("sensor.light_battery") is None


@pytest.mark.usefixtures("fake_ble")
async def test_no_battery_sensor_for_mains_model(hass: HomeAssistant) -> None:
    """A mains model gets no battery sensor even with readback on."""
    await _setup(hass, _entry(readback=True, radio_id="003F"))  # SL200III Bi, mains
    assert hass.states.get("sensor.light_battery") is None


@pytest.mark.usefixtures("fake_ble")
async def test_battery_sensor_reports_charge(hass: HomeAssistant) -> None:
    """A battery model with readback on gets a sensor that polls the light."""
    with patch.object(
        GodoxMeshLink, "async_request_battery", AsyncMock(return_value=73)
    ):
        await _setup(hass, _entry(readback=True, radio_id="0089"))  # MA5R
        state = hass.states.get("sensor.light_battery")
        assert state is not None
        assert state.state == "73"
        assert state.attributes["device_class"] == "battery"


@pytest.mark.usefixtures("fake_ble")
async def test_battery_sensor_unavailable_when_light_does_not_answer(
    hass: HomeAssistant,
) -> None:
    """A light that does not answer (unreachable) is not fatal."""
    from homeassistant.exceptions import HomeAssistantError

    with patch.object(
        GodoxMeshLink,
        "async_request_battery",
        AsyncMock(side_effect=HomeAssistantError("no answer")),
    ):
        await _setup(hass, _entry(readback=True, radio_id="0089"))
        state = hass.states.get("sensor.light_battery")
        assert state is not None
        assert state.state in ("unknown", "unavailable")
