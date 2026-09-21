"""Tests for provisioning a factory-reset light onto an existing mesh network.

The critical property: the new light must join the network the entry already
uses, reusing its network and application keys. Generating fresh keys would
produce a second, isolated network that the entry cannot address.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_DEVICE_KEY,
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
    MESH_PROVISIONING_SERVICE_UUID,
)
from custom_components.godox_mesh._lib import MeshState
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
DISCOVERY_PATH = (
    "custom_components.godox_mesh.config_flow.async_discovered_service_info"
)
NEW_LIGHT = "22:33:44:55:66:77"
NEW_DEVICE_KEY = "bb" * 16


def _unprovisioned(
    address: str = NEW_LIGHT,
    name: str = "GD_LED",
    manufacturer_data: dict[int, bytes] | None = None,
):
    """An advertisement from a factory-reset light.

    ``manufacturer_data`` defaults to empty (no model detectable); pass a Godox
    blob to exercise the model-detection path.
    """
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    manufacturer_data = manufacturer_data or {}
    device = BLEDevice(address, name, {})
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[MESH_PROVISIONING_SERVICE_UUID],
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[MESH_PROVISIONING_SERVICE_UUID],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=None,
    )


def _advert(radio_id: str, company: int = 0x0211) -> dict[int, bytes]:
    """Godox manufacturer data carrying *radio_id* (payload offsets 4/5)."""
    value = int(radio_id, 16)
    payload = bytearray(16)
    payload[4] = value & 0xFF
    payload[5] = (value >> 8) & 0xFF
    return {company: bytes(payload)}


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None}
            ]
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.fixture
def capture_sessions():
    """Record what the provisioning and binding sessions were given."""
    seen: dict = {"provision": None, "bind": None, "order": []}

    async def fake_provision(self):
        seen["provision"] = {
            "address": self.address,
            "net_key": self.net_key.hex(),
            "unicast_address": self.unicast_address,
            "iv_index": self.iv_index,
            "provisioner_address": self.provisioner_address,
        }
        seen["order"].append("provision")
        return MeshState(
            network_key=self.net_key.hex(),
            app_key="00" * 16,
            device_key=NEW_DEVICE_KEY,
            provisioner_address=self.provisioner_address,
            node_address=self.unicast_address,
            sequence_number=1,
            iv_index=self.iv_index,
        )

    async def fake_bind(self):
        seen["bind"] = {
            "address": self.address,
            "app_key": self._state.app_key,
            "device_key": self._state.device_key,
            "node_address": self._state.node_address,
            "sequence_number": self._state.sequence_number,
        }
        seen["order"].append("bind")
        return self._state.next_sequence(2)

    with (
        patch(
            "custom_components.godox_mesh.mesh.ProvisioningSession.run",
            fake_provision,
        ),
        patch("custom_components.godox_mesh.mesh.ConfigSession.run", fake_bind),
    ):
        yield seen


async def _run_provision_flow(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    name: str = "Fill Light",
    radio_id: str | None = None,
):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "provision_node"}
    )
    if result["type"] is not FlowResultType.FORM:
        return result
    user_input = {CONF_ADDRESS: NEW_LIGHT}
    if name is not None:
        user_input[CONF_NAME] = name
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    # A provisioning failure re-shows the provision_node form; success advances
    # to the model step, then a settings step, which we complete to reach the
    # created entry.
    if result["type"] is not FlowResultType.FORM or result["step_id"] != "provision_model":
        return result
    model_input = {CONF_RADIO_ID: radio_id} if radio_id else {}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], model_input
    )
    if result["type"] is not FlowResultType.FORM or result["step_id"] != "node_settings":
        return result
    return await hass.config_entries.options.async_configure(result["flow_id"], {})


async def test_provisioning_reuses_the_existing_network_key(
    hass: HomeAssistant, loaded_entry, capture_sessions
) -> None:
    """The whole point: join the existing network, do not create a new one."""
    with (
        patch(DISCOVERY_PATH, return_value=[_unprovisioned()]),
        patch(BLE_PATH, return_value=object()),
    ):
        result = await _run_provision_flow(hass, loaded_entry)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert capture_sessions["provision"]["net_key"] == MESH_STATE["network_key"]
    assert capture_sessions["provision"]["iv_index"] == MESH_STATE["iv_index"]
    assert (
        capture_sessions["provision"]["provisioner_address"]
        == MESH_STATE["provisioner_address"]
    )


async def test_provisioning_binds_the_networks_app_key_with_the_new_device_key(
    hass: HomeAssistant, loaded_entry, capture_sessions
) -> None:
    """Binding uses the network's app key but the new node's own device key."""
    with (
        patch(DISCOVERY_PATH, return_value=[_unprovisioned()]),
        patch(BLE_PATH, return_value=object()),
    ):
        await _run_provision_flow(hass, loaded_entry)
        await hass.async_block_till_done()

    assert capture_sessions["order"] == ["provision", "bind"]
    assert capture_sessions["bind"]["app_key"] == MESH_STATE["app_key"]
    assert capture_sessions["bind"]["device_key"] == NEW_DEVICE_KEY
    # A node owns two consecutive addresses (element 0 and 1), so the second
    # light starts at 0x0004, not 0x0003 (which is the first light's element 1).
    assert capture_sessions["bind"]["node_address"] == 4


async def test_provisioning_assigns_the_next_free_unicast_address(
    hass: HomeAssistant, fake_ble, capture_sessions
) -> None:
    """Allocation strides by the element count, so element-1 addresses are not reused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_MODEL: None},
                {CONF_NODE_ADDRESS: 4, CONF_NAME: "Back", CONF_MODEL: None},
            ]  # occupy 0x0002-0x0003 and 0x0004-0x0005
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch(DISCOVERY_PATH, return_value=[_unprovisioned()]):
            await _run_provision_flow(hass, entry)
            await hass.async_block_till_done()

    assert capture_sessions["provision"]["unicast_address"] == 6


async def test_provisioning_does_not_reuse_sequence_numbers(
    hass: HomeAssistant, loaded_entry, capture_sessions
) -> None:
    """Binding consumes numbers from the shared counter, which must advance."""
    link = loaded_entry.runtime_data.link
    before = link._controller.state.sequence_number

    with (
        patch(DISCOVERY_PATH, return_value=[_unprovisioned()]),
        patch(BLE_PATH, return_value=object()),
    ):
        await _run_provision_flow(hass, loaded_entry)
        await hass.async_block_till_done()

    # The bind started at the live counter, not at the library's default of 1.
    assert capture_sessions["bind"]["sequence_number"] >= before
    assert (
        loaded_entry.runtime_data.link._controller.state.sequence_number
        > capture_sessions["bind"]["sequence_number"]
    )


async def test_provisioned_light_becomes_an_entity(
    hass: HomeAssistant, loaded_entry, capture_sessions
) -> None:
    """The new node joins the entry's node list and gets its own light."""
    with (
        patch(DISCOVERY_PATH, return_value=[_unprovisioned()]),
        patch(BLE_PATH, return_value=object()),
    ):
        result = await _run_provision_flow(hass, loaded_entry)
        await hass.async_block_till_done()

    nodes = result["data"][CONF_NODES]
    assert [node[CONF_NODE_ADDRESS] for node in nodes] == [2, 4]
    # The node's own device key is kept so it can be re-bound later.
    assert nodes[1][CONF_DEVICE_KEY] == NEW_DEVICE_KEY
    assert hass.states.get("light.fill_light") is not None


async def test_provision_node_records_the_model_and_derives_the_name(
    hass: HomeAssistant, loaded_entry, capture_sessions
) -> None:
    """The detected model is written, so capabilities/CCT resolve, and it names the light.

    The old flow dropped the model, leaving every added light on the default
    2800-6500 K range. Detection here comes from the advertisement's
    manufacturer data.
    """
    advertised = _unprovisioned(manufacturer_data=_advert("003F"))  # SL200III Bi
    with (
        patch(DISCOVERY_PATH, return_value=[advertised]),
        patch(BLE_PATH, return_value=object()),
    ):
        # No name override, and accept the detected model (empty model input).
        result = await _run_provision_flow(hass, loaded_entry, name=None)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    node = result["data"][CONF_NODES][1]
    assert node[CONF_RADIO_ID] == "003F"
    # Name derived from the model, not typed: "SL200IIIBi (6677)".
    assert node[CONF_NAME].startswith("SL200IIIBi")


async def test_provision_node_excludes_the_entry_primary_light(
    hass: HomeAssistant, loaded_entry
) -> None:
    """The light already set up on this entry must not be offered for provisioning."""
    # An advert at the entry's own address, plus a genuine candidate.
    primary = _unprovisioned(address=ADDRESS)
    candidate = _unprovisioned(address=NEW_LIGHT)
    with patch(DISCOVERY_PATH, return_value=[primary, candidate]):
        result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "provision_node"}
        )

    offered = result["data_schema"].schema[CONF_ADDRESS].container
    assert set(offered) == {NEW_LIGHT}


async def test_no_unprovisioned_lights_aborts(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Nothing factory reset in range means nothing to provision."""
    with patch(DISCOVERY_PATH, return_value=[]):
        result = await _run_provision_flow(hass, loaded_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_unprovisioned_devices"


async def test_provisioning_failure_is_reported(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A failure leaves the node list untouched and shows an error."""
    async def boom(self):
        raise RuntimeError("no response")

    with (
        patch(DISCOVERY_PATH, return_value=[_unprovisioned()]),
        patch(BLE_PATH, return_value=object()),
        patch("custom_components.godox_mesh.mesh.ProvisioningSession.run", boom),
    ):
        result = await _run_provision_flow(hass, loaded_entry)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "provision_failed"}
    assert len(loaded_entry.options[CONF_NODES]) == 1


def test_allocator_skips_a_nodes_full_element_span() -> None:
    """A three-element light must not have the next light land on its elements.

    Both lights this was developed against are two-element nodes, so a fixed
    stride of two looked correct. The device reports its own count in the
    Provisioning Capabilities PDU, and that is what is used.
    """
    from custom_components.godox_mesh.config_flow import (
        _next_free_node_address,
        _occupied_addresses,
    )
    from custom_components.godox_mesh.const import CONF_NODE_ADDRESS, CONF_NUM_ELEMENTS

    # one three-element light at 0x0002 occupies 0x0002-0x0004
    nodes = [{CONF_NODE_ADDRESS: 2, CONF_NUM_ELEMENTS: 3}]
    taken = _occupied_addresses(nodes)

    assert taken == {2, 3, 4}
    assert _next_free_node_address(taken) == 6


def test_allocator_defaults_when_a_node_predates_the_element_count() -> None:
    """Entries written before the count was stored fall back to two."""
    from custom_components.godox_mesh.config_flow import _occupied_addresses
    from custom_components.godox_mesh.const import CONF_NODE_ADDRESS

    assert _occupied_addresses([{CONF_NODE_ADDRESS: 2}]) == {2, 3}


def test_allocator_handles_a_single_element_node() -> None:
    """A one-element node occupies one address, not two."""
    from custom_components.godox_mesh.config_flow import (
        _next_free_node_address,
        _occupied_addresses,
    )
    from custom_components.godox_mesh.const import CONF_NODE_ADDRESS, CONF_NUM_ELEMENTS

    taken = _occupied_addresses([{CONF_NODE_ADDRESS: 2, CONF_NUM_ELEMENTS: 1}])

    assert taken == {2}
    assert _next_free_node_address(taken, num_elements=1) == 3
