"""`firmware-measurements.json` must stay consistent with the artifacts it joins.

The measurements themselves -- SHA-256, vector table, CRC-table offsets -- are
taken from Godox's firmware images, which are not redistributable, so a full
regeneration cannot run here. That was the reason this file had no guard at all.

But it is not purely measurements. Every entry also carries `versionCode`,
`firmwareName`, `product` and `radioId`, and all four are *joined* from
`firmware-coverage.json` and `product-catalogue.json` -- both committed. Those
are exactly the fields that go stale when someone refreshes the catalogues and
forgets to re-run `analyse_firmware.py`, which is the realistic failure. So the
joined half is checked here, and only the byte-level half needs the corpus.

Structural facts about the measurements are checked too, because they need no
images: checksums must be unique and well-formed, MCU images must look like
ARM Cortex-M and Telink mesh images must not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ARTIFACTS = Path(__file__).resolve().parents[1] / "reverse-artifacts"
MEASUREMENTS = ARTIFACTS / "firmware-measurements.json"
COVERAGE = ARTIFACTS / "firmware-coverage.json"
CATALOGUE = ARTIFACTS / "product-catalogue.json"

pytestmark = pytest.mark.skipif(
    not (MEASUREMENTS.exists() and COVERAGE.exists() and CATALOGUE.exists()),
    reason="research artifacts not present",
)


def _load() -> tuple[dict, dict, dict]:
    return (
        json.loads(MEASUREMENTS.read_text()),
        json.loads(COVERAGE.read_text()),
        json.loads(CATALOGUE.read_text()),
    )


def test_mcu_versions_match_the_firmware_catalogue() -> None:
    """A refreshed catalogue with stale measurements must not pass unnoticed."""
    measurements, coverage, _ = _load()
    published = {e["radioId"]: e.get("versionCode") for e in coverage["mcu"]}

    stale = [
        f"{e['radioId']}: measured v{e.get('versionCode')}, "
        f"catalogue says v{published.get(e['radioId'])}"
        for e in measurements["mcu"]
        if e["radioId"] in published and e.get("versionCode") != published[e["radioId"]]
    ]
    assert not stale, (
        "firmware-measurements.json disagrees with firmware-coverage.json -- "
        "re-run reverse-artifacts/analyse_firmware.py:\n  " + "\n  ".join(stale)
    )


def test_bluetooth_versions_match_the_firmware_catalogue() -> None:
    measurements, coverage, _ = _load()
    published = {e["firmwareName"]: e.get("versionCode") for e in coverage["bluetooth"]}

    for entry in measurements["bt"]:
        name = entry["file"].split("/")[-1]
        if name in published:
            assert entry.get("versionCode") == published[name], name


def test_product_names_match_the_product_catalogue() -> None:
    """The joined product name must not drift from the catalogue it came from."""
    measurements, _, catalogue = _load()
    names = {
        p["radioId"].upper(): p.get("productName")
        for p in catalogue["products"]
        if p.get("radioId")
    }

    wrong = [
        f"{e['radioId']}: measured {e.get('product')!r}, catalogue says "
        f"{names.get(e['radioId'])!r}"
        for e in measurements["mcu"]
        if e["radioId"] in names and e.get("product") != names[e["radioId"]]
    ]
    assert not wrong, "\n  ".join(wrong)


def test_checksums_are_well_formed() -> None:
    measurements, _, _ = _load()

    for entry in measurements["bt"] + measurements["mcu"]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["file"]
        assert entry["size"] > 0, entry["file"]


def test_identical_images_belong_to_one_declared_family() -> None:
    """Duplicate checksums are expected, but only within a family.

    Godox ships one MCU image for a whole product family -- the firmware is
    literally named `TP2R_TP4R_TP8R_V139` and serves all three. So identical
    bytes under different radioIds is normal. What would not be normal is two
    products sharing bytes while the catalogue claims they run *different*
    firmware, which would mean the corpus was collected or joined wrongly.
    """
    measurements, _, _ = _load()
    by_digest: dict[str, set[str]] = {}
    for entry in measurements["mcu"]:
        by_digest.setdefault(entry["sha256"], set()).add(entry.get("firmwareName"))

    conflicts = {
        digest[:16]: sorted(str(n) for n in names)
        for digest, names in by_digest.items()
        if len(names) > 1
    }
    assert not conflicts, f"identical images claiming different firmware: {conflicts}"


def test_the_two_chip_families_are_distinguishable() -> None:
    """MCU images carry an ARM Cortex-M vector table; the Telink images do not.

    This is what separates the two families without disassembly, so it is worth
    asserting rather than assuming.
    """
    measurements, _, _ = _load()

    assert all(e["cortexm"] for e in measurements["mcu"])
    assert not any(e["cortexm"] for e in measurements["bt"])
