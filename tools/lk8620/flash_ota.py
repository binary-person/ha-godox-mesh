#!/usr/bin/env python3
"""Flash a Telink LK8620 image over BLE GATT, on a mesh-logged-in connection.

Why this exists
---------------
The firmware gates its OTA characteristic behind a login flag. The write
callback for the OTA characteristic starts with::

    tjl  0x0d954        ; returns the byte at RAM 0x848804
    tcmp r0, #0
    tjeq 0x077ec        ; if zero, return immediately — write discarded

and that flag is set by the write callback of the **Mesh Proxy Data In**
characteristic (``0x2ADD``) when it receives a Proxy PDU whose type field
(``byte & 0x3F``) is **0**, i.e. a mesh **Network PDU**. Beacons (type 1) and
proxy-configuration PDUs (type 2) take other branches and do not set it.

So an OTA stream written on a bare connection is accepted by GATT and silently
dropped by the firmware — which is exactly what happened when this was first
attempted: 9294 packets written without error, and the light never rebooted.

This matches the Godox app, which runs its OTA from ``onProxyLoginSuccess()``:
the proxy session is established first, on the same connection.

What this does
--------------
1. Connects and runs the mesh proxy handshake.
2. Sends one real mesh Network PDU (a harmless status request), which opens the
   OTA gate.
3. Writes the OTA stream to the OTA characteristic on that same connection.

Usage
-----
    uv run python tools/lk8620/flash_ota_login.py IMAGE \
        --state mesh_state.json --address <addr> \
        --flash --i-understand-the-risk

Defaults to a dry run. Interrupting a real OTA can leave a light needing
recovery.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ota_packets import OtaStream, total_packets  # noqa: E402

from godox_mesh_bt.controller import GodoxController  # noqa: E402
from godox_mesh_bt.protocol import build_status_request  # noqa: E402

OTA_CHARACTERISTIC = "00010203-0405-0607-0809-0a0b0c0d2b12"

# The firmware's end-command handler compares the index we send against its own
# received-packet counter and rejects the whole image if they differ, so a single
# dropped packet discards the transfer. The characteristic only offers
# write-without-response, so there is no ATT-level guarantee; pacing is the only
# lever. 6 ms is what the app uses; this is deliberately gentler.
INTER_PACKET_DELAY = 0.012


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--address", required=True)
    ap.add_argument("--flash", action="store_true", help="actually write")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    ap.add_argument("--dry-run-out", type=Path, help="write the packet stream to a file")
    args = ap.parse_args()

    image = args.image.read_bytes()
    stream = OtaStream(image)
    count = total_packets(len(image))
    print(f"image      : {len(image)} bytes, {count} data packets")
    print(f"sha256     : {hashlib.sha256(image).hexdigest()}")

    if args.dry_run_out:
        raw = b"".join(d for k, _t, d in stream.operations() if k == "write")
        Path(args.dry_run_out).write_bytes(raw)
        print(f"wrote raw write-stream to {args.dry_run_out} ({len(raw)} bytes)")
        print(f"stream sha : {hashlib.sha256(raw).hexdigest()}")

    if not (args.flash and args.i_understand_the_risk):
        print("\nDRY RUN — no device touched. Pass --flash --i-understand-the-risk.")
        return 0

    controller = GodoxController(args.address, str(args.state))
    print(f"\nconnecting to {args.address} …")
    await controller.connect()
    client = controller._client
    print("connected; mesh proxy session established")

    # The characteristic supports notifications, and the firmware reports the
    # end-command result there. Capture them: without this an image rejected for
    # a dropped packet looks identical to a successful flash.
    notes: list[bytes] = []

    def on_ota_note(_c: object, data: bytearray) -> None:
        notes.append(bytes(data))
        print(f"  [OTA notify] {bytes(data).hex()}")

    with contextlib.suppress(Exception):
        await client._client.start_notify(OTA_CHARACTERISTIC, on_ota_note)
        print("subscribed to OTA notifications")

    # Open the OTA gate: a Network PDU on this connection sets the firmware's
    # login flag, without which every OTA write is silently discarded.
    print("sending a mesh Network PDU to open the OTA gate …")
    await controller.send_v2_command_raw(build_status_request())
    await asyncio.sleep(1.0)

    try:
        written = 0
        for kind, _tag, data in stream.operations():
            if kind == "read":
                with contextlib.suppress(Exception):
                    await client._client.read_gatt_char(OTA_CHARACTERISTIC)
                continue
            await client._client.write_gatt_char(
                OTA_CHARACTERISTIC, data, response=False
            )
            if data[:2] not in (b"\x00\xff", b"\x01\xff", b"\x02\xff"):
                written += 1
                if written % 500 == 0:
                    print(f"  {written}/{count} …")
                await asyncio.sleep(INTER_PACKET_DELAY)
        print(f"end command sent ({written} data packets written).")
        await asyncio.sleep(2.5)
        if notes:
            print(f"OTA notifications received: {[n.hex() for n in notes]}")
        else:
            print("no OTA notification received")
        print("VERIFY: query FD 02 — the reported BLE version must change.")
    finally:
        with contextlib.suppress(Exception):
            await controller.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
