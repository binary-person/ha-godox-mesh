"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

# The integration is imported as a top-level ``custom_components`` package, the
# same way Home Assistant loads it from a config directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.godox_mesh.const import (  # noqa: E402
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"

# Synthetic keys. Never put real mesh state in the repository: these are
# live crypto material for a physical light, which is why .gitignore
# excludes mesh_state.json.
MESH_STATE = {
    "network_key": "00112233445566778899aabbccddeeff",
    "app_key": "0f0e0d0c0b0a09080706050403020100",
    "device_key": "deadbeefdeadbeefdeadbeefdeadbeef",
    "device_address": ADDRESS,
    "provisioner_address": 1,
    "node_address": 2,
    "sequence_number": 300000,
    "iv_index": 0,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load integrations from custom_components/ in every test."""
    return enable_custom_integrations


@pytest.fixture(autouse=True)
def mock_bluetooth_history():
    """Stub the DBus advertisement history the adapter fixture leaves live.

    pytest-homeassistant-custom-component patches the Linux adapter's refresh
    and adapters, but the bluetooth component also reads history at setup,
    which reaches for a real DBus connection.
    """
    with patch("bluetooth_adapters.systems.linux.LinuxAdapters.history", {}):
        yield


@pytest.fixture(autouse=True)
def auto_enable_bluetooth(mock_bluetooth_history, enable_bluetooth):
    """Set up the bluetooth component with a mocked adapter.

    The integration declares bluetooth_adapters as a dependency, so without
    this every setup would fail trying to reach a real DBus/BlueZ stack.
    """
    return enable_bluetooth


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a single-node mesh network."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Key Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: "SL200III Bi"}
            ]
        },
    )


class FakeBleakClient:
    """Stand-in for the BLE transport, recording what the library writes."""

    def __init__(self, *args, **_kwargs) -> None:
        # (hass, address, name) as built by the mesh link's client factory.
        self.target = args[1] if len(args) > 1 else None
        self.is_connected = False
        self.mtu_size = 247
        self.writes: list[tuple[str, bytes]] = []
        self._notify = None

    async def connect(self, **_kwargs) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def write_gatt_char(self, characteristic, data, response=False) -> None:
        self.writes.append((characteristic, bytes(data)))

    async def start_notify(self, _characteristic, callback) -> None:
        self._notify = callback

    async def stop_notify(self, _characteristic) -> None:
        self._notify = None


@pytest.fixture
def fake_ble(monkeypatch):
    """Replace the integration's BLE client with an in-memory fake."""
    clients: list[FakeBleakClient] = []

    def factory(_hass, _address, _name):
        client = FakeBleakClient(_hass, _address, _name)
        clients.append(client)
        return client

    monkeypatch.setattr(
        "custom_components.godox_mesh.mesh.HomeAssistantBleakClient", factory
    )
    # The fake transport never emits a Secure Network Beacon, so without this
    # every connect would burn the full real-world wait.
    monkeypatch.setattr("custom_components.godox_mesh.mesh.BEACON_WAIT_TIMEOUT", 0.01)
    monkeypatch.setattr(
        "custom_components.godox_mesh.mesh.PROXY_CONFIG_ACK_TIMEOUT", 0.01
    )
    return clients


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_ble
) -> ConfigEntry:
    """Set up the integration with a fake BLE transport."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry
