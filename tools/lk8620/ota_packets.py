#!/usr/bin/env python3
"""Reproduce the Telink GATT-OTA packet stream exactly as the Godox app builds it.

Ported line-for-line from `com/telink/ble/mesh/util/OtaPacketParser.java` in the
decompiled Godox Light 4.1.0 APK. This module is transport-agnostic: it emits the
byte sequence, so it can drive a real flash or prove equivalence against the app.

The wire format (per data packet, 20 bytes):
    [0..1]  index, little-endian u16
    [2..17] 16 payload bytes (0xFF-padded in the final short packet)
    [18..19] CRC-16 little-endian, over bytes [0 .. len-3]

The command framing around the data stream:
    start-of-conversation reads/writes are 0xFF00 (version) and 0xFF01 (start);
    the end command is 0xFF02 followed by the last index and its ones-complement.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


def crc16(buf: bytes) -> int:
    """CRC-16 exactly as OtaPacketParser.crc16 — reflected, poly 0xA001, init
    0xFFFF, computed over every byte except the final two."""
    length = len(buf) - 2
    table = (0x0000, 0xA001)          # short[]{0, -24575} in the Java source
    crc = 0xFFFF
    for i in range(length):
        b = buf[i]
        for _ in range(8):
            crc = (table[(crc ^ b) & 1] & 0xFFFF) ^ (crc >> 1)
            b >>= 1
    return crc & 0xFFFF


def total_packets(n: int) -> int:
    return n // 16 if n % 16 == 0 else n // 16 + 1


def data_packet(image: bytes, index: int) -> bytes:
    """Build one 20-byte data packet — a direct port of getPacket()."""
    total = total_packets(len(image))
    if not (0 <= index < total):
        return b""
    length = len(image)
    if length > 16:
        length = length - index * 16 if index + 1 == total else 16
    pkt = bytearray(b"\xFF" * 20)
    pkt[2:2 + length] = image[index * 16: index * 16 + length]
    struct.pack_into("<H", pkt, 0, index)              # fillIndex
    struct.pack_into("<H", pkt, 18, crc16(bytes(pkt))) # fillCrc
    return bytes(pkt)


def end_command(last_index: int) -> bytes:
    """0xFF02 + last index + ones-complement — a port of b.i()."""
    inv = ~last_index
    return bytes([0x02, 0xFF,
                  last_index & 0xFF, (last_index >> 8) & 0xFF,
                  inv & 0xFF, (inv >> 8) & 0xFF])


CMD_VERSION = bytes([0x00, 0xFF])   # b.h()  — CMD_OTA_FW_VERSION
CMD_START   = bytes([0x01, 0xFF])   # b.j()  — CMD_OTA_START


@dataclass
class OtaStream:
    """The full ordered sequence of GATT operations, as the app performs them."""
    image: bytes

    def operations(self):
        """Yield ('write'|'read', characteristic, bytes|None) in app order."""
        yield ("write", "OTA", CMD_VERSION)          # 00 FF
        yield ("write", "OTA", CMD_START)            # 01 FF
        yield ("read",  "OTA", None)                 # handshake read (discarded)
        n = total_packets(len(self.image))
        for i in range(n):
            yield ("write", "OTA", data_packet(self.image, i))
        yield ("write", "OTA", end_command(n - 1))   # 02 FF <idx> <~idx>


if __name__ == "__main__":
    import sys
    import hashlib
    img = open(sys.argv[1], "rb").read()
    s = OtaStream(img)
    ops = list(s.operations())
    writes = [b for k, _, b in ops if k == "write"]
    dstream = b"".join(writes)
    print(f"image      : {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()[:16]}…")
    print(f"packets    : {total_packets(len(img))} data + version/start/end")
    print(f"operations : {len(ops)} ({sum(1 for k,_,_ in ops if k=='write')} writes, "
          f"{sum(1 for k,_,_ in ops if k=='read')} read)")
    print(f"first data : {ops[3][2].hex()}")
    print(f"last  data : {[b for k,_,b in ops if k=='write'][-2].hex()}")
    print(f"end  cmd   : {ops[-1][2].hex()}")
    print(f"stream sha : {hashlib.sha256(dstream).hexdigest()}")
