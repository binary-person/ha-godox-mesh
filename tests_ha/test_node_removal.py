"""Tests for removing lights from a mesh network entry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MAC,
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE


@pytest.fixture
async def two_light_entry(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
    """A mesh network entry with two lights on it."""
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
    return entry


def _device_names(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    return {
        device.name
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    }


async def test_removing_a_node_removes_its_entity_and_device(
    hass: HomeAssistant, two_light_entry
) -> None:
    """A removed light must not linger as an unavailable ghost."""
    assert _device_names(hass, two_light_entry) == {"Key Light", "Fill Light"}

    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        result = await hass.config_entries.options.async_init(
            two_light_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "remove_node"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_NODES: ["3"]}
        )
        await hass.async_block_till_done()

    assert result["data"][CONF_NODES] == [
        {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None}
    ]
    assert hass.states.get("light.key_light") is not None
    assert hass.states.get("light.fill_light") is None
    assert _device_names(hass, two_light_entry) == {"Key Light"}


async def test_remaining_light_still_works_after_a_removal(
    hass: HomeAssistant, two_light_entry, fake_ble
) -> None:
    """Removing one light must not disturb the shared connection."""
    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        result = await hass.config_entries.options.async_init(
            two_light_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "remove_node"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_NODES: ["3"]}
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.key_light"},
            blocking=True,
        )

    assert hass.states.get("light.key_light").state == "on"


async def test_removing_every_node_is_refused(
    hass: HomeAssistant, two_light_entry
) -> None:
    """An entry with no lights left would be a mesh connection to nothing."""
    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        result = await hass.config_entries.options.async_init(
            two_light_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "remove_node"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_NODES: ["2", "3"]}
        )

    assert result["type"].value == "form"
    assert result["errors"] == {CONF_NODES: "cannot_remove_all"}


async def test_device_can_be_deleted_from_the_ui(
    hass: HomeAssistant, two_light_entry, hass_ws_client
) -> None:
    """Deleting a stale device from the device page must be allowed."""
    from custom_components.godox_mesh import async_remove_config_entry_device

    registry = dr.async_get(hass)
    device = next(
        d
        for d in dr.async_entries_for_config_entry(registry, two_light_entry.entry_id)
        if d.name == "Fill Light"
    )

    assert await async_remove_config_entry_device(hass, two_light_entry, device)


async def test_deleting_a_light_lets_it_be_rediscovered(
    hass: HomeAssistant, fake_ble
) -> None:
    """A deleted light's address is cleared from HA's discovery match history.

    A provisioned light keeps advertising the proxy service, but HA holds its
    address in the match history, so without clearing it the unchanged advert
    stays suppressed and the light is not offered again until a restart.
    """
    from custom_components.godox_mesh import async_remove_config_entry_device

    node_mac = "11:22:33:44:55:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None},
                {
                    CONF_NODE_ADDRESS: 3,
                    CONF_NAME: "Fill Light",
                    CONF_MODEL: None,
                    CONF_MAC: node_mac,
                },
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

    device = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if d.name == "Fill Light"
    )
    with patch(
        "custom_components.godox_mesh.bluetooth.async_rediscover_address"
    ) as rediscover:
        assert await async_remove_config_entry_device(hass, entry, device)

    rediscover.assert_any_call(hass, node_mac)
