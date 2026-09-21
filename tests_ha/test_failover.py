"""End-to-end failover: losing the configured light must not lose the network."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.godox_mesh.ble import DeviceNotFound
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
    MESH_PROXY_SERVICE_UUID,
)
from custom_components.godox_mesh._lib.crypto import k3
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE, FakeBleakClient

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
DISCOVERY = "custom_components.godox_mesh.gateway.async_discovered_service_info"
SECOND_LIGHT = "22:33:44:55:66:77"


def _advert(address: str, net_key: str = MESH_STATE["network_key"], rssi: int = -60):
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    service_data = {MESH_PROXY_SERVICE_UUID: b"\x00" + k3(bytes.fromhex(net_key))}
    device = BLEDevice(address, "GD_LED", {})
    advertisement = AdvertisementData(
        local_name="GD_LED",
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[MESH_PROXY_SERVICE_UUID],
        tx_power=None,
        rssi=rssi,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name="GD_LED",
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[MESH_PROXY_SERVICE_UUID],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=None,
    )


class _Power:
    """Simulate mains power to individual lights."""

    def __init__(self, clients: list) -> None:
        self._clients = clients
        self.offline: set[str] = set()

    def unplug(self, address: str) -> None:
        self.offline.add(address)
        # Losing power kills any live connection, it does not merely prevent
        # future ones. Modelling only the latter would hide the case where a
        # stale connection stops the gateway ever being reconsidered.
        for client in self._clients:
            if client.target == address:
                client.is_connected = False

    def replug(self, address: str) -> None:
        self.offline.discard(address)


@pytest.fixture
def unplugged(monkeypatch, fake_ble):
    """Make named addresses refuse connections, as an unplugged light would."""
    power = _Power(fake_ble)
    real = FakeBleakClient.connect

    async def connect(self, **kwargs):
        if self.target in power.offline:
            raise DeviceNotFound(f"{self.target} is not in range")
        await real(self)

    monkeypatch.setattr(FakeBleakClient, "connect", connect)
    return power


@pytest.fixture
async def two_node_entry(hass: HomeAssistant, fake_ble) -> MockConfigEntry:
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
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _turn_on(hass: HomeAssistant, entity: str) -> None:
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: entity}, blocking=True
    )


async def test_network_survives_losing_the_configured_light(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """Unplug light number one; the rest of the network stays controllable."""
    link = two_node_entry.runtime_data.link
    unplugged.unplug(ADDRESS)

    with patch(DISCOVERY, return_value=[_advert(SECOND_LIGHT)]):
        await _turn_on(hass, "light.fill_light")

    assert link.gateway_address == SECOND_LIGHT
    assert hass.states.get("light.fill_light").state == "on"


async def test_a_gateway_that_advertises_but_will_not_connect_is_abandoned(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """A light that advertises but refuses connections must not trap the link.

    A light can keep advertising -- so it looks reachable and the sticky
    ``preferred`` bias keeps choosing it -- while being unable to hold a
    connection. The link must demote it after a failed attempt and enter the
    mesh through a sibling within the same command, rather than hammering it.
    """
    link = two_node_entry.runtime_data.link
    unplugged.unplug(ADDRESS)  # will not connect ...

    # ... but is still advertising, and more strongly than the sibling.
    with patch(
        DISCOVERY,
        return_value=[_advert(ADDRESS, rssi=-30), _advert(SECOND_LIGHT, rssi=-80)],
    ):
        await _turn_on(hass, "light.fill_light")

    assert link.gateway_address == SECOND_LIGHT
    assert hass.states.get("light.fill_light").state == "on"


async def test_entity_identity_survives_failover(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """Identity must not follow the transport, or automations break silently."""
    registry = er.async_get(hass)
    before = {
        entity.entity_id: entity.unique_id
        for entity in er.async_entries_for_config_entry(
            registry, two_node_entry.entry_id
        )
    }

    unplugged.unplug(ADDRESS)
    with patch(DISCOVERY, return_value=[_advert(SECOND_LIGHT)]):
        await _turn_on(hass, "light.fill_light")

    after = {
        entity.entity_id: entity.unique_id
        for entity in er.async_entries_for_config_entry(
            registry, two_node_entry.entry_id
        )
    }
    assert before == after
    assert all(ADDRESS in unique_id for unique_id in after.values())


async def test_sticks_to_the_fallback_once_chosen(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """After failover, the configured light coming back must not cause churn.

    This is what makes a two-light bench test meaningful: with the connection
    held on the second light, the first becomes a node reachable only over the
    mesh.
    """
    link = two_node_entry.runtime_data.link
    unplugged.unplug(ADDRESS)
    with patch(DISCOVERY, return_value=[_advert(SECOND_LIGHT)]):
        await _turn_on(hass, "light.fill_light")
    assert link.gateway_address == SECOND_LIGHT

    # Light one is plugged back in and advertising strongly again.
    unplugged.replug(ADDRESS)
    with patch(
        DISCOVERY,
        return_value=[_advert(ADDRESS, rssi=-30), _advert(SECOND_LIGHT, rssi=-80)],
    ):
        await _turn_on(hass, "light.key_light")

    assert link.gateway_address == SECOND_LIGHT, "gateway churned back unnecessarily"
    assert hass.states.get("light.key_light").state == "on"


async def test_returns_to_the_configured_light_when_the_fallback_dies(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """A dead fallback must not strand the network."""
    link = two_node_entry.runtime_data.link
    unplugged.unplug(ADDRESS)
    with patch(DISCOVERY, return_value=[_advert(SECOND_LIGHT)]):
        await _turn_on(hass, "light.fill_light")
    assert link.gateway_address == SECOND_LIGHT

    unplugged.replug(ADDRESS)
    unplugged.unplug(SECOND_LIGHT)
    with patch(DISCOVERY, return_value=[_advert(ADDRESS)]):
        await _turn_on(hass, "light.key_light")

    assert link.gateway_address == ADDRESS


async def test_still_fails_clearly_when_the_whole_network_is_gone(
    hass: HomeAssistant, two_node_entry, unplugged
) -> None:
    """Every light unplugged is still an error, not a silent no-op."""
    unplugged.unplug(ADDRESS)
    unplugged.unplug(SECOND_LIGHT)

    with patch(DISCOVERY, return_value=[]):
        with pytest.raises(HomeAssistantError, match="not in range"):
            await _turn_on(hass, "light.key_light")


async def test_setup_succeeds_when_only_a_sibling_is_reachable(
    hass: HomeAssistant, fake_ble
) -> None:
    """The configured light being off must not block setup.

    Regression: the old reachability check queried the configured address alone,
    so a powered-off entry light failed setup entirely -- and the failover that
    would have entered through a sibling never got to run.
    """
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_MODEL: None}]},
    )
    entry.add_to_hass(hass)
    # The configured light is unreachable, but a sibling advertises the network.
    with (
        patch(BLE_PATH, return_value=None),
        patch(DISCOVERY, return_value=[_advert(SECOND_LIGHT)]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_setup_retries_when_nothing_of_the_mesh_is_reachable(
    hass: HomeAssistant, fake_ble
) -> None:
    """With no node in range at all, setup defers rather than erroring out."""
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: [{CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_MODEL: None}]},
    )
    entry.add_to_hass(hass)
    with (
        patch(BLE_PATH, return_value=None),
        patch(DISCOVERY, return_value=[]),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
