"""Battery readback over the vendor protocol (works on stock firmware)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from godox_mesh_bt.controller import (
    RESPONSE_OPCODE,
    BatteryTimeout,
    GodoxController,
)
from godox_mesh_bt.crypto import build_vendor_access_payload, pack_proxy_network_pdu
from godox_mesh_bt.protocol import build_battery_request
from godox_mesh_bt.state import MeshState


def test_battery_request_matches_the_apps_frame() -> None:
    """getBatteryPower sends 0xFD with data byte 1 and end byte 0xA6."""
    frame = build_battery_request()
    assert frame[0] == 0xFD
    assert frame[1] == 0x01
    assert frame[6] == 0xA6  # the end byte selects the battery record
    assert len(frame) == 8
    # exactly what the decompiled app builds
    assert frame.hex() == "fd01ffffffffa648"


@pytest.fixture
def mesh_state() -> MeshState:
    return MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=0x0001,
        node_address=0x0002,
        sequence_number=1,
        iv_index=0,
        device_key="aa" * 16,
    )


def _connected(mesh_state: MeshState):
    client = MagicMock()
    client.connect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.is_connected = True
    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF", state=mesh_state, client_factory=lambda a: client
    )
    controller._client._client = client
    return controller, client


def _battery_pdu(state: MeshState, payload: bytes) -> bytes:
    return pack_proxy_network_pdu(
        build_vendor_access_payload(RESPONSE_OPCODE, payload),
        bytes.fromhex(state.network_key),
        bytes.fromhex(state.app_key),
        iv_index=state.iv_index,
        seq=500,
        src=0x0002,
        dst=state.provisioner_address,
        ttl=10,
    )


@pytest.mark.asyncio
async def test_request_battery_returns_the_parsed_reply(mesh_state) -> None:
    controller, _ = _connected(mesh_state)
    # A6: state, hour, minute, option|percent -> 64%
    reply = bytes.fromhex("a600000040000000")

    async def answer() -> None:
        await asyncio.sleep(0)
        controller._handle_response(_battery_pdu(mesh_state, reply))

    task = asyncio.create_task(answer())
    battery = await controller.request_battery(timeout=1.0)
    await task

    assert battery.power_percent == 64
    assert battery.state == 0


@pytest.mark.asyncio
async def test_request_battery_times_out_when_unanswered(mesh_state) -> None:
    """A mains light answers a constant 100 %; an unreachable one never answers; fail loudly, don't hang."""
    controller, _ = _connected(mesh_state)
    with pytest.raises(BatteryTimeout):
        await controller.request_battery(timeout=0.05)


@pytest.mark.asyncio
async def test_battery_and_status_replies_do_not_cross_wires(mesh_state) -> None:
    """An A0 status reply must not satisfy a battery request, and vice versa."""
    controller, _ = _connected(mesh_state)
    controller._handle_response(
        _battery_pdu(mesh_state, bytes.fromhex("a00a1b32ffff01f9"))  # A0 status
    )
    with pytest.raises(BatteryTimeout):
        await controller.request_battery(timeout=0.05)
