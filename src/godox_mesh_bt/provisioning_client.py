"""BLE bearer for BT Mesh PB-GATT provisioning.

This module implements the **Provisioning Bearer** layer of the BT Mesh
PB-GATT bearer (Mesh Profile spec § 5.3.1).

The device exposes the **Mesh Provisioning Service** (UUID ``0x1827``) with
two characteristics:

* ``0x2ADB`` — Provisioning Data In (Write Without Response)
* ``0x2ADC`` — Provisioning Data Out (Notify)

Notifications from ``0x2ADC`` carry a proxy/SAR header byte (``0x03``) as
the first byte, followed by the provisioning PDU type and payload.  This
header must be stripped before passing bytes to :func:`parse_pdu`.

Examples
--------
>>> PROV_DATA_IN_UUID
'00002adb-0000-1000-8000-00805f9b34fb'
>>> PROV_DATA_OUT_UUID
'00002adc-0000-1000-8000-00805f9b34fb'
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

logger = logging.getLogger(__name__)

PROV_DATA_IN_UUID  = "00002adb-0000-1000-8000-00805f9b34fb"
PROV_DATA_OUT_UUID = "00002adc-0000-1000-8000-00805f9b34fb"


class ProvisioningTimeout(Exception):
    """Raised when no provisioning PDU arrives within the expected time window.

    Examples
    --------
    >>> raise ProvisioningTimeout("No PDU received within 10s")
    Traceback (most recent call last):
        ...
    godox_mesh_bt.provisioning_client.ProvisioningTimeout: No PDU received within 10s
    """


class ProvisioningClient:
    """Low-level PB-GATT bearer for sending and receiving provisioning PDUs.

    Connects to the Mesh Provisioning Service (UUID ``0x1827``), subscribes
    to notifications on ``0x2ADC``, and writes PDUs to ``0x2ADB`` using
    Write-Without-Response.

    Parameters
    ----------
    address : str
        BLE device address or UUID (platform-dependent format).

    Examples
    --------
    >>> client = ProvisioningClient("AA:BB:CC:DD:EE:FF")
    >>> client.address
    'AA:BB:CC:DD:EE:FF'
    >>> client._client is None
    True
    """

    def __init__(
        self,
        address: str,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.address = address
        self._client_factory = client_factory or BleakClient
        self._client: BleakClient | None = None
        self._rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> None:
        logger.info("provisioning client connecting to %s", self.address)
        self._client = self._client_factory(self.address)
        await self._client.__aenter__()
        await self._client.start_notify(PROV_DATA_OUT_UUID, self._on_notification)
        logger.info("provisioning client connected")

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.stop_notify(PROV_DATA_OUT_UUID)
        except Exception:
            pass
        await self._client.disconnect()
        logger.info("provisioning client disconnected")

    async def send(self, pdu: bytes) -> None:
        """Write a PDU to the Provisioning Data In characteristic (0x2ADB).

        Parameters
        ----------
        pdu : bytes
            Complete proxy-framed PDU (e.g. from :func:`build_invite`).
            Written using Write Without Response.

        Raises
        ------
        RuntimeError
            If called before :meth:`connect`.
        """
        if self._client is None:
            raise RuntimeError("Not connected")
        logger.debug("provisioning → device: %s", pdu.hex())
        await self._client.write_gatt_char(PROV_DATA_IN_UUID, pdu, response=False)

    async def receive(self, timeout: float = 10.0) -> bytes:
        """Wait for and return the next notification from the device.

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait (default 10.0).

        Returns
        -------
        bytes
            Raw notification bytes including the leading proxy/SAR byte
            (``0x03``).  Strip with ``raw[1:]`` before passing to
            :func:`parse_pdu`.

        Raises
        ------
        ProvisioningTimeout
            If no notification arrives within *timeout* seconds.
        """
        try:
            data = await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ProvisioningTimeout(f"No PDU received within {timeout}s") from exc
        logger.debug("provisioning ← device: %s", data.hex())
        return data

    def _on_notification(self, _handle: int, data: bytearray) -> None:
        self._rx_queue.put_nowait(bytes(data))
