#!/usr/bin/env python3
"""Probe a Godox light's status readback — run before and after flashing.

Reads everything the vendor protocol can report and prints it plainly.

Stock firmware already reports live brightness from record ``0xA0`` -- including
changes made on the light's own panel -- and live colour temperature for
anything commanded over the mesh. The one field that can be stale, on some
models, is colour temperature changed on the light's own dial; flashing the
patch does not recover it. So this is a diagnostic, not a before/after test.

    uv run python tools/lk8620/probe_readback.py --state mesh_state.json --address <addr>

Add ``--set-then-read`` to command a distinctive brightness/colour temperature
first, which shows the commanded values reading back.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from godox_mesh_bt.controller import (  # noqa: E402
    REQUEST_OPCODE,
    GodoxController,
)
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

PATCHED_VERSION = 1


def _describe(v2: bytes) -> str:
    """Render a reply frame in human terms."""
    if not v2:
        return "(empty)"
    sub = v2[0]
    if sub == 0xA0 and len(v2) >= 4:
        return f"A0 status  brightness={v2[1]}%  cct={v2[2] * 100}K  gm={v2[3]}"
    if sub == 0xA3 and len(v2) >= 4:
        return f"A3 status  brightness={v2[1]}  effect={v2[2]}  param={v2[3]}"
    if sub == 0xA6 and len(v2) >= 5:
        return (
            f"A6 battery  state={v2[1]}  runtime={v2[2]}h{v2[3]:02d}m  "
            f"charge={v2[4] & 0x7F}%"
        )
    if sub == 0xAF and len(v2) >= 5 and v2[1] == 0x20:
        tag = "  <-- PATCHED" if v2[4] == PATCHED_VERSION else "  (stock)"
        return f"AF20 version  reported={v2[4]}{tag}"
    if sub == 0xAF and len(v2) >= 5 and v2[1] == 0x30:
        return f"AF30 mcu version  {v2[2]}.{v2[3]:02d}  mcu={v2[4]}"
    return f"0x{sub:02x}  {v2.hex()}"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--address", required=True)
    ap.add_argument(
        "--set-then-read",
        action="store_true",
        help="command 40%% / 3200K first, then read back",
    )
    args = ap.parse_args()

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
            dst, enc_access = decrypt_network_pdu(d, enc, state.iv_index)
            payload = decrypt_access_pdu(
                enc_access, bytes.fromhex(state.app_key), state.iv_index, seq, src, dst
            )
        except Exception:
            return
        if len(payload) > 3:
            replies.append(payload[3:])

    await controller.connect()
    await controller._client.start_notify(on_notify)

    async def query(
        frame: bytes, label: str, expect: tuple[int, ...] | None = None
    ) -> bytes | None:
        # The light retransmits replies until acknowledged, so a window can carry
        # retries of an earlier answer. Select by sub-command rather than order.
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
        await asyncio.sleep(2.5)
        seen = list(replies)
        got = None
        if expect:
            got = next((r for r in seen if r and r[0] in expect), None)
        elif seen:
            got = seen[0]
        print(f"  {label:<34} {_describe(got) if got else '(no matching reply)'}")
        others = [r for r in seen if r is not got]
        if others:
            uniq = sorted({r.hex() for r in others})
            print(f"  {'':34} (also saw {len(others)} other frame(s): {', '.join(uniq)})")
        return got

    try:
        if args.set_then_read:
            print("\ncommanding 40% / 3200K …")
            await controller.set_params(brightness=40, cct=3200)
            await asyncio.sleep(1.5)

        print("\n--- firmware version -------------------------------------")
        version = await query(build_v2_command(0xFD, 0xFF, bytes([2])), "FD 02 version", (0xAF,))

        print("\n--- status (queried twice: the patch has a one-query lag) --")
        first = await query(build_v2_command(0xFD, 0xA0, bytes([1])), "FD 01 end=A0 status #1", (0xA0, 0xA3, 0xAD))
        second = await query(build_v2_command(0xFD, 0xA0, bytes([1])), "FD 01 end=A0 status #2", (0xA0, 0xA3, 0xAD))

        print("\n--- battery (mains lights do not answer) -------------------")
        await query(build_v2_command(0xFD, 0xA6, bytes([1])), "FD 01 end=A6 battery", (0xA6,))

        print("\n--- mcu version -------------------------------------------")
        await query(build_v2_command(0xFD, 0xFF, bytes([3])), "FD 03 mcu version", (0xAF,))

        print("\n=== verdict ==============================================")
        patched = bool(version and len(version) >= 5 and version[4] == PATCHED_VERSION)
        print(f"  firmware: {'PATCHED' if patched else 'stock'}")
        if first and second:
            if first == second:
                print(f"  status  : identical on both reads -> {first.hex()}")
                if not patched:
                    print("            (stock reports live brightness here)")
            else:
                print("  status  : CHANGED between reads -> the refresh is working")
                print(f"            #1 {first.hex()}\n            #2 {second.hex()}")
    finally:
        with contextlib.suppress(Exception):
            await controller.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
