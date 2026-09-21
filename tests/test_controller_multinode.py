"""Tests for multi-node routing, in-memory state, and injectable transports.

These cover the capabilities the Home Assistant integration depends on: one
proxy connection fanning out to several mesh nodes, mesh state held in memory
and persisted through a callback, and BLE clients supplied by the caller.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from godox_mesh_bt.config_session import ConfigSession
from godox_mesh_bt.controller import GodoxController
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


def connected_controller(mesh_state: MeshState, **kwargs) -> tuple[GodoxController, MagicMock]:
    client = MagicMock()
    client.connect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.is_connected = True
    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda address: client,
        **kwargs,
    )
    controller._client._client = client
    return controller, client


@pytest.mark.asyncio
async def test_controller_accepts_in_memory_state_without_path(mesh_state) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    assert controller.state.network_key == mesh_state.network_key
    assert controller.state_path is None


@pytest.mark.asyncio
async def test_controller_requires_state_or_state_path() -> None:
    with pytest.raises(ValueError, match="state_path"):
        GodoxController("AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_controller_state_writer_receives_advanced_state(mesh_state) -> None:
    saved: list[MeshState] = []
    controller, _ = connected_controller(
        mesh_state,
        state=mesh_state,
        state_writer=saved.append,
    )

    await controller.power_on()

    assert [state.sequence_number for state in saved] == [2]
    assert controller.state.sequence_number == 2


@pytest.mark.asyncio
async def test_controller_state_writer_replaces_file_persistence(tmp_path, mesh_state) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    controller, _ = connected_controller(
        mesh_state,
        state_path=state_file,
        state_writer=lambda state: None,
    )

    await controller.power_on()

    # The writer took over persistence, so the file is untouched.
    assert MeshState.load(state_file).sequence_number == 1


@pytest.mark.asyncio
async def test_send_v2_command_defaults_destination_to_node_address(mesh_state) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    with patch("godox_mesh_bt.controller.pack_proxy_network_pdu") as pack:
        pack.return_value = b"\x00pdu"
        await controller.send_v2_command(0xFE, 0xFF, bytes([0x00]))

    assert pack.call_args.kwargs["dst"] == 0x0002


@pytest.mark.asyncio
async def test_send_v2_command_routes_to_explicit_destination(mesh_state) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    with patch("godox_mesh_bt.controller.pack_proxy_network_pdu") as pack:
        pack.return_value = b"\x00pdu"
        await controller.send_v2_command(0xFE, 0xFF, bytes([0x00]), dst=0x0005)

    assert pack.call_args.kwargs["dst"] == 0x0005


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["power_on", "power_off"])
async def test_power_commands_route_to_explicit_destination(mesh_state, method) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    with patch.object(controller, "send_v2_command", AsyncMock()) as send:
        await getattr(controller, method)(dst=0x0007)

    assert send.call_args.kwargs["dst"] == 0x0007


@pytest.mark.asyncio
async def test_set_params_routes_to_explicit_destination(mesh_state) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    with patch.object(controller, "send_payload", AsyncMock()) as send:
        await controller.set_params(brightness=80, cct=4000, dst=0x0009)

    assert send.call_args.kwargs["dst"] == 0x0009


@pytest.mark.asyncio
async def test_sequence_number_is_shared_across_nodes(mesh_state) -> None:
    """One mesh network has one sequence counter, whichever node is addressed."""
    controller, client = connected_controller(mesh_state, state=mesh_state)

    await controller.power_on(dst=0x0002)
    await controller.power_on(dst=0x0003)
    await controller.power_on(dst=0x0004)

    assert controller.state.sequence_number == 4
    assert client.write_gatt_char.await_count == 3


@pytest.mark.asyncio
async def test_config_session_uses_injected_client_factory(mesh_state) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    seen: list[str] = []

    def factory(address: str):
        seen.append(address)
        return client

    session = ConfigSession(
        "AA:BB:CC:DD:EE:FF",
        mesh_state,
        client_factory=factory,
    )
    await session.run()

    assert seen == ["AA:BB:CC:DD:EE:FF"]
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
async def test_config_session_targets_explicit_node(mesh_state) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()

    session = ConfigSession(
        "AA:BB:CC:DD:EE:FF",
        mesh_state,
        client_factory=lambda address: client,
    )
    await session.run()

    assert session.final_state.sequence_number == mesh_state.sequence_number + 2


@pytest.mark.asyncio
async def test_controller_reports_connection_state(mesh_state) -> None:
    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state=mesh_state,
        client_factory=lambda address: _FakeClient(),
    )

    assert controller.is_connected is False
    await controller._client.connect()
    assert controller.is_connected is True


class _FakeClient:
    is_connected = False

    async def connect(self) -> None:
        self.is_connected = True


@pytest.mark.asyncio
async def test_controller_uses_default_handshake_timeouts(mesh_state) -> None:
    controller, _ = connected_controller(mesh_state, state=mesh_state)

    assert controller.beacon_wait_timeout == 2.0
    assert controller.proxy_config_ack_timeout == 5.0


@pytest.mark.asyncio
async def test_controller_accepts_shorter_handshake_timeouts(mesh_state) -> None:
    """A long-running caller can trade ack certainty for a faster connect."""
    controller, _ = connected_controller(
        mesh_state,
        state=mesh_state,
        beacon_wait_timeout=0.05,
        proxy_config_ack_timeout=0.05,
    )

    assert controller.beacon_wait_timeout == 0.05
    assert controller.proxy_config_ack_timeout == 0.05


@pytest.mark.asyncio
async def test_connect_completes_within_the_configured_timeouts(mesh_state) -> None:
    """A silent device must not stall connect() for the default 12 seconds."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.is_connected = True

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state=mesh_state,
        client_factory=lambda address: client,
        beacon_wait_timeout=0.01,
        proxy_config_ack_timeout=0.01,
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    await controller.connect()
    elapsed = loop.time() - started

    assert elapsed < 1.0
    # Filter type and whitelist were still sent despite no acknowledgement.
    assert client.write_gatt_char.await_count == 2
