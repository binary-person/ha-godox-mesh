#!/usr/bin/env python3
"""Generate ``docs/models.md`` -- the glanceable per-model support list.

One row per model, so someone can scan for their light and see, at a glance,
what this integration gives it and how confident that support is. Everything is
derived: the capability table this integration ships
(``capabilities_data.json``) for what a model *gets*, and Godox's own product
catalogue for what the vendor app can do that this integration does not.

Regenerate with ``uv run python scripts/generate_model_support.py`` (run
``generate_capabilities.py`` first if the catalogue changed).
"""

from __future__ import annotations

import json
from pathlib import Path

CAPS = Path("custom_components/godox_mesh/capabilities_data.json")
CATALOGUE = Path("reverse-artifacts/product-catalogue.json")
# Hand-edited per-model verification and behaviour notes, keyed by radioId. This
# is the single source of truth for which models are "Verified" (driven on real
# hardware) and for the per-model notes shown in the table. See its own _note.
NOTES = Path("docs/model_notes.json")
OUT = Path("docs/models.md")


def _load_notes() -> dict[str, dict]:
    """radioId -> {verified: bool, notes: str}, from docs/model_notes.json."""
    raw = json.loads(NOTES.read_text())
    return {k.upper(): v for k, v in raw.items() if not k.startswith("_")}


def _modeset(product: dict) -> set[int]:
    out: set[int] = set()
    for m in product.get("modeType") or []:
        try:
            out.add(int(m))
        except (TypeError, ValueError):
            continue
    return out


def _features(entry: dict) -> list[str]:
    """The user-visible controls this integration gives a model, in order."""
    feats: list[str] = []
    if int(entry["min_kelvin"]) == int(entry["max_kelvin"]):
        feats.append("brightness (fixed daylight)")
    else:
        feats.append("colour temperature")
    if entry.get("gm_min", 0) != entry.get("gm_max", 0):
        feats.append("tint")
    modes = set(entry.get("modes") or ())
    if "hsi" in modes:
        feats.append("HSI colour")
    if "rgb" in modes:
        feats.append("RGB channels")
    if "xy" in modes:
        feats.append("CIE xy")
    if "color_chip" in modes:
        feats.append("gels")
    if entry.get("effects"):
        feats.append("effects")
    if entry.get("selfie_min_kelvin", 0) != entry.get("selfie_max_kelvin", 0):
        feats.append("selfie CCT")
    if entry.get("fan"):
        feats.append("fan")
    if entry.get("control_modes"):
        feats.append("control mode")
    if entry.get("smoothness_modes"):
        feats.append("dimming smoothness")
    if entry.get("attachment"):
        feats.append("accessory recognition")
    if entry.get("battery"):
        feats.append("battery")
    return feats


def _not_supported(modes: set[int]) -> list[str]:
    """App features this integration deliberately does not expose, per model."""
    out: list[str] = []
    if 17 in modes:
        out.append("motorised accessory")  # separate device -- see model-support.md
    if modes & {1000, 1001, 1002}:
        out.append("pixel animations")  # LT1, library-only
    return out


def _confidence(rid: str, notes: dict[str, dict]) -> tuple[int, str]:
    """(sort key, label). Lower sort key = higher confidence.

    Every model in the capability table is served a current mesh firmware image
    (that is the gate for being in the table at all), so the only distinction
    that remains is whether its light has actually been driven on hardware --
    recorded as ``verified: true`` in docs/model_notes.json.
    """
    if notes.get(rid, {}).get("verified"):
        return (0, "Verified")
    return (1, "High")


def main() -> int:
    caps = json.loads(CAPS.read_text())
    caps = {k.upper(): v for k, v in caps.items() if not k.startswith("_")}
    catalogue = {
        (p.get("radioId") or "").upper(): p
        for p in json.loads(CATALOGUE.read_text())["products"]
    }
    notes = _load_notes()

    # A note for a radioId that is not a supported light cannot be rendered --
    # flag it rather than silently dropping the edit.
    for rid in notes:
        if rid not in caps:
            print(f"  warning: note for {rid} is not a model in the table; ignored")

    rows = []
    for rid, entry in caps.items():
        modes = _modeset(catalogue.get(rid, {}))
        conf_key, conf_label = _confidence(rid, notes)
        rows.append(
            {
                "rid": rid,
                "name": entry.get("name") or catalogue.get(rid, {}).get("productName", rid),
                "conf_key": conf_key,
                "conf": conf_label,
                "gets": ", ".join(_features(entry)),
                "not": ", ".join(_not_supported(modes)) or "—",
                "notes": (notes.get(rid, {}).get("notes") or "").strip() or "—",
            }
        )
    rows.sort(key=lambda r: (r["conf_key"], r["name"].lower()))

    n = len(rows)
    verified = sum(1 for r in rows if r["conf"] == "Verified")
    high = sum(1 for r in rows if r["conf"] == "High")

    lines = [
        "<!-- Generated by scripts/generate_model_support.py -- do not hand-edit. -->",
        "# Supported models",
        "",
        f"**{n} Godox Bluetooth-mesh lights.** Find your model below to see what",
        "this integration gives it. Everything here is derived from Godox's own",
        "app and product catalogue -- the same data the official app uses to",
        "decide what controls to show -- so a model listed as supported gets the",
        "same commands the vendor app would send it.",
        "",
        "Every model here is served one of Godox's three current mesh-firmware",
        "images (confirmed by a per-radioId sweep of the firmware API), which is",
        "what makes it reachable over the mesh at all. Bluetooth Godox products",
        "that are *not* mesh are out of scope and are listed, with the evidence,",
        "in [model-support.md](model-support.md#what-this-integration-cannot-reach).",
        "",
        f"**{verified} of {n} are confirmed on real hardware.** If your model",
        "works — or misbehaves — please post it on the [model support",
        "board](https://github.com/binary-person/ha-godox-mesh/issues/1); that is",
        "how the rest get verified. Confirmed models and their quirks are curated",
        "into `docs/model_notes.json` and appear in the Notes column below.",
        "",
        "Regenerate this file with `uv run python scripts/generate_model_support.py`.",
        "",
        "## How confident is the support?",
        "",
        "Every command is built to the exact byte layout read out of the vendor",
        "app, over the mesh path already proven on real hardware, and asserted",
        "byte-for-byte in the tests. The tiers below are about *which lights that",
        "has been confirmed against*, not about whether the frames are right.",
        "",
        f"- **Verified** ({verified} of {n}) -- driven end to end on real hardware.",
        f"- **High** ({high}) -- catalogue-derived: the model is served a current",
        "  mesh firmware, and its controls come from Godox's own catalogue, but",
        "  its specific light has not been exercised here. This is most models,",
        "  and the frames are identical to what the vendor app sends.",
        "",
        "Two data-derived choices carry their own small risk regardless of tier:",
        "gel selection sends the catalogue's number for the gel (inferred, not",
        "read back), and models that take 0-1000 per channel are rescaled from",
        "Home Assistant's 0-255 linearly. See",
        "[model-support.md](model-support.md) for the details.",
        "",
        '"Not in HA" names anything the vendor app can do for that model which',
        "this integration does not: a **motorised accessory** (a powered stand or",
        "electronic softbox -- a separate device, not a light control) or **pixel",
        "animations** (the LT1's per-pixel upload, available in the library only).",
        "",
        "## Models",
        "",
        "| Model | Radio ID | Confidence | What you get | Not in HA | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | `{r['rid']}` | {r['conf']} | {r['gets']} "
            f"| {r['not']} | {r['notes']} |"
        )
    lines.append("")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} with {n} models (verified {verified}, high {high})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
