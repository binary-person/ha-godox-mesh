#!/usr/bin/env python3
"""Verify the combined status patch on a Telink mesh image (LK8620/LK8720/LK8728B)."""
from __future__ import annotations
import sys
import re
import struct
from pathlib import Path
sys.path.insert(0, "tools/tc32")
sys.path.insert(0, "tools/lk8620")
from tc32dis import TC32
from patch_status_readback import build, CHIPS, find_dead_sites, find_version_byte, FD_FRAME

dis = TC32(Path("tools/tc32/generate_sleigh.py"))


def main() -> int:
    chip = sys.argv[1]
    stock = Path(sys.argv[2]).read_bytes()
    patched = Path(sys.argv[3]).read_bytes()
    c = CHIPS[chip]
    site_rb, site_rf = find_dead_sites(stock, 2)
    ok = True
    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'  '+detail if detail else ''}")

    print(f"=== {chip}  readback@0x{site_rb:05x}  refresh@0x{site_rf:05x} ===")

    # 1. byte-for-byte equal to an independent rebuild
    expected, _ = build(stock, chip)
    check("matches an independent rebuild", bytes(patched) == bytes(expected))

    # 2. header/size
    check("same length", len(stock) == len(patched))
    check("KNLT intact", patched[8:12] == b"KNLT")
    check("length field == file size", struct.unpack_from("<I", patched, 0x18)[0] == len(patched))

    # 3. changed bytes only in the two trampolines, two detours, version byte
    ver = find_version_byte(stock)
    def region(i):
        # the final four bytes are the image checksum, recomputed by the patch
        return (c["fd_detour"] <= i < c["fd_detour"] + 4 or c["b0_handler"] <= i < c["b0_handler"] + 4
                or site_rb <= i < site_rb + 48 or site_rf <= i < site_rf + 48 or i == ver
                or i >= len(stock) - 4)
    changed = [i for i in range(len(stock)) if stock[i] != patched[i]]
    check("all changes are in the patch regions", all(region(i) for i in changed),
          f"{len(changed)} bytes")

    # 3b. the trailing image checksum must match what the firmware accumulates:
    #     the sum of every data packet's CRC-16 except the last. A stale value
    #     makes the light reject the image with OTA result code 7.
    sys.path.insert(0, str(Path(__file__).parent))
    from ota_packets import OtaStream  # noqa: PLC0415

    def trailing_sum(img: bytes) -> int:
        pk = [d for k, _t, d in OtaStream(img).operations()
              if k == "write" and d and d[:2] not in (b"\x00\xff", b"\x01\xff", b"\x02\xff")]
        acc = 0
        for q in pk[:-1]:
            acc = (acc + struct.unpack_from("<H", q, 18)[0]) & 0xFFFFFFFF
        return acc

    stored = struct.unpack("<I", bytes(patched[-4:]))[0]
    computed = trailing_sum(bytes(patched))
    check("trailing image checksum is consistent", stored == computed,
          f"stored {stored:#010x} computed {computed:#010x}")
    check("stock image checksum verifies with the same rule",
          struct.unpack("<I", bytes(stock[-4:]))[0] == trailing_sum(bytes(stock)))

    # 4. readback detour + trampoline
    d = list(dis.disasm(patched, c["fd_detour"], 4))
    check("0xFD detour is a tjl to readback trampoline",
          d and d[0][2] == f"tjl 0x{site_rb:05x}", d[0][2] if d else "?")
    rb = [t for _, _, t in dis.disasm(patched, site_rb, 24)]
    check("readback forwards to enqueue", f"tjl 0x{c['enqueue']:05x}" in rb)
    check("readback returns to detour+4", f"tjl 0x{c['fd_detour']+4:05x}" in rb)

    # 5. refresh detour + trampoline
    d2 = list(dis.disasm(patched, c["b0_handler"], 4))
    check("0xB0 detour is a tjl to refresh trampoline",
          d2 and d2[0][2] == f"tjl 0x{site_rf:05x}", d2[0][2] if d2 else "?")
    rf = list(dis.disasm(patched, site_rf, 26))
    rft = [t for _, _, t in rf]
    check("refresh forwards to enqueue", f"tjl 0x{c['enqueue']:05x}" in rft)
    check("refresh returns into the B0 handler", f"tjl 0x{c['b0_handler']+4:05x}" in rft)
    check("refresh restores r3 (B0 subtype)", "tmov r3, #160" in rft)
    check("refresh computes &fd_frame via pc-relative add", any(t.startswith("tadd r0, pc") for t in rft))
    # the FD frame is embedded and reachable
    check("FD-01 status frame embedded in refresh trampoline", FD_FRAME in patched[site_rf:site_rf+48])

    # 6. both trampoline sites dead — no external branch into either window
    ext = []
    for a, _, t in dis.disasm(patched, 0x0200, 0x1d000 - 0x0200):
        m = re.search(r"0x([0-9a-f]+)", t) if "tj" in t else None
        if not m:
            continue
        tgt = int(m.group(1), 16)
        for site in (site_rb, site_rf):
            if site <= tgt < site + 48 and not (site <= a < site + 0x400) \
               and a not in (c["fd_detour"], c["b0_handler"]):
                ext.append((a, tgt))
    check("no external branch into either trampoline (besides detours)", not ext, str(ext))

    # 7. version downgrade
    if ver is not None:
        check("reported version downgraded to 0x01", patched[ver] == 0x01,
              f"0x{stock[ver]:02x} -> 0x{patched[ver]:02x}")

    print("  " + ("ALL PASS" if ok else "*** FAIL ***"))
    return 0 if ok else 1


sys.exit(main())
