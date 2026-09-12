from __future__ import annotations

from godox_mesh_bt.crypto import encode_vendor_opcode


def test_encode_vendor_opcode() -> None:
    # Godox opcode 135664 (0x211F0)
    opcode = 135664
    encoded = encode_vendor_opcode(opcode)
    assert encoded.hex().upper() == "F01102"
