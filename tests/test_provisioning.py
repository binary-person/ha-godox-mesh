"""Tests for ProvisioningSession state machine."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def make_mock_client():
    """Create a mock ProvisioningClient."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.send = AsyncMock()
    return client


def make_capabilities_pdu():
    """Capabilities PDU as received from device: proxy(1) + pdu_type(1) + payload(11)."""
    return bytes([0x03, 0x01]) + bytes([0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def make_pubkey_pdu(pub_bytes=None):
    """Device public key PDU: proxy(1) + pdu_type(1) + 64 bytes."""
    if pub_bytes is None:
        pub_bytes = bytes(64)
    return bytes([0x03, 0x03]) + pub_bytes


def make_confirmation_pdu():
    return bytes([0x03, 0x05]) + bytes(16)


def make_random_pdu():
    return bytes([0x03, 0x06]) + bytes(16)


def make_complete_pdu():
    return bytes([0x03, 0x08])


class TestProvisioningSessionBasic:
    def test_creates_with_params(self):
        from godox_mesh_bt.provisioning import ProvisioningSession
        ps = ProvisioningSession(
            address="AA:BB:CC:DD:EE:FF",
            net_key=bytes(16),
            key_index=0,
            iv_index=0,
            unicast_address=0x0002,
        )
        assert ps.address == "AA:BB:CC:DD:EE:FF"

    def test_unicast_address_stored(self):
        from godox_mesh_bt.provisioning import ProvisioningSession
        ps = ProvisioningSession(
            address="AA:BB:CC:DD:EE:FF",
            net_key=bytes(16),
            key_index=0,
            iv_index=0,
            unicast_address=0x0003,
        )
        assert ps.unicast_address == 0x0003


class TestProvisioningSessionRun:
    def _make_session_with_mock_client(self, mock_client, responses):
        """Create a ProvisioningSession that uses a mock client returning given responses."""
        responses_iter = iter(responses)
        mock_client.receive = AsyncMock(side_effect=lambda timeout=10.0: asyncio.coroutine(lambda: next(responses_iter))())

        async def fake_receive(timeout=10.0):
            return next(responses_iter)

        mock_client.receive = AsyncMock(side_effect=fake_receive)
        return mock_client

    @pytest.mark.asyncio
    async def test_provision_sends_invite_first(self):
        from godox_mesh_bt.provisioning import ProvisioningSession
        mock_client = make_mock_client()
        responses = [
            make_capabilities_pdu(),
            make_pubkey_pdu(),
            make_confirmation_pdu(),
            make_random_pdu(),
            make_complete_pdu(),
        ]
        mock_client.receive = AsyncMock(side_effect=responses)

        with patch("godox_mesh_bt.provisioning.ProvisioningClient", return_value=mock_client), \
             patch("godox_mesh_bt.provisioning.generate_p256_keypair", return_value=(MagicMock(), bytes(64))), \
             patch("godox_mesh_bt.provisioning.ecdh_shared_secret", return_value=bytes(32)):
            ps = ProvisioningSession(
                address="AA:BB:CC:DD:EE:FF",
                net_key=bytes(16),
                key_index=0,
                iv_index=0,
                unicast_address=0x0002,
            )
            await ps.run()

        # First send should be the Invite PDU
        first_call_args = mock_client.send.call_args_list[0][0][0]
        assert first_call_args[0] == 0x03  # proxy type
        assert first_call_args[1] == 0x00  # Invite pdu_type

    @pytest.mark.asyncio
    async def test_provision_returns_mesh_state(self):
        from godox_mesh_bt.provisioning import ProvisioningSession
        from godox_mesh_bt.state import MeshState
        mock_client = make_mock_client()
        responses = [
            make_capabilities_pdu(),
            make_pubkey_pdu(),
            make_confirmation_pdu(),
            make_random_pdu(),
            make_complete_pdu(),
        ]
        mock_client.receive = AsyncMock(side_effect=responses)

        with patch("godox_mesh_bt.provisioning.ProvisioningClient", return_value=mock_client), \
             patch("godox_mesh_bt.provisioning.generate_p256_keypair", return_value=(MagicMock(), bytes(64))), \
             patch("godox_mesh_bt.provisioning.ecdh_shared_secret", return_value=bytes(32)):
            ps = ProvisioningSession(
                address="AA:BB:CC:DD:EE:FF",
                net_key=bytes(16),
                key_index=0,
                iv_index=0,
                unicast_address=0x0002,
            )
            result = await ps.run()

        assert isinstance(result, MeshState)
        assert result.node_address == 0x0002

    @pytest.mark.asyncio
    async def test_provision_sends_five_pdus(self):
        """Provisioner sends: Invite, Start, PublicKey, Confirmation, Random, Data = 6 total."""
        from godox_mesh_bt.provisioning import ProvisioningSession
        mock_client = make_mock_client()
        responses = [
            make_capabilities_pdu(),
            make_pubkey_pdu(),
            make_confirmation_pdu(),
            make_random_pdu(),
            make_complete_pdu(),
        ]
        mock_client.receive = AsyncMock(side_effect=responses)

        with patch("godox_mesh_bt.provisioning.ProvisioningClient", return_value=mock_client), \
             patch("godox_mesh_bt.provisioning.generate_p256_keypair", return_value=(MagicMock(), bytes(64))), \
             patch("godox_mesh_bt.provisioning.ecdh_shared_secret", return_value=bytes(32)):
            ps = ProvisioningSession(
                address="AA:BB:CC:DD:EE:FF",
                net_key=bytes(16),
                key_index=0,
                iv_index=0,
                unicast_address=0x0002,
            )
            await ps.run()

        assert mock_client.send.call_count == 6  # invite, start, pubkey, confirmation, random, data
