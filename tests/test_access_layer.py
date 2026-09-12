from __future__ import annotations

from pathlib import Path

REFERENCE_PDU_PATH = Path(__file__).parent / "fixtures" / "reference-pdu.hex"


def test_build_vendor_access_payload() -> None:
    # Godox V2 payload (Power On)
    godox_payload = bytes.fromhex("FE00FFFFFFFFFF7F")
    # Vendor Opcode 135664
    opcode = 135664

    # Combined Access Payload = Opcode(3) + Godox(8)
    from godox_mesh_bt.crypto import build_vendor_access_payload

    access_payload = build_vendor_access_payload(opcode, godox_payload)

    assert access_payload.hex().upper() == "F01102FE00FFFFFFFFFF7F"


def test_pack_vendor_proxy_pdu() -> None:
    from godox_mesh_bt.crypto import pack_proxy_network_pdu

    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    godox_payload = bytes.fromhex("FE00FFFFFFFFFF7F")
    opcode = 135664

    from godox_mesh_bt.crypto import build_vendor_access_payload

    access_payload = build_vendor_access_payload(opcode, godox_payload)

    proxy_pdu = pack_proxy_network_pdu(
        access_payload,
        net_key,
        app_key,
        iv_index=0,
        seq=1,
        src=0x0001,
        dst=0x0002,
        ttl=4,
    )

    expected_hex = REFERENCE_PDU_PATH.read_text().strip()
    assert proxy_pdu.hex().lower() == expected_hex.lower()
