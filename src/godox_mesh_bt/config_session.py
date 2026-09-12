"""Post-provisioning configuration session using Config Server model (device key path).

After a device is provisioned its App Key slot is empty.  This module sends
two Config Server messages over the Mesh Proxy connection to bind the app key:

1. **Config AppKey Add** (opcode ``0x00``) — installs the application key.
2. **Config Model App Bind** (opcode ``0x803D``) — binds the key to the
   Telink vendor model (CompanyID ``0x0211``, ModelID ``0x0000``).

Both messages are encrypted with the **device key** (unicast path, TTL=0,
dst=``node_address``).  The device must already be on the Mesh Proxy Service
(UUID ``0x1828``) advertising its network ID beacon before calling
:meth:`ConfigSession.run`.

Data path
---------
::

    Provisioner                         Device
    -----------                         ------
    Config AppKey Add (access PDU) →
                                 ←      Config AppKey Status (ACK, logged)
    Config Model App Bind         →
                                 ←      Config Model App Status (ACK, logged)

Examples
--------
>>> PROXY_DATA_IN_UUID
'00002add-0000-1000-8000-00805f9b34fb'
>>> PROXY_DATA_OUT_UUID
'00002ade-0000-1000-8000-00805f9b34fb'
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

from .config import (
    TELINK_COMPANY_ID,
    TELINK_VENDOR_MODEL_ID,
    build_config_app_key_add,
    build_config_model_app_bind,
)
from .crypto import pack_proxy_device_key_pdu
from .state import MeshState

logger = logging.getLogger(__name__)

PROXY_DATA_IN_UUID = "00002add-0000-1000-8000-00805f9b34fb"
PROXY_DATA_OUT_UUID = "00002ade-0000-1000-8000-00805f9b34fb"


class ConfigSession:
    """Sends Config AppKey Add and Config Model App Bind to a freshly provisioned device.

    Uses the device key (Config Server path) over the mesh proxy connection.

    Parameters
    ----------
    address : str
        BLE device address of the provisioned node, now advertising the
        Mesh Proxy Service (UUID ``0x1828``).
    state : MeshState
        Mesh state containing ``network_key``, ``app_key``, ``device_key``,
        ``provisioner_address``, ``node_address``, and ``sequence_number``.
        ``device_key`` must be non-empty (set by :class:`ProvisioningSession`).

    Examples
    --------
    >>> from godox_mesh_bt.state import MeshState
    >>> state = MeshState("00"*16, "11"*16, 1, 2, 1, 0, device_key="aa"*16)
    >>> session = ConfigSession("AA:BB:CC:DD:EE:FF", state)
    >>> session._state.node_address
    2
    """

    def __init__(
        self,
        address: str,
        state: MeshState,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.address = address
        self._state = state
        self._client_factory = client_factory or BleakClient

    @property
    def final_state(self) -> MeshState:
        """Return the mesh state with the sequence numbers this session used.

        Returns
        -------
        MeshState
            State advanced past every PDU the session sent. Persist this so the
            next session does not reuse a sequence number the device has already
            recorded in its replay protection list.

        Examples
        --------
        >>> from godox_mesh_bt.state import MeshState
        >>> state = MeshState("00"*16, "11"*16, 1, 2, 1, 0, device_key="aa"*16)
        >>> ConfigSession("AA:BB", state).final_state.sequence_number
        1
        """

        return self._state

    async def run(self) -> MeshState:
        """Connect to the proxy, send Config AppKey Add and Model App Bind, then disconnect.

        Returns
        -------
        MeshState
            Mesh state advanced past the two config PDUs that were sent.

        Raises
        ------
        BleakError
            If BLE connection or characteristic access fails.
        """
        async with self._client_factory(self.address) as client:
            await client.start_notify(PROXY_DATA_OUT_UUID, self._on_notify)
            try:
                await self._send_app_key_add(client)
                await asyncio.sleep(0.3)
                await self._send_model_app_bind(client)
                await asyncio.sleep(0.3)
            finally:
                try:
                    await client.stop_notify(PROXY_DATA_OUT_UUID)
                except Exception:
                    pass
        return self._state

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        logger.debug("config ← device: %s", data.hex())

    async def _send_config_pdu(self, client: BleakClient, access_payload: bytes) -> None:
        """Encrypt *access_payload* with the device key and write it as a proxy PDU.

        Parameters
        ----------
        client : BleakClient
            Connected BleakClient on the Mesh Proxy Service.
        access_payload : bytes
            Unencrypted Config Server access-layer payload.
        """
        state = self._state
        proxy_pdu = pack_proxy_device_key_pdu(
            access_payload=access_payload,
            net_key=bytes.fromhex(state.network_key),
            device_key=bytes.fromhex(state.device_key),
            iv_index=state.iv_index,
            seq=state.sequence_number,
            src=state.provisioner_address,
            dst=state.node_address,
        )
        self._state = state.next_sequence()
        logger.info("config → device: %s", proxy_pdu.hex())
        await client.write_gatt_char(PROXY_DATA_IN_UUID, proxy_pdu, response=False)

    async def _send_app_key_add(self, client: BleakClient) -> None:
        payload = build_config_app_key_add(
            net_key_index=0,
            app_key_index=0,
            app_key=bytes.fromhex(self._state.app_key),
        )
        logger.info("config: sending AppKey Add")
        await self._send_config_pdu(client, payload)

    async def _send_model_app_bind(self, client: BleakClient) -> None:
        payload = build_config_model_app_bind(
            element_address=self._state.node_address,
            app_key_index=0,
            company_id=TELINK_COMPANY_ID,
            model_id=TELINK_VENDOR_MODEL_ID,
        )
        logger.info("config: sending Model App Bind")
        await self._send_config_pdu(client, payload)
