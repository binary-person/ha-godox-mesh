#!/usr/bin/env python3
"""Read the BLE chip's RAM over the mesh, using the memread debug firmware.

Requires a light flashed with the image from ``patch_memread.py``. Six bytes per
request, addresses in 0x840000..0x84FFFF.

    uv run python tools/lk8620/memread.py --state mesh_state.json \
        --address <addr> --at 0x8435fc --length 16
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from godox_mesh_bt.controller import REQUEST_OPCODE, GodoxController  # noqa: E402
from godox_mesh_bt.crypto import (  # noqa: E402
    build_vendor_access_payload,
    decrypt_access_pdu,
    decrypt_network_pdu,
    deobfuscate,
    k2,
    pack_proxy_network_pdu,
)
from godox_mesh_bt.protocol import build_v2_command  # noqa: E402
from godox_mesh_bt.state import MeshState  # noqa: E402

MARKER = 0xB7
BYTES_PER_READ = 6


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--address", required=True)
    ap.add_argument("--at", required=True, help="RAM address, e.g. 0x8435fc")
    ap.add_argument("--length", type=int, default=16)
    args = ap.parse_args()

    start = int(args.at, 0)
    if not 0x840000 <= start <= 0x84FFFF:
        print("address must be within 0x840000..0x84FFFF")
        return 1

    state = MeshState.load(str(args.state))
    controller = GodoxController(args.address, str(args.state))
    replies: list[bytes] = []
    _n, enc, priv = k2(bytes.fromhex(state.network_key))

    def on_notify(pdu: bytes) -> None:
        if not pdu or (pdu[0] & 0x3F) != 0x00:
            return
        try:
            d = deobfuscate(pdu[1:], priv, state.iv_index)
            seq = int.from_bytes(d[2:5], "big")
            src = int.from_bytes(d[5:7], "big")
            dst, ea = decrypt_network_pdu(d, enc, state.iv_index)
            payload = decrypt_access_pdu(
                ea, bytes.fromhex(state.app_key), state.iv_index, seq, src, dst
            )
        except Exception:
            return
        if len(payload) > 3:
            replies.append(payload[3:])

    await controller.connect()
    await controller._client.start_notify(on_notify)

    async def send(frame: bytes, wait: float = 1.6) -> list[bytes]:
        replies.clear()
        s = controller.state
        pdu = pack_proxy_network_pdu(
            build_vendor_access_payload(REQUEST_OPCODE, frame),
            bytes.fromhex(s.network_key),
            bytes.fromhex(s.app_key),
            iv_index=s.iv_index,
            seq=s.sequence_number,
            src=s.provisioner_address,
            dst=s.node_address,
            ttl=10,
        )
        await controller._client.write_proxy(pdu)
        controller._advance_state()
        await asyncio.sleep(wait)
        return list(replies)

    data = bytearray()
    try:
        offset = 0
        while offset < args.length:
            addr = start + offset
            low = addr & 0xFF
            high = (addr >> 8) & 0xFF
            frame = build_v2_command(0xFD, 0xFF, bytes([4, low, high]))
            got = [r for r in await send(frame) if r and r[0] == MARKER]
            if not got:
                print(f"  {addr:#08x}: no reply (is the memread firmware flashed?)")
                break
            chunk = got[0][1 : 1 + BYTES_PER_READ]
            data += chunk
            print(f"  {addr:#08x}  {chunk.hex(' ')}")
            # acknowledge so the retransmit does not pollute the next read
            await send(build_v2_command(0xE0, 0xFF, bytes([MARKER])), wait=0.4)
            offset += BYTES_PER_READ
    finally:
        with contextlib.suppress(Exception):
            await controller.disconnect()

    if data:
        print(f"\n{start:#08x}: {bytes(data[: args.length]).hex(' ')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
