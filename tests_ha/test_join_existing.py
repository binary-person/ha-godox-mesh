"""Adding a discovered light to an existing mesh from the Add-device flow.

The "Add device" button starts a config flow, which used to only ever create a
new, separate mesh. This provisions the discovered light onto a mesh already set
up here instead -- no new entry; the existing entry gains a node.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_DEVICE_KEY,
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
PROVISION_PATH = "custom_components.godox_mesh.mesh.GodoxMeshLink.async_provision_node"
NEW_LIGHT = "22:33:44:55:66:77"
NEW_DEVICE_KEY = "bb" * 16


def _advert(radio_id: str, company: int = 0x0211) -> dict[int, bytes]:
    value = int(radio_id, 16)
    payload = bytearray(16)
    payload[4] = value & 0xFF
    payload[5] = (value >> 8) & 0xFF
    return {company: bytes(payload)}


def _service_info(address: str = NEW_LIGHT, manufacturer_data=None):
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    manufacturer_data = manufacturer_data or {}
    proxy = "00001828-0000-1000-8000-00805f9b34fb"
    device = BLEDevice(address, "GD_LED", {})
    adv = AdvertisementData(
        local_name="GD_LED",
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[proxy],
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name="GD_LED",
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[proxy],
        source="local",
        device=device,
        advertisement=adv,
        connectable=True,
        time=0,
        tx_power=None,
    )


@pytest.fixture
async def existing_mesh(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light"}]},
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_join_option_appears_only_when_a_mesh_exists(
    hass: HomeAssistant,
) -> None:
    """With no mesh yet, only the two new-mesh choices are offered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    options = result["data_schema"].schema["setup_method"].config["options"]
    assert set(options) == {"mesh_state", "provision"}


async def test_join_adds_the_light_to_the_existing_entry(
    hass: HomeAssistant, existing_mesh
) -> None:
    """Choosing "add to existing mesh" provisions onto it and adds a node."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(manufacturer_data=_advert("003F")),  # SL200III Bi
    )
    options = result["data_schema"].schema["setup_method"].config["options"]
    assert "join_existing" in options

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "join_existing"}
    )
    assert result["step_id"] == "join_existing"

    # One mesh, so the confirm form has no field -- submit provisions it.
    with (
        patch(PROVISION_PATH, AsyncMock(return_value=(NEW_DEVICE_KEY, 2))),
        patch(BLE_PATH, return_value=object()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
        assert result["step_id"] == "join_model"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}  # accept the detected model
        )
        assert result["step_id"] == "settings"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}  # accept the model's default settings
        )
        await hass.async_block_till_done()

    # No new entry -- the existing one gained a node.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "added_to_existing"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    nodes = existing_mesh.options[CONF_NODES]
    assert [n[CONF_NODE_ADDRESS] for n in nodes] == [2, 4]
    added = nodes[1]
    assert added[CONF_RADIO_ID] == "003F"
    assert added[CONF_NAME].startswith("SL200IIIBi")
    assert added[CONF_DEVICE_KEY] == NEW_DEVICE_KEY


async def test_join_reports_a_provisioning_failure(
    hass: HomeAssistant, existing_mesh
) -> None:
    """A failed provisioning re-shows the form with an error, no node added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "join_existing"}
    )
    with (
        patch(PROVISION_PATH, AsyncMock(side_effect=RuntimeError("boom"))),
        patch(BLE_PATH, return_value=object()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "join_existing"
    assert result["errors"] == {"base": "provision_failed"}
    assert len(existing_mesh.options[CONF_NODES]) == 1  # nothing added
