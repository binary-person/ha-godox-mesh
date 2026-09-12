from __future__ import annotations

from pathlib import Path

import pytest

from godox_mesh_bt.crypto import (
    k2,
)

REFERENCE_PDU_PATH = Path(__file__).parent / "fixtures" / "reference-pdu.hex"


def test_pack_reference_pdu() -> None:
    # 1. Setup fields
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    nid, enc_key, priv_key = k2(net_key)
    iv_index = 0
    seq_val = 1
    src_val = 0x0001
    dst_val = 0x0002
    ttl = 4
    godox_payload = bytes.fromhex("FE00FFFFFFFFFF7F")
    vendor_opcode = bytes.fromhex("F01102")  # Opcode F0, Company 0211

    # 2. Encrypt Access PDU
    from godox_mesh_bt.crypto import encrypt_access_pdu
    encrypted_access = encrypt_access_pdu(
        vendor_opcode + godox_payload, app_key, iv_index, seq_val, src_val, dst_val
    )

    # 3. Encrypt Network PDU
    from godox_mesh_bt.crypto import encrypt_network_pdu
    encrypted_net = encrypt_network_pdu(
        dst_val, encrypted_access, enc_key, iv_index, seq_val, src_val, ttl
    )

    # 4. Assemble Network PDU
    byte0 = nid  # IVI=0
    assert bytes([byte0]) + encrypted_net

    # 5. Obfuscate
    # Wait, encrypt_network_pdu should probably return the whole encrypted part
    # including CTL/TTL, SEQ, SRC?
    # No, usually packing handles the whole assembly.

    # 6. Proxy wrapping
    # ... I'll refine the implementation in crypto.py to make this easier
    from godox_mesh_bt.crypto import pack_proxy_network_pdu
    proxy_pdu = pack_proxy_network_pdu(
        vendor_opcode + godox_payload,
        net_key,
        app_key,
        iv_index,
        seq_val,
        src_val,
        dst_val,
        ttl,
    )

    expected_hex = REFERENCE_PDU_PATH.read_text().strip()
    assert proxy_pdu.hex().lower() == expected_hex.lower()


def test_pack_real_captured_app_pdu_matches_telink_bytes() -> None:
    from godox_mesh_bt.crypto import pack_proxy_network_pdu

    net_key = bytes.fromhex("bc6bf99f840f9ca379562468a110d2a9")
    app_key = bytes.fromhex("9f360e3d7d27a7e9aa56ab7342f1e3e4")
    access_payload = bytes.fromhex("f01102f03237320000005d")

    proxy_pdu = pack_proxy_network_pdu(
        access_payload,
        net_key,
        app_key,
        iv_index=0,
        seq=281,
        src=0x0001,
        dst=0x0002,
        ttl=10,
    )

    assert proxy_pdu.hex().upper() == "004CC042109F0DAA08624DF8F6A62B653F5F86892663CFB98C3F4E702F56"


@pytest.mark.parametrize(
    ("seq", "access_payload_hex", "proxy_pdu_hex"),
    [
        (281, "f01102f03237320000005d", "004CC042109F0DAA08624DF8F6A62B653F5F86892663CFB98C3F4E702F56"),
        (282, "f01102f0322b3200000000", "004CB3256CDA1B767D413600ACDE806BB60955F61ECA03E1680627BECC25"),
        (283, "f01102f032203200000070", "004CAD7A74BE22FBDBF0C1884B7854557CD9394437408DA50052C4CBD782"),
        (284, "f01102f0322b3200000000", "004C9585E7129B001DBE8D562F32531DDCBCBF174B0CEA7DEE14705C0101"),
        (285, "f01102f0324132000000ac", "004C582A07AE90433AB45EA9DD069D9A2DF6A6EC35A3B4E3307A0C5E6DEA"),
        (286, "f01102f001413200000018", "004C5B2FAEC6F75608B5B98FDD005AF5FB54160C34DBA6668239430B8AAF"),
    ],
)
def test_pack_real_captured_app_sequence_matches_telink_bytes(
    seq: int,
    access_payload_hex: str,
    proxy_pdu_hex: str,
) -> None:
    from godox_mesh_bt.crypto import pack_proxy_network_pdu

    proxy_pdu = pack_proxy_network_pdu(
        bytes.fromhex(access_payload_hex),
        bytes.fromhex("bc6bf99f840f9ca379562468a110d2a9"),
        bytes.fromhex("9f360e3d7d27a7e9aa56ab7342f1e3e4"),
        iv_index=0,
        seq=seq,
        src=0x0001,
        dst=0x0002,
        ttl=10,
    )

    assert proxy_pdu.hex().upper() == proxy_pdu_hex
