"""Tests for status readback and the wider command set on the controller."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from godox_mesh_bt.controller import (
    RESPONSE_OPCODE,
    StatusTimeout,
    GodoxController,
)
from godox_mesh_bt.crypto import build_vendor_access_payload, pack_proxy_network_pdu
from godox_mesh_bt.protocol import SUB_STATUS_CCT
from godox_mesh_bt.state import MeshState


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


def connected(mesh_state: MeshState):
    client = MagicMock()
    client.connect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.is_connected = True
    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF", state=mesh_state, client_factory=lambda a: client
    )
    controller._client._client = client
    return controller, client


def status_pdu(state: MeshState, payload: bytes, *, src: int = 0x0002) -> bytes:
    """Build the proxy PDU a light would send in reply to a status request."""
    return pack_proxy_network_pdu(
        build_vendor_access_payload(RESPONSE_OPCODE, payload),
        bytes.fromhex(state.network_key),
        bytes.fromhex(state.app_key),
        iv_index=state.iv_index,
        seq=500,
        src=src,
        dst=state.provisioner_address,
        ttl=10,
    )


def test_response_opcode_is_the_request_opcode_plus_one() -> None:
    """The light answers on 0xF1 where commands go out on 0xF0."""
    from godox_mesh_bt.controller import REQUEST_OPCODE

    assert RESPONSE_OPCODE == REQUEST_OPCODE + 1
    assert build_vendor_access_payload(RESPONSE_OPCODE, b"")[:3].hex() == "f11102"


@pytest.mark.asyncio
async def test_request_status_sends_the_status_request_frame(mesh_state) -> None:
    controller, client = connected(mesh_state)
    reply = bytes.fromhex("a00a1b32ffff01f9")

    async def answer() -> None:
        await asyncio.sleep(0)
        controller._handle_response(status_pdu(mesh_state, reply))

    task = asyncio.create_task(answer())
    status = await controller.request_status(timeout=1.0)
    await task

    assert status.sub_command == SUB_STATUS_CCT
    assert (status.brightness, status.cct) == (10, 2700)
    sent = client.write_gatt_char.await_args_list[0].args[1]
    assert sent[0] == 0x00  # proxy network PDU


@pytest.mark.asyncio
async def test_request_status_times_out_without_a_reply(mesh_state) -> None:
    """A light whose status is unwired must fail loudly, not hang."""
    controller, _ = connected(mesh_state)

    with pytest.raises(StatusTimeout):
        await controller.request_status(timeout=0.05)


@pytest.mark.asyncio
async def test_stale_replies_do_not_satisfy_a_later_request(mesh_state) -> None:
    """A reply that arrived before the request must not be returned for it."""
    controller, _ = connected(mesh_state)
    controller._handle_response(status_pdu(mesh_state, bytes.fromhex("a00a1b32ffff01f9")))

    with pytest.raises(StatusTimeout):
        await controller.request_status(timeout=0.05)


@pytest.mark.asyncio
async def test_unrelated_notifications_are_ignored(mesh_state) -> None:
    controller, _ = connected(mesh_state)

    controller._handle_response(b"")
    controller._handle_response(b"\x01beacon")
    controller._handle_response(b"\x00garbage-that-will-not-decrypt")

    with pytest.raises(StatusTimeout):
        await controller.request_status(timeout=0.05)


@pytest.mark.asyncio
async def test_set_effect_sends_the_older_effect_frame(mesh_state) -> None:
    controller, _ = connected(mesh_state)
    sent: list = []
    controller.send_payload = AsyncMock(
        side_effect=lambda *a, **k: sent.append((a, k)) or None
    )

    await controller.set_effect(4, brightness=80, dst=0x0003)

    (payload,), kwargs = sent[0]
    assert payload[0] == 0xF3
    assert payload[1] == 80 and payload[2] == 4
    assert kwargs["dst"] == 0x0003


@pytest.mark.asyncio
async def test_set_effect_sends_the_newer_frame_for_version_1(mesh_state) -> None:
    """122 of the 177 models with effects take this frame, not the 0xF3 one.

    The third data byte is the V3 selector, which is deliberately *not* the
    symbol: Candle is symbol 10 but selector 5.
    """
    controller, _ = connected(mesh_state)
    sent: list = []
    controller.send_payload = AsyncMock(
        side_effect=lambda *a, **k: sent.append((a, k)) or None
    )

    await controller.set_effect(
        10, brightness=80, speed=60, effect_version=1, dst=0x0003
    )

    (payload,), kwargs = sent[0]
    assert payload[0] == 0xF7
    assert payload[2:6] == bytes([80, 0, 5, 60])
    assert kwargs["dst"] == 0x0003


@pytest.mark.asyncio
async def test_set_fan_mode_sends_the_fan_frame(mesh_state) -> None:
    controller, _ = connected(mesh_state)
    sent: list = []
    controller.send_v2_command = AsyncMock(
        side_effect=lambda *a, **k: sent.append((a, k)) or None
    )

    await controller.set_fan_mode(2)

    (model, _end, data), _kwargs = sent[0]
    assert model == 0xF5
    assert data[0] == 2


@pytest.mark.asyncio
async def test_invalid_effect_and_fan_values_are_rejected(mesh_state) -> None:
    controller, _ = connected(mesh_state)

    with pytest.raises(ValueError):
        await controller.set_effect(256, brightness=50)
    with pytest.raises(ValueError):
        await controller.set_fan_mode(9)
