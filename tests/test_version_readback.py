"""Firmware-version readback — used to detect the readback patch (version 1)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from godox_mesh_bt.controller import (
    RESPONSE_OPCODE,
    VersionTimeout,
    GodoxController,
)
from godox_mesh_bt.crypto import build_vendor_access_payload, pack_proxy_network_pdu
from godox_mesh_bt.protocol import build_version_request, parse_version_response
from godox_mesh_bt.state import MeshState


def test_version_request_matches_the_apps_frame() -> None:
    """getFirmwareVersion sends 0xFD with data byte 2 and end byte 0xFF."""
    frame = build_version_request()
    assert frame[0] == 0xFD
    assert frame[1] == 0x02
    assert frame.hex() == "fd02ffffffffff56"


def test_parse_version_reply() -> None:
    """The AF 20 reply carries the reported firmware version in byte 4."""
    # stock LK8620 reports 0x66 (102); the readback patch makes it report 1.
    assert parse_version_response(bytes.fromhex("af20000066ffff00")) == 102
    assert parse_version_response(bytes.fromhex("af20000001ffff00")) == 1


def test_parse_version_rejects_a_non_version_reply() -> None:
    with pytest.raises(ValueError, match="not a version"):
        parse_version_response(bytes.fromhex("a00a1b32ffff01f9"))


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


def _connected(mesh_state):
    client = MagicMock()
    client.connect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.is_connected = True
    c = GodoxController("AA:BB:CC:DD:EE:FF", state=mesh_state, client_factory=lambda a: client)
    c._client._client = client
    return c


def _pdu(state, payload):
    return pack_proxy_network_pdu(
        build_vendor_access_payload(RESPONSE_OPCODE, payload),
        bytes.fromhex(state.network_key), bytes.fromhex(state.app_key),
        iv_index=state.iv_index, seq=500, src=0x0002,
        dst=state.provisioner_address, ttl=10,
    )


@pytest.mark.asyncio
async def test_request_version_returns_the_reported_version(mesh_state) -> None:
    controller = _connected(mesh_state)

    async def answer():
        await asyncio.sleep(0)
        controller._handle_response(_pdu(mesh_state, bytes.fromhex("af20000001ffff00")))

    task = asyncio.create_task(answer())
    version = await controller.request_version(timeout=1.0)
    await task
    assert version == 1  # a patched light


@pytest.mark.asyncio
async def test_request_version_times_out_when_unanswered(mesh_state) -> None:
    controller = _connected(mesh_state)
    with pytest.raises(VersionTimeout):
        await controller.request_version(timeout=0.05)
