#!/usr/bin/env python3
"""Add a RAM-read command to an LK8620 image, for debugging only.

Why
---
The remaining open question about status readback is whether the MCU *sends* the
colour temperature on a panel change and the BLE chip drops it, or never sends
it at all. Godox does not publish the SL200III's MCU firmware, so the only way
to settle it is to look at what actually arrives over the UART — which means
reading the BLE chip's RAM.

This adds one command to the existing 0xFD status handler. Stock firmware
dispatches on the request's first data byte (1 = status, 2 = BLE version,
3 = MCU version) and ignores anything else, and it also ignores request bytes
2..5 entirely. So data byte **4** is free to mean "read memory", with the
address low half taken from bytes 2..3.

    request : FD 04 <lo> <hi> FF FF <end> <crc>
    reply   : B7 <6 bytes read from 0x84_hhll> <crc>

The base is fixed at 0x840000, which covers everything interesting: the status
record cache (0x8435fc), the UART TX/RX queues (0x8436b8 / 0x8436c8), and the
OTA gate flag (0x848804).

This is a debugging image. It is not the readback patch and should not be left
on a light; reflash stock when finished.

Safety
------
The handler is placed in a 194-byte run of 0x00 padding at 0x23f62 that carries
no pointers and no branch targets, rather than over any code. The detour
displaces two non-branch instructions, and the trailing image checksum is
recomputed (without which the light rejects the image with OTA result code 7).

    uv run python tools/lk8620/patch_memread.py stock.bin out.bin
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ota_packets import OtaStream  # noqa: E402
from tc32asm import asm16, tjl  # noqa: E402

STOCK_SHA = "aa56783357eae820be9af03e61d5bea3f816bc0f6e7ea52333411cdd5d7afb0c"

DETOUR = 0x02F3E  # `tmov r0,#0 ; tcmp r3,#3` — two non-branch instructions
RESUME = 0x02F42  # the `tjne` that follows them, which reads our replayed flags
CRC_LOOP = 0x03042  # shared CRC-then-send path used by the version handler
REPLY_BUF = 0x843390  # reply frame: length at +5, body at +6..+12, crc at +13
CRC_TABLE = 0x21BC4
BASE = 0x23F64  # inside the verified-unreferenced padding run
MARKER = 0xB7  # reply sub-command, distinct from every stock record
RAM_BASE_HI = 0x84  # address = 0x840000 | (byte3 << 8 | byte2)


def short_branch(cond_hi: int, addr: int, target: int) -> bytes:
    """Encode a TC32 short conditional branch (0xC0 = eq, 0xC1 = ne)."""
    off = (target - addr - 4) >> 1
    if not -128 <= off <= 127:
        raise ValueError("short branch out of range")
    return struct.pack("<H", (cond_hi << 8) | (off & 0xFF))


def build_handler(base: int) -> bytes:
    """Assemble the memory-read handler laid out at *base*."""
    # Two passes: the pc-relative loads need the literal offsets, which depend
    # on the code length, so lay the code out once to measure it.
    for _ in range(2):
        body: list[bytes] = []

        def emit(text: str) -> None:
            body.append(asm16(text, base + sum(len(b) for b in body)))

        def here() -> int:
            return base + sum(len(b) for b in body)

        # --- not-our-command path -------------------------------------
        emit("tcmp r3, #4")
        br_at = here()
        body.append(b"\x00\x00")  # placeholder for tjeq -> MEM
        emit("tmov r0, #0")  # replayed displaced instruction
        emit("tcmp r3, #3")  # replayed displaced instruction (sets flags)
        body.append(tjl(here(), RESUME))

        mem = here()
        # --- assemble the address: 0x840000 | (r7[3]<<8 | r7[2]) -------
        emit("tloadrb r0, [r7, #2]")
        emit("tloadrb r1, [r7, #3]")
        emit("tshftl r1, r1, #8")
        emit("tor r0, r1")
        emit(f"tmov r1, #{RAM_BASE_HI}")
        emit("tshftl r1, r1, #16")
        emit("tor r0, r1")

        # --- r4 = reply buffer ----------------------------------------
        buf_load_at = here()
        body.append(b"\x00\x00")  # placeholder for tloadr r4, [pc, #..]

        # --- reply sub-command ----------------------------------------
        emit(f"tmov r1, #{MARKER}")
        emit("tstorerb r1, [r4, #6]")

        # --- copy six bytes, unrolled (no loop, nothing to get wrong) --
        for i in range(6):
            emit(f"tloadrb r1, [r0, #{i}]")
            emit(f"tstorerb r1, [r4, #{7 + i}]")

        # --- set up the shared CRC loop and jump into it ---------------
        emit("tadd r5, r4, #0")
        emit("tadd r5, #12")
        emit(f"tmov r0, #{MARKER}")
        emit("tmov r2, #0")
        emit("tadd r3, r4, #6")
        crc_load_at = here()
        body.append(b"\x00\x00")  # placeholder for tloadr r1, [pc, #..]
        body.append(tjl(here(), CRC_LOOP))

        code_len = sum(len(b) for b in body)
        pad = (-(base + code_len)) % 4
        lit_buf = base + code_len + pad
        lit_crc = lit_buf + 4

        # resolve the placeholders
        def pc_rel(at: int, literal: int) -> int:
            return literal - (((at + 4) & ~3))

        blob = bytearray(b"".join(body))
        blob[br_at - base : br_at - base + 2] = short_branch(0xC0, br_at, mem)
        blob[buf_load_at - base : buf_load_at - base + 2] = asm16(
            f"tloadr r4, [pc, #{pc_rel(buf_load_at, lit_buf)}]", buf_load_at
        )
        blob[crc_load_at - base : crc_load_at - base + 2] = asm16(
            f"tloadr r1, [pc, #{pc_rel(crc_load_at, lit_crc)}]", crc_load_at
        )
        out = bytes(blob) + b"\x00" * pad
        out += struct.pack("<I", REPLY_BUF) + struct.pack("<I", CRC_TABLE)
    return out


def trailing_checksum(image: bytes) -> int:
    """Sum of the CRC-16 of every OTA data packet except the last."""
    packets = [
        d
        for k, _t, d in OtaStream(image).operations()
        if k == "write" and d and d[:2] not in (b"\x00\xff", b"\x01\xff", b"\x02\xff")
    ]
    acc = 0
    for packet in packets[:-1]:
        acc = (acc + struct.unpack_from("<H", packet, 18)[0]) & 0xFFFFFFFF
    return acc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()

    stock = args.input.read_bytes()
    digest = hashlib.sha256(stock).hexdigest()
    if digest != STOCK_SHA:
        print(f"refusing: input is not the known stock image ({digest[:16]}…)")
        return 1

    handler = build_handler(BASE)
    if BASE + len(handler) > 0x24024:
        print("refusing: handler would run past the verified padding run")
        return 1

    out = bytearray(stock)
    if any(out[BASE + i] for i in range(len(handler))):
        print("refusing: target region is not all zero padding")
        return 1
    out[BASE : BASE + len(handler)] = handler
    out[DETOUR : DETOUR + 4] = tjl(DETOUR, BASE)
    out[-4:] = struct.pack("<I", trailing_checksum(bytes(out)))

    args.output.write_bytes(bytes(out))
    print(f"handler at        : {BASE:#07x} ({len(handler)} bytes)")
    print(f"detour at         : {DETOUR:#07x} -> tjl {BASE:#07x}")
    print(f"bytes changed     : {sum(1 for a, b in zip(stock, out) if a != b)}")
    print(f"length unchanged  : {len(out) == len(stock)}")
    print(f"output sha256     : {hashlib.sha256(bytes(out)).hexdigest()}")
    print("\nusage: request FD 04 <lo> <hi> FF FF <end>  -> reply B7 + 6 bytes")
    print(f"       address = 0x{RAM_BASE_HI:02x}0000 | (hi << 8 | lo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
