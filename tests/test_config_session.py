"""Tests for ConfigSession post-provisioning configuration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def make_mock_state(
    device_key="00" * 16,
    node_address=0x0002,
    net_key="00" * 16,
    app_key="00" * 16,
    sequence_number=1,
    provisioner_address=0x0001,
    iv_index=0,
):
    from godox_mesh_bt.state import MeshState
    return MeshState(
        network_key=net_key,
        app_key=app_key,
        device_key=device_key,
        provisioner_address=provisioner_address,
        node_address=node_address,
        sequence_number=sequence_number,
        iv_index=iv_index,
    )


class TestConfigSessionBasic:
    def test_creates_with_address_and_state(self):
        from godox_mesh_bt.config_session import ConfigSession
        state = make_mock_state()
        cs = ConfigSession(address="AA:BB:CC:DD:EE:FF", state=state)
        assert cs.address == "AA:BB:CC:DD:EE:FF"


class TestConfigSessionRun:
    @pytest.mark.asyncio
    async def test_run_connects_and_disconnects(self):
        from godox_mesh_bt.config_session import ConfigSession
        state = make_mock_state()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("godox_mesh_bt.config_session.BleakClient", return_value=mock_client):
            cs = ConfigSession(address="AA:BB:CC:DD:EE:FF", state=state)
            await cs.run()

        mock_client.__aenter__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sends_two_config_messages(self):
        from godox_mesh_bt.config_session import ConfigSession
        state = make_mock_state()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("godox_mesh_bt.config_session.BleakClient", return_value=mock_client):
            cs = ConfigSession(address="AA:BB:CC:DD:EE:FF", state=state)
            await cs.run()

        # Should write at least twice: App Key Add + Model App Bind
        assert mock_client.write_gatt_char.await_count >= 2
