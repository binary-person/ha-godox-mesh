#!/usr/bin/env python3
"""Patch a Godox Telink mesh firmware (LK8620 / LK8720 / LK8728B) for live status.

Stock firmware answers vendor status request 0xFD from a RAM cache seeded with a
boot default and refilled only when the MCU volunteers a frame, so a query
would return stale data -- which hardware disproved, see
docs/readback-hardware-findings.md. This applies one
combined patch:

  1. Readback — the 0xFD handler also FORWARDS the request to the MCU, whose
     live reply refills the cache. A query then returns current state (the
     cache answer lags one query; see the docs).
  2. Refresh-on-change — when the MCU signals a panel change (0xB0), the BT chip
     sends a 0xFD to the MCU so the live value is cached proactively. Panel
     changes then show up on the client's next read with no lag.
  3. Reversibility — the reported BLE version is set to 1 so the official Godox
     app offers stock firmware as an "update", i.e. an easy restore path.

Flashing is hardware-proven; the patch itself delivers no observable change. See docs/lk8620-flashing.md.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

# Per-chip anchors, from disassembly (docs/bt-chip-firmware.md):
#   fd_detour   : local 0xFD handler entry (overwritten with a call to trampoline A)
#   fd_argsetup : start of the stock forward-path register setup (copied verbatim)
#   enqueue     : the UART enqueue-to-MCU primitive
#   b0_handler  : the 0xB0-A0 handler entry (overwritten with a call to trampoline B)
CHIPS = {
    "LK8620":  dict(fd_detour=0x02F34, fd_argsetup=0x02ECA, enqueue=0x0E548, b0_handler=0x03282),
    "LK8720":  dict(fd_detour=0x02F9E, fd_argsetup=0x02F34, enqueue=0x0E618, b0_handler=0x03336),
    "LK8728B": dict(fd_detour=0x02F34, fd_argsetup=0x02ECA, enqueue=0x0E5D0, b0_handler=0x03286),
}
SHA_TO_CHIP = {
    "aa56783357eae820be9af03e61d5bea3f816bc0f6e7ea52333411cdd5d7afb0c": "LK8620",
    "cc8c48fc0d039f393298d611b2848e4fb882f8935a466bce641818de4efcea6d": "LK8720",
    "ffba898f84c6bfc6d644c210b602082dbad3234dae3a8c0b06d012225ca36df3": "LK8728B",
}
ARGSETUP_LEN = 10
FD_FRAME = bytes.fromhex("fd01ffffffffa095")   # V2 status request selecting the A0 record
# The end byte selects WHICH record the light reports. Hardware testing showed a
# request padded with 0xFF gets no reply at all; 0xA0 is the record that answers.


def tjl(src: int, dst: int) -> bytes:
    off = (dst - (src + 4)) >> 1
    return struct.pack("<HH", 0x9000 | ((off >> 11) & 0x7FF), 0x9800 | (off & 0x7FF))

def tmov(reg: int, imm: int) -> bytes:
    return struct.pack("<H", 0xA000 | (reg << 8) | (imm & 0xFF))

def tadd_pc(reg: int, words: int) -> bytes:
    return struct.pack("<H", 0x7000 | (reg << 8) | words)


def find_dead_sites(img: bytes, count: int, need: int = 40) -> list[int]:
    """Return `count` non-overlapping Telink demo-opcode handlers (vendor model
    0x0001, which the composition data does not bind) with `need` bytes each."""
    ptrs = sorted({struct.unpack_from("<I", img, o)[0] & ~1
                   for o in range(0x23600, 0x23A00, 4)
                   if 0x8000 < struct.unpack_from("<I", img, o)[0] < 0x20000
                   and struct.unpack_from("<I", img, o)[0] & 1})
    out: list[int] = []
    for i, h in enumerate(ptrs):
        nxt = ptrs[i + 1] if i + 1 < len(ptrs) else h + 0x400
        if nxt - h >= need and all(abs(h - s) >= need for s in out):
            out.append(h)
            if len(out) == count:
                break
    return out


def find_version_byte(img: bytes) -> int | None:
    want = bytes([img[0x23], 0xA3, 0xA3, 0x42])   # tmov r3,#ver ; strb r3,[r4,#10]
    i = img.find(want, 0x3000, 0x3200)
    return i if i >= 0 else None


def _trampoline_readback(site: int, argsetup: bytes, enqueue: int,
                         displaced: bytes, ret: int) -> bytes:
    t = bytearray(argsetup)
    t += tjl(site + len(t), enqueue)
    t += displaced
    t += tjl(site + len(t), ret)
    return bytes(t)


def _trampoline_refresh(site: int, enqueue: int, displaced: bytes, ret: int) -> bytes:
    t = bytearray()
    t += b"\x00\x00"                        # tadd r0,pc,#P  (patched below)
    t += tmov(1, len(FD_FRAME)) + tmov(2, 0) + tmov(3, 0)
    t += tjl(site + len(t), enqueue)
    t += tmov(3, 0xA0)                      # restore the 0xB0 subtype the handler needs
    t += displaced
    t += tjl(site + len(t), ret)
    while (site + len(t)) % 4:
        t += b"\x00"
    fd_off = site + len(t)
    t += FD_FRAME
    pc = (site + 4) & ~3
    t[0:2] = tadd_pc(0, (fd_off - pc) // 4)
    return bytes(t)


def _trailing_checksum(image: bytes) -> int:
    """Sum of the CRC-16 of every OTA data packet except the last.

    The firmware compares this against the u32 in the image's last four bytes
    and rejects the image (result code 7) if they differ, so any patch must
    recompute it.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from ota_packets import OtaStream  # noqa: PLC0415

    packets = [
        d for k, _t, d in OtaStream(image).operations()
        if k == "write" and d and d[:2] not in (b"\x00\xff", b"\x01\xff", b"\x02\xff")
    ]
    accumulated = 0
    for packet in packets[:-1]:
        accumulated = (accumulated + struct.unpack_from("<H", packet, 18)[0]) & 0xFFFFFFFF
    return accumulated


def build(img: bytes, chip: str, advertise_old_version: bool = True) -> tuple[bytearray, dict]:
    c = CHIPS[chip]
    out = bytearray(img)
    site_rb, site_rf = find_dead_sites(img, 2)

    # 1. readback: 0xFD handler -> forward to MCU, then answer from cache
    rb = _trampoline_readback(site_rb, img[c["fd_argsetup"]:c["fd_argsetup"] + ARGSETUP_LEN],
                              c["enqueue"], img[c["fd_detour"]:c["fd_detour"] + 4],
                              c["fd_detour"] + 4)
    out[site_rb:site_rb + len(rb)] = rb
    out[c["fd_detour"]:c["fd_detour"] + 4] = tjl(c["fd_detour"], site_rb)

    # 2. refresh-on-change: 0xB0 handler -> enqueue a 0xFD to the MCU
    rf = _trampoline_refresh(site_rf, c["enqueue"], img[c["b0_handler"]:c["b0_handler"] + 4],
                             c["b0_handler"] + 4)
    out[site_rf:site_rf + len(rf)] = rf
    out[c["b0_handler"]:c["b0_handler"] + 4] = tjl(c["b0_handler"], site_rf)

    # 3. reversibility: report BLE version 1 so the app offers stock as an update
    ver_off = find_version_byte(img) if advertise_old_version else None
    if ver_off is not None:
        out[ver_off] = 0x01

    # 4. trailing image checksum. The OTA end-command handler accumulates the
    #    SUM of every earlier data packet's CRC-16 and compares it against the
    #    u32 in the image's last four bytes; patching any byte changes a packet
    #    CRC and invalidates it, and the light rejects the image with result
    #    code 7. The four bytes sit in the final packet, which the sum excludes,
    #    so writing them cannot perturb the sum.
    out[-4:] = struct.pack("<I", _trailing_checksum(bytes(out)))

    return out, dict(site_readback=site_rb, site_refresh=site_rf, version_off=ver_off,
                     stock_version=img[0x23],
                     changed=sum(1 for a, b in zip(img, out) if a != b))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--chip", choices=sorted(CHIPS))
    ap.add_argument("--keep-version", action="store_true",
                    help="do not downgrade the reported version (keeps the app from "
                         "offering a restore)")
    args = ap.parse_args()

    img = args.input.read_bytes()
    sha = hashlib.sha256(img).hexdigest()
    chip = args.chip or SHA_TO_CHIP.get(sha)
    if chip is None:
        print("unknown image SHA; pass --chip LK8620|LK8720|LK8728B", file=sys.stderr)
        return 2
    if img[8:12] != b"KNLT":
        print("refusing: not a Telink (KNLT) image", file=sys.stderr)
        return 2

    out, info = build(img, chip, advertise_old_version=not args.keep_version)
    args.output.write_bytes(out)
    print(f"chip                : {chip}")
    print(f"readback trampoline : 0x{info['site_readback']:05x} (0xFD forwards to MCU)")
    print(f"refresh trampoline  : 0x{info['site_refresh']:05x} (0xB0 panel-change -> 0xFD)")
    if info["version_off"] is not None:
        print(f"reported version    : 0x{info['stock_version']:02x} -> 0x01 (app offers stock as restore)")
    print(f"bytes changed       : {info['changed']}")
    print(f"input  sha256       : {sha}")
    print(f"output sha256       : {hashlib.sha256(out).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
