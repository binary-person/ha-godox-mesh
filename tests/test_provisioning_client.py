"""Tests for ProvisioningClient BLE bearer."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def make_fake_client(connected=True):
    """Create a minimal fake BleakClient."""
    client = MagicMock()
    client.is_connected = connected
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.disconnect = AsyncMock()
    return client


class TestProvisioningClientInit:
    def test_creates_with_address(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient
        pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
        assert pc.address == "AA:BB:CC:DD:EE:FF"


class TestProvisioningClientConnect:
    @pytest.mark.asyncio
    async def test_connect_starts_notify(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient
        fake = make_fake_client()
        with patch("godox_mesh_bt.provisioning_client.BleakClient", return_value=fake):
            pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
            await pc.connect()
            fake.start_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_called_on_close(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient
        fake = make_fake_client()
        with patch("godox_mesh_bt.provisioning_client.BleakClient", return_value=fake):
            pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
            await pc.connect()
            await pc.disconnect()
            fake.disconnect.assert_awaited_once()


class TestProvisioningClientSend:
    @pytest.mark.asyncio
    async def test_send_writes_to_data_in(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient, PROV_DATA_IN_UUID
        fake = make_fake_client()
        with patch("godox_mesh_bt.provisioning_client.BleakClient", return_value=fake):
            pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
            await pc.connect()
            await pc.send(bytes([0x03, 0x00, 0x00]))
            fake.write_gatt_char.assert_awaited_once_with(
                PROV_DATA_IN_UUID, bytes([0x03, 0x00, 0x00]), response=False
            )


class TestProvisioningClientReceive:
    @pytest.mark.asyncio
    async def test_receive_returns_notification_payload(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient
        fake = make_fake_client()

        # Simulate a notification arriving by injecting it into the queue
        with patch("godox_mesh_bt.provisioning_client.BleakClient", return_value=fake):
            pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
            await pc.connect()
            # Manually put data into the queue (simulating a notification)
            await pc._rx_queue.put(bytes([0x01, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
            result = await pc.receive(timeout=1.0)
            assert result == bytes([0x01, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    @pytest.mark.asyncio
    async def test_receive_times_out(self):
        from godox_mesh_bt.provisioning_client import ProvisioningClient, ProvisioningTimeout
        fake = make_fake_client()
        with patch("godox_mesh_bt.provisioning_client.BleakClient", return_value=fake):
            pc = ProvisioningClient("AA:BB:CC:DD:EE:FF")
            await pc.connect()
            with pytest.raises(ProvisioningTimeout):
                await pc.receive(timeout=0.1)
