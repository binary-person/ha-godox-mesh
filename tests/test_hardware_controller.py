from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from godox_mesh_bt.controller import GodoxController
from godox_mesh_bt.scanner import scan


@pytest_asyncio.fixture
async def device_address() -> str:
    # Use environment variable if provided
    device = os.environ.get("GODOX_MESH_BT_DEVICE")
    if device:
        return device

    # Fallback to discovery
    print("\nNo GODOX_MESH_BT_DEVICE set; discovering Godox devices...")
    devices = await scan(timeout=2.0)
    godox_devices = [d for d in devices if d.likely_godox]

    if not godox_devices:
        pytest.fail("No Godox devices discovered during scan. Set GODOX_MESH_BT_DEVICE to override.")

    selected = godox_devices[0]
    print(f"Discovered {selected.name} ({selected.address})")
    return selected.address


@pytest.fixture
def state_path() -> Path:
    path = Path(os.environ.get("GODOX_MESH_BT_STATE", "mesh_state.json"))
    if not path.exists():
        pytest.fail(f"Mesh state file not found at {path}. Set GODOX_MESH_BT_STATE to override.")
    return path


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_live_power_cycle(device_address, state_path) -> None:
    async with GodoxController(device_address, state_path) as controller:
        await controller.power_off()
        await controller.power_on()


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_live_brightness_cct_sweep(device_address, state_path) -> None:
    async with GodoxController(device_address, state_path) as controller:
        await controller.set_params(brightness=25.0)
        await controller.set_params(cct=5600, brightness=50.0)
        await controller.set_params(brightness=75.0)
