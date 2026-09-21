#!/usr/bin/env python3
"""Fetch Godox's public catalogues and distill the committable artifacts.

Two steps, either of which can be run alone:

**fetch** downloads Godox's raw API responses into ``reverse-artifacts/nocommit/``.
That directory is gitignored: the raw responses are large, carry fields nothing
here uses, and are Godox's to publish, not ours.

**distill** reads those raw responses and writes the small, filtered artifacts
that *are* committed, beside this script. They keep only the fields the
integration actually consumes, which is what makes the capability table's
derivation checkable without republishing a vendor database.

    uv run python reverse-artifacts/refresh.py            # both steps
    uv run python reverse-artifacts/refresh.py --distill   # from existing raw

If you already have a scratch ``reverse/`` directory, point at it instead of
downloading::

    uv run python reverse-artifacts/refresh.py --distill --raw-dir reverse

The one trap worth knowing
--------------------------
The firmware endpoint's ``supportRadioIds`` is **filtered to whatever you asked
for**. Request model 003F alone and it answers ``["003F"]``; request 003C and
003F together and the same firmware entry answers ``["003F","003C"]``. Querying
per-model therefore reports every model supporting only itself, and any coverage
count built that way is wrong. This script asks for every radioId at once, which
is the only way to recover the real lists.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOCOMMIT = HERE / "nocommit"

PRODUCT_URL = "https://www.godox.net/godox/api/productV2/getProduct?lang=en-US"
FIRMWARE_URL = "https://www.godox.net/godox/api/firmware/last/batch-list/GodoxLight"
COLOR_CHIP_URL = "https://www.godox.net/godox/api/colorConfig/getColorV5"
HEADERS = [
    "AppName: GodoxLight",
    "AppVersion: 4.1.0",
    "SystemInfo: Android15",
]

#: Fields kept in the committed catalogue. The API returns 40 per product; these
#: are the ones the integration reads, plus ``hasBtFirmware``/``paVersion``,
#: which the docs' claims about the non-mesh products rest on. Dropping the rest
#: removes image URLs, timestamps, internal ids and unused vendor fields.
KEEP_FIELDS = (
    "radioId",
    "productName",
    "colorTemp",
    "effectType",
    "effectVersion",
    "fanSpeedModes",
    "batteryType",
    "hasBtFirmware",
    "paVersion",
    # Colour capability. ``modeType`` is the authoritative list of control
    # modes the vendor app offers for a model (1 CCT, 4 HSI, 5 RGB, 6 colour
    # chip, 7 xy, 9 effects, 16 selfie CCT, 17 electronic control); the rest
    # say how each of those is parameterised. These were dropped from the
    # first cut of this filter, which left the integration unable to tell a
    # full-colour model from a bi-colour one -- see docs/model-support.md.
    "modeType",
    "lightType",
    "rgb",
    "rgbDisplay",
    "greenMagenta",
    "colorChipVersion",
    # 100 = whole-percent brightness, 1000 = tenths. The V2 end byte carries
    # the tenth, so this decides whether it is a digit or padding.
    "luminance",
    # The vendor app's "more settings" screen. Each is a list of
    # {code, nameEn, ...} and empty on all but a handful of models.
    "controlMode",
    "frequency",
    "smoothness",
    # Whether the light recognises a motorised accessory attached to it.
    "attachmentSupport",
)


def _curl(url: str, *, data: str | None = None) -> object:
    cmd = ["curl", "-sS", "--max-time", "120"]
    for header in HEADERS:
        cmd += ["-H", header]
    if data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", data]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def fetch() -> None:
    """Download Godox's raw responses into the gitignored scratch directory."""
    NOCOMMIT.mkdir(parents=True, exist_ok=True)

    products = _curl(PRODUCT_URL)
    (NOCOMMIT / "product_live.json").write_text(json.dumps(products, indent=1))
    entries = products.get("data") or products
    print(f"fetched {len(entries)} products")

    radio_ids = sorted({(p.get("radioId") or "").upper() for p in entries if p.get("radioId")})
    pa_versions = sorted({p.get("paVersion") for p in entries if p.get("paVersion") is not None})
    # Every radioId in one request -- see the module docstring.
    body = [
        {"category": "BTF", "paVersion": pa, "radioId": rid}
        for rid in radio_ids
        for pa in pa_versions
    ]
    firmware = _curl(FIRMWARE_URL, data=json.dumps(body))
    (NOCOMMIT / "firmware_sweep.json").write_text(json.dumps(firmware, indent=1))
    print(f"fetched BT firmware coverage for {len(radio_ids)} radioIds in one request")

    # MCU firmware is genuinely per-product, so this one *is* a per-radioId
    # request -- there is nothing to deduplicate and nothing to be filtered.
    mcu_body = [{"category": "MCUF", "paVersion": None, "radioId": rid} for rid in radio_ids]
    mcu = _curl(FIRMWARE_URL, data=json.dumps(mcu_body))
    (NOCOMMIT / "mcu_sweep.json").write_text(json.dumps(mcu, indent=1))
    print(f"fetched MCU firmware for {len(mcu.get('data') or [])} products")

    # The gel catalogue. An empty body returns the lot; it needs no auth.
    chips = _curl(COLOR_CHIP_URL, data="{}")
    (NOCOMMIT / "colorchips.json").write_text(json.dumps(chips, indent=1))
    print(f"fetched {len(chips.get('data') or [])} colour chips")


def distill(raw_dir: Path) -> None:
    """Write the small committed artifacts from raw responses in *raw_dir*."""
    products_path = next(
        (raw_dir / n for n in ("product_live.json", "products.json") if (raw_dir / n).exists()),
        None,
    )
    firmware_path = next(
        (
            raw_dir / n
            for n in ("firmware_sweep.json", "firmware-sweep-live-products.json")
            if (raw_dir / n).exists()
        ),
        None,
    )
    if products_path is None or firmware_path is None:
        sys.exit(f"no raw responses in {raw_dir} -- run without --distill to fetch them")

    raw = json.loads(products_path.read_text())
    entries = raw.get("data") or raw
    # Each option in controlMode/frequency/smoothness carries nine localised
    # names; only the code and the English one are read, and keeping the rest
    # would quadruple the artifact for nothing.
    option_fields = ("controlMode", "frequency", "smoothness")

    def _trim(product: dict) -> dict:
        kept = {k: product[k] for k in KEEP_FIELDS if k in product}
        for field in option_fields:
            if isinstance(kept.get(field), list):
                kept[field] = [
                    {"code": o.get("code"), "nameEn": o.get("nameEn")}
                    for o in kept[field]
                ]
        return kept

    catalogue = [_trim(p) for p in entries if p.get("radioId")]
    catalogue.sort(key=lambda p: (p.get("radioId") or "").upper())

    firmware_raw = json.loads(firmware_path.read_text())
    coverage = [
        {
            "firmwareName": e.get("firmwareName"),
            # versionCode is the authoritative number; versionName is the same
            # value the image itself reports. Without these an entry names a
            # file but does not say *which build* of it.
            "versionCode": e.get("versionCode"),
            "versionName": e.get("versionName"),
            "versionPoint": e.get("versionPoint"),
            "paVersion": e.get("paVersion"),
            "fileSizeBytes": e.get("fileSizeBytes"),
            "downloadURL": e.get("downloadURL"),
            "supportRadioIds": sorted(
                r.upper() for r in (e.get("supportRadioIds") or [])
            ),
        }
        for e in (firmware_raw.get("data") or [])
        if e.get("category") == "BTF"
    ]
    coverage.sort(key=lambda e: e["firmwareName"] or "")

    mcu_path = raw_dir / "mcu_sweep.json"
    if not mcu_path.exists():
        mcu_path = raw_dir / "firmware-mcuf-sweep-all-radioids.json"
    mcu: list[dict] = []
    if mcu_path.exists():
        seen: set[tuple] = set()
        for e in (json.loads(mcu_path.read_text()).get("data") or []):
            if e.get("category") != "MCUF":
                continue
            key = (e.get("firmwareName"), e.get("versionCode"), e.get("radioId"))
            if key in seen:
                continue
            seen.add(key)
            mcu.append(
                {
                    "radioId": (e.get("radioId") or "").upper() or None,
                    "equipmentName": e.get("equipmentName"),
                    "firmwareName": e.get("firmwareName"),
                    "versionCode": e.get("versionCode"),
                    "versionPoint": e.get("versionPoint"),
                    "fileSizeBytes": e.get("fileSizeBytes"),
                    "downloadURL": e.get("downloadURL"),
                }
            )
        mcu.sort(key=lambda e: e["radioId"] or "")

    _distill_color_chips(raw_dir)

    stamp = {
        "_source": PRODUCT_URL,
        "_fetched": date.today().isoformat(),
        "_note": (
            "Filtered snapshot of Godox's public product API: only the fields "
            "this repository consumes. Regenerate with reverse-artifacts/refresh.py."
        ),
    }
    (HERE / "product-catalogue.json").write_text(
        json.dumps({**stamp, "products": catalogue}, indent=1, ensure_ascii=False) + "\n"
    )
    (HERE / "firmware-coverage.json").write_text(
        json.dumps(
            {**stamp, "_source": FIRMWARE_URL, "bluetooth": coverage, "mcu": mcu},
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    covered = {r for e in coverage for r in e["supportRadioIds"]}
    print(f"distilled {len(catalogue)} products; {len(covered)} are Bluetooth-mesh")
    for entry in coverage:
        print(
            f"  {entry['firmwareName']:<44} v{entry['versionCode']:<5}"
            f" {len(entry['supportRadioIds']):>3} models"
        )
    if mcu:
        print(f"  MCU firmware published for {len(mcu)} products")


#: Fields kept from each gel entry. The command names a gel by brand and
#: number, so those two plus enough to label it in a picker are all that is
#: needed; the rest of the response is timestamps and internal ids.
CHIP_FIELDS = (
    "colorChipVersion",
    "brandSeries",
    "referenceType",
    "colorNum",
    "colorName",
    "sortNum",
    "hexString",
)


def _distill_color_chips(raw_dir: Path) -> None:
    """Write the committed gel catalogue, if a raw response is present."""
    path = raw_dir / "colorchips.json"
    if not path.exists():
        print("  no colour chip response found -- skipping the gel catalogue")
        return
    raw = json.loads(path.read_text())
    chips = [
        {k: entry[k] for k in CHIP_FIELDS if k in entry}
        for entry in (raw.get("data") or raw)
    ]
    chips.sort(
        key=lambda c: (
            c.get("colorChipVersion", 0),
            c.get("brandSeries") or "",
            c.get("referenceType") or "",
            c.get("sortNum", 0),
        )
    )
    (HERE / "color-chips.json").write_text(
        json.dumps(
            {
                "_source": COLOR_CHIP_URL,
                "_fetched": date.today().isoformat(),
                "_note": (
                    "Godox's gel catalogue, filtered to the fields the colour "
                    "chip command needs. Regenerate with refresh.py."
                ),
                "chips": chips,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"distilled {len(chips)} colour chips")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="download only")
    ap.add_argument("--distill", action="store_true", help="distill only")
    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=NOCOMMIT,
        help="where the raw responses are (default: reverse-artifacts/nocommit)",
    )
    args = ap.parse_args()

    do_fetch = args.fetch or not args.distill
    do_distill = args.distill or not args.fetch
    if do_fetch:
        fetch()
    if do_distill:
        distill(args.raw_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
