from __future__ import annotations

from pathlib import Path

from godox_mesh_bt.crypto import (
    k2,
)

REFERENCE_PDU_PATH = Path(__file__).parent / "fixtures" / "reference-pdu.hex"


def test_decode_reference_pdu() -> None:
    # Load reference PDU
    pdu_hex = REFERENCE_PDU_PATH.read_text().strip()
    pdu = bytes.fromhex(pdu_hex)

    # Reference keys
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    nid, enc_key, priv_key = k2(net_key)
    iv_index = 0

    # 1. Strip Proxy SAR/Type byte
    assert pdu[0] == 0x00
    network_pdu = pdu[1:]

    # 2. Check NID
    assert network_pdu[0] & 0x7F == nid
    assert network_pdu[0] >> 7 == 0  # IVI

    # 3. De-obfuscate
    # I'll need to implement deobfuscate in crypto.py
    from godox_mesh_bt.crypto import deobfuscate
    deobf_pdu = deobfuscate(network_pdu, priv_key, iv_index)

    # Header: CTL/TTL, SEQ, SRC
    ctl_ttl = deobf_pdu[1]
    assert ctl_ttl >> 7 == 0  # CTL
    assert ctl_ttl & 0x7F == 4  # TTL
    seq = int.from_bytes(deobf_pdu[2:5], "big")
    assert seq == 1
    src = int.from_bytes(deobf_pdu[5:7], "big")
    assert src == 0x0001

    # 4. Decrypt Network PDU
    # I'll need to implement network PDU decryption in crypto.py
    from godox_mesh_bt.crypto import decrypt_network_pdu
    dst, encrypted_access = decrypt_network_pdu(deobf_pdu, enc_key, iv_index)
    assert dst == 0x0002

    # 5. Decrypt Access PDU
    from godox_mesh_bt.crypto import decrypt_access_pdu
    access_payload = decrypt_access_pdu(encrypted_access, app_key, iv_index, seq, src, dst)

    # 6. Verify Godox Payload
    # Opcode (3 bytes) + Godox Payload (8 bytes)
    assert access_payload.hex().upper() == "F01102FE00FFFFFFFFFF7F"
