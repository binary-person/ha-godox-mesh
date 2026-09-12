#!/usr/bin/env python3
"""Download the Godox firmware corpus, so the measurements can be re-derived.

`firmware-measurements.json` is measured from Godox's firmware images. Those are
their copyrighted work and are not in this repository -- but they are served by
Godox's own public endpoint, and every download URL and expected size is already
in the committed `firmware-coverage.json`. So the corpus is *reconstructible*
even though it is not redistributable, and the derivation chain is complete:

    firmware-coverage.json  ->  fetch_firmware.py  ->  a local corpus
                            ->  analyse_firmware.py  ->  firmware-measurements.json

    uv run python reverse-artifacts/fetch_firmware.py --into reverse
    uv run python reverse-artifacts/analyse_firmware.py --corpus reverse

Downloads are checked against `fileSizeBytes` from the catalogue, and against
`firmware-measurements.json`'s SHA-256 when an entry for that image exists --
which is what lets you confirm you have the same bytes the analysis used,
without anyone shipping the bytes.

This is a maintenance script: it needs the network and pulls roughly 25 MB, so
it is not part of the test suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COVERAGE = HERE / "firmware-coverage.json"
MEASUREMENTS = HERE / "firmware-measurements.json"
HEADERS = ["AppName: GodoxLight", "AppVersion: 4.1.0", "SystemInfo: Android15"]


def _download(url: str, target: Path) -> bytes:
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-sSL", "--max-time", "180"]
    for header in HEADERS:
        cmd += ["-H", header]
    cmd += ["-o", str(target), url]
    subprocess.run(cmd, check=True)
    return target.read_bytes()


def _expected_digests() -> dict[str, str]:
    """sha256 by filename, from the committed measurements where present."""
    if not MEASUREMENTS.exists():
        return {}
    blob = json.loads(MEASUREMENTS.read_text())
    return {
        entry["file"].split("/")[-1]: entry["sha256"]
        for entry in blob.get("bt", []) + blob.get("mcu", [])
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--into",
        type=Path,
        default=Path("reverse"),
        help="corpus root; images land in bt-bins/ and mcu_<chip>/ beneath it",
    )
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    args = ap.parse_args()

    if not COVERAGE.exists():
        sys.exit(f"{COVERAGE} missing -- run reverse-artifacts/refresh.py first")
    coverage = json.loads(COVERAGE.read_text())
    digests = _expected_digests()

    wanted: list[tuple[Path, str, int | None]] = []
    for entry in coverage.get("bluetooth", []):
        name = entry["firmwareName"]
        chip = next((c for c in ("LK8620", "LK8720", "LK8728B") if c in name), "unknown")
        wanted.append(
            (args.into / "bt-bins" / name, entry["downloadURL"], entry.get("fileSizeBytes"))
        )
        del chip
    for entry in coverage.get("mcu", []):
        radio_id = entry.get("radioId") or "0000"
        product = (entry.get("equipmentName") or "unknown").replace("/", "_")
        # Chip family is not in the MCU catalogue; group by it only if the
        # measurements already say which, otherwise use a flat directory.
        wanted.append(
            (
                args.into / "mcu_unsorted" / f"{radio_id}_{product}.bin",
                entry["downloadURL"],
                entry.get("fileSizeBytes"),
            )
        )

    ok = skipped = bad = 0
    for target, url, size in wanted:
        if target.exists() and not args.force:
            skipped += 1
            continue
        try:
            data = _download(url, target)
        except subprocess.CalledProcessError as err:
            print(f"  FAILED {target.name}: {err}")
            bad += 1
            continue
        if size and len(data) != size:
            print(f"  SIZE MISMATCH {target.name}: got {len(data)}, expected {size}")
            bad += 1
            continue
        expected = digests.get(target.name)
        if expected:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                print(f"  DIGEST MISMATCH {target.name}: {actual[:16]}… != {expected[:16]}…")
                bad += 1
                continue
        ok += 1

    print(f"downloaded {ok}, already present {skipped}, problems {bad}")
    print(
        "\nNote: MCU images land in mcu_unsorted/. analyse_firmware.py groups by\n"
        "mcu_<chip>/ directories, which the catalogue does not record -- sort them\n"
        "by chip using firmware-coverage.json's Bluetooth coverage lists, or point\n"
        "the analyser at an existing corpus."
    )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
