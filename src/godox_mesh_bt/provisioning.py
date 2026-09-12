"""BT Mesh provisioning session orchestrator.

This module implements the **Provisioning Protocol** (Mesh Profile spec
§ 5.4) as a six-step state machine over PB-GATT.

Exchange flow
-------------
::

    Provisioner                         Device
    -----------                         ------
    Invite (1 byte)              →
                                 ←      Capabilities (11 bytes)
    Start (5 bytes)              →
    Public Key (64 bytes)        →
                                 ←      Public Key (64 bytes)
    [ECDH shared secret computed]
    Confirmation (16 bytes)      →
                                 ←      Confirmation (16 bytes)
    Random (16 bytes)            →
                                 ←      Random (16 bytes)
    [session key / device key derived]
    Data (33 bytes: 25 + 8 MIC)  →
                                 ←      Complete (0 bytes)

On success, :meth:`ProvisioningSession.run` returns a :class:`~state.MeshState`
with the provisioned network key, derived device key, and a placeholder
app key (``"00" * 16``).  Call ``godox-mesh rebind`` to push the real app
key via the Config Server path.

Examples
--------
>>> session = ProvisioningSession(
...     address="AA:BB:CC:DD:EE:FF",
...     net_key=bytes(16),
...     key_index=0,
...     iv_index=0,
...     unicast_address=0x0002,
... )
>>> session.unicast_address
2
>>> session.provisioner_address
1
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from .crypto import (
    build_confirmation_inputs,
    compute_confirmation_salt,
    compute_confirmation_value,
    compute_provision_salt,
    derive_device_key,
    derive_session_key,
    derive_session_nonce,
    ecdh_shared_secret,
    encrypt_provisioning_data,
    generate_p256_keypair,
)
from .provisioning_client import ProvisioningClient
from .provisioning_pdu import (
    PDU_CAPABILITIES,
    PDU_COMPLETE,
    PDU_CONFIRMATION,
    PDU_PUBLIC_KEY,
    PDU_RANDOM,
    ProvisioningFailed,
    build_confirmation,
    build_data,
    build_invite,
    build_public_key,
    build_random,
    build_start,
    parse_capabilities,
    parse_confirmation,
    parse_pdu,
    parse_public_key,
    parse_random,
)
from .state import MeshState

logger = logging.getLogger(__name__)


class ProvisioningSession:
    """Orchestrates a full BT Mesh PB-GATT provisioning exchange.

    Parameters
    ----------
    address : str
        BLE device address (UUID on macOS, MAC on Linux/Windows) of the
        unprovisioned node advertising the Mesh Provisioning Service
        (UUID ``0x1827``).
    net_key : bytes
        16-byte Network Key to provision into the device.
    key_index : int
        Key index for the Network Key (usually ``0``).
    iv_index : int
        Current IV Index (usually ``0`` for a fresh network).
    unicast_address : int
        Unicast address to assign to the node (e.g. ``0x0002``).
    provisioner_address : int
        Unicast address of this provisioner (default ``0x0001``).
    attention_duration : int
        Attention timer in seconds sent in the Invite PDU (default ``0``).

    Examples
    --------
    >>> session = ProvisioningSession(
    ...     address="AA:BB:CC:DD:EE:FF",
    ...     net_key=bytes(16),
    ...     key_index=0,
    ...     iv_index=0,
    ...     unicast_address=0x0002,
    ... )
    >>> session.unicast_address
    2
    """

    def __init__(
        self,
        address: str,
        net_key: bytes,
        key_index: int,
        iv_index: int,
        unicast_address: int,
        provisioner_address: int = 0x0001,
        attention_duration: int = 0,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.address = address
        self.client_factory = client_factory
        self.net_key = net_key
        self.key_index = key_index
        self.iv_index = iv_index
        self.unicast_address = unicast_address
        self.provisioner_address = provisioner_address
        self.attention_duration = attention_duration

    async def run(self) -> MeshState:
        """Execute the full provisioning exchange.

        Returns
        -------
        MeshState
            Populated state with ``network_key``, ``device_key`` (derived),
            placeholder ``app_key`` (``"00" * 16``), and ``sequence_number=1``.
            Run ``godox-mesh rebind`` afterwards to push the real app key.

        Raises
        ------
        ProvisioningFailed
            If the device sends a Failed PDU at any step.
        ProvisioningTimeout
            If a step receives no response within the timeout window.
        """
        client = ProvisioningClient(self.address, client_factory=self.client_factory)
        await client.connect()
        try:
            return await self._exchange(client)
        finally:
            await client.disconnect()

    async def _recv(self, client: ProvisioningClient, timeout: float = 10.0) -> tuple[int, bytes]:
        """Receive a provisioning PDU, stripping the leading proxy/SAR byte.

        Parameters
        ----------
        client : ProvisioningClient
            Connected provisioning bearer.
        timeout : float
            Seconds to wait for the notification (default 10.0).

        Returns
        -------
        tuple[int, bytes]
            ``(pdu_type, payload)`` with the ``0x03`` proxy prefix removed.
        """
        raw = await client.receive(timeout=timeout)
        logger.debug("provisioning raw rx: %s", raw.hex())
        return parse_pdu(raw[1:])

    async def _exchange(self, client: ProvisioningClient) -> MeshState:
        our_private_key, our_pub_bytes = generate_p256_keypair()

        # Step 1: Invite → Capabilities
        invite_payload = bytes([self.attention_duration])
        await client.send(build_invite(self.attention_duration))
        logger.info("provisioning: sent Invite")

        raw = await client.receive()
        logger.debug("provisioning raw rx: %s", raw.hex())
        pdu_type, payload = parse_pdu(raw[1:])
        if pdu_type != PDU_CAPABILITIES:
            raise ProvisioningFailed(0xFF)
        caps = parse_capabilities(payload)
        logger.info("provisioning: received Capabilities (elements=%d)", caps.num_elements)
        # Kept, not just logged: the address allocator needs it, and assuming a
        # fixed count collides on any model that reports a different one.
        num_elements = max(1, caps.num_elements)

        # Step 2: Start + PublicKey → device PublicKey
        start_payload = bytes([0x00, 0x00, 0x00, 0x00, 0x00])  # No OOB
        await client.send(build_start())
        await client.send(build_public_key(our_pub_bytes))
        logger.info("provisioning: sent Start + PublicKey")

        raw = await client.receive()
        pdu_type, payload = parse_pdu(raw[1:])
        if pdu_type != PDU_PUBLIC_KEY:
            raise ProvisioningFailed(0xFF)
        dev_pub_bytes = parse_public_key(payload)
        logger.info("provisioning: received device PublicKey")

        # Step 3: ECDH + Confirmation
        ecdh_secret = ecdh_shared_secret(our_private_key, dev_pub_bytes)
        conf_inputs = build_confirmation_inputs(
            invite_payload, caps.raw, start_payload, our_pub_bytes, dev_pub_bytes
        )
        conf_salt = compute_confirmation_salt(conf_inputs)
        prov_random = os.urandom(16)
        our_confirmation = compute_confirmation_value(ecdh_secret, conf_salt, prov_random)

        await client.send(build_confirmation(our_confirmation))
        logger.info("provisioning: sent Confirmation")

        raw = await client.receive()
        pdu_type, payload = parse_pdu(raw[1:])
        if pdu_type != PDU_CONFIRMATION:
            raise ProvisioningFailed(0xFF)
        dev_confirmation = parse_confirmation(payload)
        logger.info("provisioning: received device Confirmation")

        # Step 4: Random exchange
        await client.send(build_random(prov_random))
        logger.info("provisioning: sent Random")

        raw = await client.receive()
        pdu_type, payload = parse_pdu(raw[1:])
        if pdu_type != PDU_RANDOM:
            raise ProvisioningFailed(0xFF)
        dev_random = parse_random(payload)
        logger.info("provisioning: received device Random")

        # Verify device confirmation
        dev_conf_check = compute_confirmation_value(ecdh_secret, conf_salt, dev_random)
        if dev_conf_check != dev_confirmation:
            logger.warning("provisioning: device confirmation MISMATCH — proceeding anyway")

        # Step 5: Derive keys + send Data
        prov_salt = compute_provision_salt(conf_salt, prov_random, dev_random)
        session_key = derive_session_key(ecdh_secret, prov_salt)
        session_nonce = derive_session_nonce(ecdh_secret, prov_salt)
        device_key = derive_device_key(ecdh_secret, prov_salt)

        encrypted_data = encrypt_provisioning_data(
            self.net_key, self.key_index, 0, self.iv_index,
            self.unicast_address, session_key, session_nonce,
        )
        await client.send(build_data(encrypted_data))
        logger.info("provisioning: sent Data")

        # Step 6: Wait for Complete
        raw = await client.receive()
        pdu_type, _ = parse_pdu(raw[1:])
        if pdu_type != PDU_COMPLETE:
            raise ProvisioningFailed(0xFF)
        logger.info("provisioning: received Complete — provisioning succeeded!")

        return MeshState(
            network_key=self.net_key.hex(),
            app_key="00" * 16,  # placeholder; set via Config AppKey Add
            device_key=device_key.hex(),
            provisioner_address=self.provisioner_address,
            node_address=self.unicast_address,
            iv_index=self.iv_index,
            sequence_number=1,
            num_elements=num_elements,
        )
