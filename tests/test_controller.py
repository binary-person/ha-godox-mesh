from __future__ import annotations

import asyncio
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch

from godox_mesh_bt.controller import (
    CONTROL_SETTLE_SECONDS,
    GodoxController,
)
from godox_mesh_bt.protocol import build_v2_command
from godox_mesh_bt.state import MeshState


@pytest.fixture
def mesh_state() -> MeshState:
    return MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=0x0001,
        node_address=0x0002,
        sequence_number=1,
        iv_index=0
    )


@pytest.mark.asyncio
async def test_controller_initialization(tmp_path, mesh_state) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    
    address = "AA:BB:CC:DD:EE:FF"
    controller = GodoxController(address, state_file)
    
    assert controller.address == address
    assert controller.state.network_key == mesh_state.network_key
    assert controller.state.sequence_number == 1


@pytest.mark.asyncio
async def test_controller_send_v2_command(tmp_path, mesh_state) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    
    address = "AA:BB:CC:DD:EE:FF"
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True
    
    def factory(addr):
        return mock_client

    controller = GodoxController(address, state_file, client_factory=factory)
    controller._client._client = mock_client # Manually set for test
    
    # Send Power On
    await controller.power_on()
    
    # Verify write was called
    mock_client.write_gatt_char.assert_called_once()
    args, kwargs = mock_client.write_gatt_char.call_args
    # proxy characteristic
    assert args[0] == "00002add-0000-1000-8000-00805f9b34fb"
    # value (Proxy PDU)
    pdu = args[1]
    assert pdu[0] == 0x00 # SAR 0, Type 0
    
    # Verify state advancement
    new_state = MeshState.load(state_file)
    assert new_state.sequence_number == 2


@pytest.mark.asyncio
async def test_controller_disconnect_waits_for_control_write_to_settle(
    tmp_path,
    mesh_state,
    monkeypatch,
) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.stop_notify = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True
    sleep = AsyncMock()
    monkeypatch.setattr("godox_mesh_bt.controller.asyncio.sleep", sleep)

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
    )
    controller._client._client = mock_client

    # Mock connect to avoid 12s of timeouts (beacon + proxy acks)
    monkeypatch.setattr(controller, "connect", AsyncMock())

    await controller.connect()
    await controller.send_v2_command(0xFE, 0xFF, bytes([0x00]))
    await controller.disconnect()

    sleep.assert_awaited_once_with(CONTROL_SETTLE_SECONDS)
    mock_client.stop_notify.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_controller_connect_writes_proxy_config_from_current_state(
    tmp_path,
    mesh_state,
    monkeypatch,
    caplog,
) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True
    pack_calls: list[dict[str, object]] = []
    notify_callback = None

    def fake_pack_proxy_config_pdu(
        *,
        opcode: int,
        parameters: bytes,
        net_key: bytes,
        iv_index: int,
        seq: int,
        src: int,
    ) -> bytes:
        pack_calls.append(
            {
                "opcode": opcode,
                "parameters": parameters,
                "net_key": net_key,
                "iv_index": iv_index,
                "seq": seq,
                "src": src,
            }
        )
        return b"\x02proxy-config"

    monkeypatch.setattr(
        "godox_mesh_bt.controller.pack_proxy_config_pdu",
        fake_pack_proxy_config_pdu,
    )

    BEACON_PDU = b"\x01\x01\x00" + b"\xbb" * 20  # realistic 23-byte Secure Network Beacon

    async def fake_start_notify(characteristic: str, callback) -> None:
        nonlocal notify_callback
        notify_callback = callback
        # Simulate device sending beacon immediately after CCC enable
        async def emit_beacon() -> None:
            await asyncio.sleep(0)
            callback("mock-char", bytearray(BEACON_PDU))
        asyncio.ensure_future(emit_beacon())

    async def fake_write_gatt_char(characteristic: str, data: bytes, *, response: bool) -> None:
        # Proxy config ack only for proxy config writes (type 0x02); not for beacon echo
        if data[0] == 0x02 and notify_callback is not None:
            notify_callback("mock-char", bytearray(b"\x02\x00\x00"))

    mock_client.start_notify.side_effect = fake_start_notify
    mock_client.write_gatt_char.side_effect = fake_write_gatt_char

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
    )

    with caplog.at_level(logging.DEBUG, logger="godox_mesh_bt"):
        await controller.connect()

    assert pack_calls == [
        {
            "opcode": 0x00,
            "parameters": b"\x00",
            "net_key": bytes.fromhex(mesh_state.network_key),
            "iv_index": mesh_state.iv_index,
            "seq": mesh_state.sequence_number,
            "src": mesh_state.provisioner_address,
        },
        {
            "opcode": 0x01,
            "parameters": b"\x00\x01\xff\xff",
            "net_key": bytes.fromhex(mesh_state.network_key),
            "iv_index": mesh_state.iv_index,
            "seq": mesh_state.sequence_number + 1,
            "src": mesh_state.provisioner_address,
        },
    ]
    # 3 writes: beacon echo (type=0x01) + filter type (type=0x02) + whitelist (type=0x02)
    assert mock_client.write_gatt_char.call_args_list == [
        call(
            "00002add-0000-1000-8000-00805f9b34fb",
            BEACON_PDU,
            response=False,
        ),
        call(
            "00002add-0000-1000-8000-00805f9b34fb",
            b"\x02proxy-config",
            response=False,
        ),
        call(
            "00002add-0000-1000-8000-00805f9b34fb",
            b"\x02proxy-config",
            response=False,
        ),
    ]
    assert MeshState.load(state_file).sequence_number == mesh_state.sequence_number + 2
    mock_client.start_notify.assert_awaited_once()
    mock_client.stop_notify.assert_not_awaited()
    assert "proxy client connected" in caplog.text
    assert "proxy config filter type acknowledged" in caplog.text
    assert "proxy config whitelist acknowledged" in caplog.text
    assert "proxy notifications left active for session" in caplog.text

    await controller.disconnect()
    mock_client.stop_notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_controller_connect_skips_beacon_echo_on_timeout(
    tmp_path,
    mesh_state,
    monkeypatch,
    caplog,
) -> None:
    """When no beacon arrives, the controller should warn and continue without echoing."""
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True

    monkeypatch.setattr(
        "godox_mesh_bt.controller.pack_proxy_config_pdu",
        lambda **_: b"\x02proxy-config",
    )

    notify_callback = None

    async def fake_start_notify(characteristic: str, callback) -> None:
        nonlocal notify_callback
        notify_callback = callback
        # No beacon emitted — device does not respond

    async def fake_write_gatt_char(characteristic: str, data: bytes, *, response: bool) -> None:
        # Emit proxy config ack for type-0x02 writes only
        if data[0] == 0x02 and notify_callback is not None:
            notify_callback("mock-char", bytearray(b"\x02\x00\x00"))

    mock_client.start_notify.side_effect = fake_start_notify
    mock_client.write_gatt_char.side_effect = fake_write_gatt_char

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
        # Short beacon timeout so the test does not wait the full two seconds.
        beacon_wait_timeout=0.05,
    )

    with caplog.at_level(logging.WARNING, logger="godox_mesh_bt"):
        await controller.connect()

    assert "no beacon received" in caplog.text
    # Only proxy config writes should happen (no beacon echo)
    writes = mock_client.write_gatt_char.call_args_list
    assert all(call[0][1][0] == 0x02 for call in writes), (
        "expected only proxy config (type=0x02) writes when no beacon received"
    )


@pytest.mark.asyncio
async def test_controller_connect_beacon_notification_does_not_satisfy_proxy_ack(
    tmp_path,
    mesh_state,
    monkeypatch,
    caplog,
) -> None:
    """Beacon notifications (type=0x01) must not be confused with proxy config acks (type=0x02)."""
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True

    monkeypatch.setattr(
        "godox_mesh_bt.controller.pack_proxy_config_pdu",
        lambda **_: b"\x02proxy-config",
    )

    notify_callback = None

    async def fake_start_notify(characteristic: str, callback) -> None:
        nonlocal notify_callback
        notify_callback = callback
        async def emit_beacon() -> None:
            await asyncio.sleep(0)
            callback("mock-char", bytearray(b"\x01\x01\x00" + b"\xcc" * 20))
        asyncio.ensure_future(emit_beacon())

    async def fake_write_gatt_char(characteristic: str, data: bytes, *, response: bool) -> None:
        # Send another beacon on beacon-echo write to verify it doesn't corrupt proxy ack wait
        if data[0] == 0x01 and notify_callback is not None:
            notify_callback("mock-char", bytearray(b"\x01\x01\x00" + b"\xcc" * 20))
        # Send proper proxy config ack for proxy config writes
        if data[0] == 0x02 and notify_callback is not None:
            notify_callback("mock-char", bytearray(b"\x02\x00\x00"))

    mock_client.start_notify.side_effect = fake_start_notify
    mock_client.write_gatt_char.side_effect = fake_write_gatt_char

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
    )

    with caplog.at_level(logging.DEBUG, logger="godox_mesh_bt"):
        await controller.connect()

    assert "proxy config filter type acknowledged" in caplog.text
    assert "proxy config whitelist acknowledged" in caplog.text


@pytest.mark.asyncio
async def test_controller_send_v2_command_logs_payload_and_mesh_destination(
    tmp_path,
    mesh_state,
    caplog,
) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)

    mock_client = MagicMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
    )
    controller._client._client = mock_client

    with caplog.at_level(logging.DEBUG, logger="godox_mesh_bt.controller"):
        await controller.send_v2_command(0xF0, 0, bytes([95, 56, 50, 0, 0]))

    assert "dst=0x0002" in caplog.text
    assert "payload=f05f3832000000" in caplog.text


@pytest.mark.asyncio
async def test_controller_set_params_combines_brightness_and_cct(tmp_path, mesh_state, monkeypatch) -> None:
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    controller = GodoxController("AA:BB:CC:DD:EE:FF", state_file)
    calls: list[tuple[bytes, int | None]] = []

    async def fake_send_payload(payload: bytes, *, dst: int | None = None) -> None:
        calls.append((payload, dst))

    monkeypatch.setattr(controller, "send_payload", fake_send_payload)

    await controller.set_params(brightness=95.5, cct=2900)

    # 95.5 -> percent 95, fractional 5 in the end byte
    # 2900 -> temp 29
    # tint absent -> the legacy centre value 50 and a zeroed signed field
    # dst None -> falls back to the mesh state node_address
    (payload, dst), = calls
    assert payload == build_v2_command(0xF0, 5, bytes([95, 29, 50, 0, 0]))
    assert dst is None


@pytest.mark.asyncio
async def test_controller_set_params_encodes_green_magenta_tint(
    tmp_path, mesh_state, monkeypatch
) -> None:
    """A tint-capable model gets the signed field as well as the legacy one."""
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    controller = GodoxController("AA:BB:CC:DD:EE:FF", state_file)
    payloads: list[bytes] = []

    async def fake_send_payload(payload: bytes, *, dst: int | None = None) -> None:
        payloads.append(payload)

    monkeypatch.setattr(controller, "send_payload", fake_send_payload)

    await controller.set_params(
        brightness=50, cct=5600, gm=-20, supports_gm=True
    )
    # -20 -> 30 in the legacy centred field, 0xEC as a signed byte.
    assert payloads == [
        build_v2_command(0xF0, 0, bytes([50, 56, 30, 0, 0xEC]))
    ]

    payloads.clear()
    # The same tint on a model without a tint range must not reach the wire.
    await controller.set_params(brightness=50, cct=5600, gm=-20)
    assert payloads == [build_v2_command(0xF0, 0, bytes([50, 56, 30, 0, 0]))]


@pytest.mark.asyncio
async def test_controller_connect_proxy_ack_timeout_logs_at_debug_not_warning(
    tmp_path,
    mesh_state,
    monkeypatch,
    caplog,
) -> None:
    """Proxy config ack timeouts are expected (RPL filtering) — must log at DEBUG, not WARNING."""
    state_file = tmp_path / "mesh_state.json"
    mesh_state.save(state_file)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.start_notify = AsyncMock()
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()
    mock_client.is_connected = True

    monkeypatch.setattr(
        "godox_mesh_bt.controller.pack_proxy_config_pdu",
        lambda **_: b"\x02proxy-config",
    )

    async def fake_start_notify(characteristic: str, callback) -> None:
        pass  # No beacon, no proxy acks — device is silent

    mock_client.start_notify.side_effect = fake_start_notify

    controller = GodoxController(
        "AA:BB:CC:DD:EE:FF",
        state_file,
        client_factory=MagicMock(return_value=mock_client),
        beacon_wait_timeout=0.01,
        proxy_config_ack_timeout=0.01,
    )

    # Capture all levels including DEBUG
    with caplog.at_level(logging.DEBUG, logger="godox_mesh_bt.controller"):
        await controller.connect()

    # The ack timeout message must appear at DEBUG level
    ack_timeout_records = [
        r for r in caplog.records
        if "ack not received" in r.message or "filter duplicates" in r.message
    ]
    assert ack_timeout_records, "expected a proxy ack timeout log message"
    for record in ack_timeout_records:
        assert record.levelno == logging.DEBUG, (
            f"proxy ack timeout must log at DEBUG, got {record.levelname}: {record.message}"
        )

    # Must NOT appear at WARNING level
    warning_ack_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and ("acknowledge" in r.message or "ack" in r.message)
    ]
    assert not warning_ack_records, (
        f"proxy ack timeout must not produce WARNING: {[r.message for r in warning_ack_records]}"
    )


@pytest.mark.asyncio
async def test_rebind_delegates_to_config_session(tmp_path, mesh_state) -> None:
    """rebind() delegates to ConfigSession.run()."""
    device_key_state = MeshState(
        network_key=mesh_state.network_key,
        app_key=mesh_state.app_key,
        provisioner_address=mesh_state.provisioner_address,
        node_address=mesh_state.node_address,
        sequence_number=mesh_state.sequence_number,
        iv_index=mesh_state.iv_index,
        device_key="aa" * 16,
    )
    state_file = tmp_path / "mesh_state.json"
    device_key_state.save(state_file)

    ran: list[str] = []

    async def fake_run(self) -> MeshState:
        ran.append(self.address)
        return self._state.next_sequence(2)

    with patch("godox_mesh_bt.config_session.ConfigSession.run", fake_run):
        controller = GodoxController("AA:BB:CC:DD:EE:FF", state_file)
        await controller.rebind()

    assert ran == ["AA:BB:CC:DD:EE:FF"]
    # The sequence numbers the config session consumed are persisted, so the
    # next session cannot replay them into the device's protection list.
    assert MeshState.load(state_file).sequence_number == device_key_state.sequence_number + 2
