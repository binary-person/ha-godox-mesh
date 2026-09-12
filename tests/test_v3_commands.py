from __future__ import annotations

from godox_mesh_bt.protocol import build_v3_command


def test_build_v3_command() -> None:
    # cmd=0xFC (252), data=[0x01, 0x02]
    command = build_v3_command(0xFC, bytes([0x01, 0x02]))
    # Expected: FC 05 01 02 <CRC8>
    # Length = 2 (data) + 3 = 5
    from godox_mesh_bt.crypto import checksum
    expected_crc = checksum(bytes.fromhex("FC050102"))
    assert command.hex().upper() == f"FC050102{expected_crc:02X}"
