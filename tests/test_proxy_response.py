from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from godox_mesh_bt.client import ProxyClient
from godox_mesh_bt.crypto import k2


@pytest.mark.asyncio
async def test_proxy_client_response_decryption() -> None:
    address = "AA:BB:CC:DD:EE:FF"
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    iv_index = 0
    
    # Pre-generate a response PDU (e.g. Status On)
    # I'll just use the same logic as pack_proxy_network_pdu for simplicity here
    from godox_mesh_bt.crypto import pack_proxy_network_pdu
    # Let's say Godox Response Payload: Status On
    godox_response = bytes.fromhex("FE00FFFFFFFFFF7F") 
    vendor_opcode_res = bytes.fromhex("F11102") # Opcode F1
    
    response_pdu = pack_proxy_network_pdu(
        vendor_opcode_res + godox_response,
        net_key,
        app_key,
        iv_index=0,
        seq=100,
        src=0x0002, # From light
        dst=0x0001, # To us
        ttl=4
    )
    
    mock_client = MagicMock()
    mock_client.is_connected = True
    mock_client.start_notify = AsyncMock()

    client = ProxyClient(address, client_factory=MagicMock(return_value=mock_client))
    client._client = mock_client
    
    # Callback to receive decrypted payload
    received_payloads = []
    def on_payload(payload: bytes) -> None:
        received_payloads.append(payload)
    
    # We need a way for ProxyClient to decrypt using keys
    # I'll add a 'handler' that decrypts
    from godox_mesh_bt.crypto import deobfuscate, decrypt_network_pdu, decrypt_access_pdu
    
    def notification_handler(data: bytes) -> None:
        # 1. Strip Proxy header
        network_pdu = data[1:]
        # 2. Deobfuscate
        _, _, priv_key = k2(net_key)
        deobf = deobfuscate(network_pdu, priv_key, iv_index)
        # 3. Decrypt Network
        _, enc_key, _ = k2(net_key)
        dst, encrypted_access = decrypt_network_pdu(deobf, enc_key, iv_index)
        # 4. Decrypt Access
        seq = int.from_bytes(deobf[2:5], "big")
        src = int.from_bytes(deobf[5:7], "big")
        access_payload = decrypt_access_pdu(encrypted_access, app_key, iv_index, seq, src, dst)
        on_payload(access_payload)

    await client.start_notify(notification_handler)
    
    # Simulate notification
    # Find the bleak callback passed to start_notify
    bleak_cb = mock_client.start_notify.call_args[0][1]
    bleak_cb("proxy-char", bytearray(response_pdu))
    
    assert len(received_payloads) == 1
    assert received_payloads[0].hex().upper() == (vendor_opcode_res + godox_response).hex().upper()


def test_decrypt_proxy_network_pdu_returns_access_payload() -> None:
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    godox_response = bytes.fromhex("FE00FFFFFFFFFF7F")
    vendor_opcode_res = bytes.fromhex("F11102")

    from godox_mesh_bt.crypto import decrypt_proxy_network_pdu, pack_proxy_network_pdu

    response_pdu = pack_proxy_network_pdu(
        vendor_opcode_res + godox_response,
        net_key,
        app_key,
        iv_index=0,
        seq=100,
        src=0x0002,
        dst=0x0001,
        ttl=4,
    )

    decrypted = decrypt_proxy_network_pdu(
        response_pdu,
        net_key,
        app_key,
        iv_index=0,
    )

    assert decrypted.src == 0x0002
    assert decrypted.dst == 0x0001
    assert decrypted.seq == 100
    assert decrypted.access_payload == vendor_opcode_res + godox_response
