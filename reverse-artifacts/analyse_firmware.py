#!/usr/bin/env python3
"""Re-derive the firmware corpus inventory from a local set of images.

`firmware-inventory.md` records what Godox's firmware API serves and what
disassembling it showed. The images themselves are Godox's copyrighted work and
are not in this repository, so this script regenerates the *measurements* from
whatever corpus you have downloaded -- letting anyone confirm the inventory's
numbers rather than take them on trust.

    uv run python reverse-artifacts/analyse_firmware.py --corpus reverse

Every field it emits is a direct measurement of the bytes:

``versionCode``, ``versionName``, ``firmwareName``
    Which *build* each image is, joined from ``firmware-coverage.json``. A
    checksum says two files are the same; a version says which one you have and
    whether Godox has since shipped a newer one. For the Bluetooth images
    ``versionName`` is the same number in hex -- ``"000066"`` is 0x66, decimal
    102 -- which is what the chip itself reports to a ``FD 02`` query.
``sha256``, ``size``
    Identity. These are the useful half of the inventory: download the same
    images from Godox (requests are in ``docs/firmware-api.md``) and these
    confirm you have the bytes the analysis was done on.
``cortexm``, ``sp``, ``reset``
    ARM Cortex-M vector table: word 0 is the initial stack pointer, word 1 the
    reset vector. A main-MCU image has a stack pointer in SRAM (0x2000_0000)
    and a reset vector in flash (0x0800_0000); the Telink mesh images do not,
    which is how the two families are told apart without disassembly.
``crc8_255``, ``crc8_256``
    Offset of the Dallas/Maxim reflected-0x8C CRC-8 lookup table, or -1. This
    is the table the vendor protocol's checksum uses, and finding it is the
    entry point for locating the frame builders.

Not reproduced
--------------
The historical ``mcu-analysis.json`` also carried ``a_frames_strict``. Its
definition was not recorded and could not be recovered from the data, so it is
deliberately omitted rather than approximated -- a re-invented heuristic that
disagreed with the committed docs would be worse than an absent field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent

SRAM_BASE = 0x20000000
FLASH_BASE = 0x08000000


def _crc8_table() -> bytes:
    """The Dallas/Maxim reflected-0x8C table the vendor protocol uses."""
    out = bytearray()
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
        out.append(crc)
    return bytes(out)


CRC8_TABLE = _crc8_table()


def measure(path: Path, root: Path) -> dict:
    """Measure one firmware image."""
    raw = path.read_bytes()
    sp, reset = struct.unpack_from("<II", raw, 0) if len(raw) >= 8 else (0, 0)
    cortexm = (
        (sp & 0xFFF00000) == SRAM_BASE
        and (reset & 0xFF000000) == FLASH_BASE
        and bool(reset & 1)  # Thumb bit
    )
    return {
        "file": str(path.relative_to(root)),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cortexm": cortexm,
        "sp": f"0x{sp:08x}",
        "reset": f"0x{reset:08x}",
        "crc8_256": raw.find(CRC8_TABLE),
        "crc8_255": raw.find(CRC8_TABLE[:255]),
    }


def _versions(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Firmware versions from the committed coverage file, if present.

    Returns ``(by_filename, by_radio_id)`` -- Bluetooth images are keyed by
    filename because one image serves many models; MCU images are per-product.
    """
    if not path.exists():
        return {}, {}
    blob = json.loads(path.read_text())
    bt = {
        e["firmwareName"]: e
        for e in blob.get("bluetooth") or blob.get("firmware") or []
        if e.get("firmwareName")
    }
    mcu = {e["radioId"]: e for e in blob.get("mcu") or [] if e.get("radioId")}
    return bt, mcu


def _catalogue(path: Path) -> dict[str, str]:
    """radioId -> product name, from the committed catalogue if present."""
    if not path.exists():
        return {}
    blob = json.loads(path.read_text())
    entries = blob.get("products") or blob.get("data") or []
    return {
        (p.get("radioId") or "").upper(): p.get("productName") or ""
        for p in entries
        if p.get("radioId")
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--corpus",
        type=Path,
        default=Path("reverse"),
        help="directory holding bt-bins/ and mcu_*/ (default: reverse)",
    )
    ap.add_argument("--output", type=Path, default=HERE / "firmware-measurements.json")
    args = ap.parse_args()

    if not args.corpus.exists():
        print(f"no corpus at {args.corpus} -- see docs/firmware-api.md to download it")
        return 1

    names = _catalogue(HERE / "product-catalogue.json")
    bt_versions, mcu_versions = _versions(HERE / "firmware-coverage.json")
    bt: list[dict] = []
    mcu: list[dict] = []

    for image in sorted(args.corpus.rglob("*.bin")):
        rel = image.relative_to(args.corpus)
        parts = rel.parts
        entry = measure(image, args.corpus)
        if parts and parts[0] == "bt-bins":
            chip = next(
                (c for c in ("LK8620", "LK8720", "LK8728B") if c in image.name), None
            )
            meta = bt_versions.get(image.name, {})
            bt.append(
                {
                    "chip": chip,
                    "versionCode": meta.get("versionCode"),
                    "versionName": meta.get("versionName"),
                    **entry,
                }
            )
        elif parts and parts[0].startswith("mcu_"):
            # MCU images are named <radioId>_<product>.bin
            match = re.match(r"([0-9A-Fa-f]{4})_(.+)\.bin$", image.name)
            radio_id = match.group(1).upper() if match else None
            meta = mcu_versions.get(radio_id or "", {})
            mcu.append(
                {
                    "chip": parts[0].removeprefix("mcu_").upper(),
                    "radioId": radio_id,
                    "product": names.get(radio_id or "", match.group(2) if match else ""),
                    "versionCode": meta.get("versionCode"),
                    "firmwareName": meta.get("firmwareName"),
                    **entry,
                }
            )

    result = {
        "_note": (
            "Measurements of Godox firmware images, regenerated by "
            "reverse-artifacts/analyse_firmware.py. The images themselves are not "
            "redistributed; these checksums identify them."
        ),
        "bt": sorted(bt, key=lambda e: e["file"]),
        "mcu": sorted(mcu, key=lambda e: e["file"]),
    }
    args.output.write_text(json.dumps(result, indent=1) + "\n")
    print(f"measured {len(bt)} Bluetooth and {len(mcu)} MCU images -> {args.output}")
    non_cortex = [e["file"] for e in mcu if not e["cortexm"]]
    if non_cortex:
        print(f"  not Cortex-M: {non_cortex}")
    # `is not None`, not truthiness: at least one product (M300R, 008C) really
    # does publish versionCode 0 despite a firmwareName reading "V101".
    unversioned = [e["radioId"] for e in mcu if e.get("versionCode") is None]
    if unversioned:
        print(f"  no version in the MCU catalogue: {unversioned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
