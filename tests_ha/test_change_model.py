"""Changing a light's model after setup, without removing and re-adding it.

A wrong model pick (an SL200Bi chosen for an SL200III Bi, say) otherwise means
deleting the device and re-pairing. This lets it be corrected in two clicks, and
the capabilities/colour-temperature range re-resolve on the reload that follows.
"""

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
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"


async def _entry(hass: HomeAssistant, nodes: list[dict]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: nodes},
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_single_node_goes_straight_to_the_model_step(hass: HomeAssistant) -> None:
    """With one light there is nothing to pick -- go straight to setting the model."""
    entry = await _entry(
        hass, [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_RADIO_ID: "00D1"}]
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_model"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "set_model"

    with patch(BLE_PATH, return_value=object()):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_RADIO_ID: "003F"}  # SL200Bi -> SL200III Bi
        )
        assert result["step_id"] == "node_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NODES][0][CONF_RADIO_ID] == "003F"


@pytest.mark.usefixtures("fake_ble")
async def test_the_model_step_is_prefilled_with_the_current_model(
    hass: HomeAssistant,
) -> None:
    """The picker starts on the node's current model, so a nudge is one change."""
    entry = await _entry(
        hass, [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_RADIO_ID: "00D1"}]
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_model"}
    )
    key = next(k for k in result["data_schema"].schema if k == CONF_RADIO_ID)
    assert key.description["suggested_value"] == "00D1"


@pytest.mark.usefixtures("fake_ble")
async def test_multi_node_picks_one_then_rewrites_only_it(hass: HomeAssistant) -> None:
    """With several lights, pick which one, and only that node changes."""
    entry = await _entry(
        hass,
        [
            {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_RADIO_ID: "00D1"},
            {CONF_NODE_ADDRESS: 4, CONF_NAME: "Fill", CONF_RADIO_ID: "003A"},
        ],
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_model"}
    )
    assert result["step_id"] == "change_model"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NODES: "4"}
    )
    assert result["step_id"] == "set_model"

    with patch(BLE_PATH, return_value=object()):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_RADIO_ID: "003F"}
        )
        assert result["step_id"] == "node_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

    nodes = {n[CONF_NODE_ADDRESS]: n for n in entry.options[CONF_NODES]}
    assert nodes[4][CONF_RADIO_ID] == "003F"  # the one picked
    assert nodes[2][CONF_RADIO_ID] == "00D1"  # untouched


@pytest.mark.usefixtures("fake_ble")
async def test_use_xy_offered_only_for_xy_models(hass: HomeAssistant) -> None:
    """The settings step shows use-xy for an xy model, hides it otherwise.

    It reflects the model *picked* on the preceding step, which is why the two
    steps are split: the field can only follow a choice already submitted.
    """
    from custom_components.godox_mesh.const import CONF_USE_XY

    def fields_for(schema):
        return {getattr(k, "schema", k) for k in schema}

    async def settings_for(entry, radio_id: str):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "change_model"}
        )
        assert result["step_id"] == "set_model"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_RADIO_ID: radio_id}
        )
        assert result["step_id"] == "node_settings"
        return fields_for(result["data_schema"].schema)

    # 00B6 (SL200 RF) supports xy.
    entry = await _entry(
        hass, [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_RADIO_ID: "00B6"}]
    )
    assert CONF_USE_XY in await settings_for(entry, "00B6")

    # 003F (SL200III Bi) does not.
    entry2 = MockConfigEntry(
        domain=DOMAIN,
        title="Fill",
        unique_id="11:22:33:44:55:66",
        data={CONF_ADDRESS: "11:22:33:44:55:66", CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Fill", CONF_RADIO_ID: "003F"}]},
    )
    entry2.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()
    assert CONF_USE_XY not in await settings_for(entry2, "003F")
