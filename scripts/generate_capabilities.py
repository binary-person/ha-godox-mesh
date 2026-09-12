#!/usr/bin/env python3
"""Regenerate ``capabilities_data.json`` from Godox's own product catalogue.

The integration gives each light the right controls from data rather than from
per-model code, so this table is the only thing that needs to change when Godox
ships a new light. Run it against a fresh catalogue snapshot and commit the
result::

    uv run python scripts/generate_capabilities.py

It reads the committed artifacts under ``reverse-artifacts/`` by default, so the
table is reproducible from a clean checkout. To rebuild from a fresh download
instead, refresh those artifacts first::

    uv run python reverse-artifacts/refresh.py

Where each field comes from
---------------------------
``min_kelvin`` / ``max_kelvin``
    ``colorTemp``. Equal values mean a fixed-daylight light, which the
    integration renders with no colour-temperature control at all.
``fan`` / ``fan_modes``
    ``fanSpeedModes`` -- a *list* of modes with wire codes and localised names.
    It is empty for most of the range. An earlier version of this table read a
    ``hasFan`` field that does not exist in the catalogue, and so marked every
    single model as having a fan.
``effects``
    ``effectType`` gives the catalogue effect ids a model ships. The wire
    symbol is the id **minus one**. Evidence: the vendor app's only
    ``changeLightFX`` caller passes ``getSymbol() - 1``, and the MCU images that
    carry an effect table accept exactly that range -- ML100R's accept-list is
    symbols 1-16, which fits its catalogue ids 4-17 minus one and would *not*
    fit the raw ids. (An earlier version of this comment cited a ``0xF3``
    comparison chain in the LK8620 *mesh* image. That chain is not in that
    binary -- the mesh chip forwards ``0xF3`` to the MCU rather than decoding
    it -- so the citation was wrong even though the conclusion was right.) Names come from the vendor app's
    two ``word_fx_type_*`` tables, selected by ``effectVersion`` and indexed by
    the catalogue id.
``battery``
    ``batteryType``, an *integer* where 0 means mains-only.
``chip``
    Which of the three mesh firmware images Godox serves the model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Effect names from the vendor app (ConfigConstKt.findFxIconOrNameRes), keyed
# by catalogue effect id. EffectVersion 0 is the older table, 1 the newer.
_FX_NAMES: dict[int, dict[int, str]] = {
    0: {
        1: "RGB Cycle", 2: "Flash Light", 3: "Party", 4: "Lightning",
        5: "Broken Bulb", 6: "TV", 7: "Candle", 8: "Fire", 9: "Firework",
        10: "Police Car", 11: "Firetruck", 12: "Ambulance", 13: "Music",
        14: "SOS", 15: "Strong Light",
    },
    1: {
        1: "RGB Fade", 2: "RGB Flow", 3: "RGB Chase", 4: "RGB Cycle",
        5: "Party", 6: "Flash Light", 7: "Lightning", 8: "Cloudy",
        9: "Broken Bulb", 10: "TV", 11: "Candle", 12: "Fire", 13: "Firework",
        14: "Explosion", 15: "Welding", 16: "Police Car", 17: "SOS",
        18: "Music", 19: "Px Candle", 20: "Px Fire",
    },
}


#: Godox's catalogue spells the same suffixes several ways -- "Bi" 57 times,
#: "BI" and "bi" twice each, "mini" alongside "Air" and "Pro". These are the
#: model names shown in the config flow's picker, so the inconsistency is
#: user-visible. Normalise to the dominant spelling rather than inventing one.
_NAME_FIXES = {
    "bi": "Bi",
    "BI": "Bi",
    "mini": "Mini",
}


def _tidy_name(name: str | None) -> str | None:
    """Normalise a product name's suffix capitalisation."""
    if not name:
        return name
    out = name
    for wrong, right in _NAME_FIXES.items():
        # Preceded by an alphanumeric ("SL150IIIbi") or a space ("LC500 mini"),
        # and ending on a word boundary, so a word like "Bianco" would survive.
        out = re.sub(rf"(?<=[0-9A-Za-z ]){re.escape(wrong)}\b", right, out)
    return out


def _as_kelvin(value: object) -> int | None:
    """Coerce a catalogue colour-temperature bound to an int, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_battery(product: dict) -> bool:
    """Whether the model runs on a battery.

    ``batteryType`` is an integer in the catalogue, 0 meaning mains-only. It is
    easy to get this wrong by comparing against the *string* "0", which is true
    for every model and marks the whole range as battery-powered.
    """
    raw = product.get("batteryType")
    if isinstance(raw, bool) or raw is None:
        return False
    try:
        return int(raw) != 0
    except (TypeError, ValueError):
        return False


#: Accepted alternative keys, so a renamed artifact field does not silently
#: yield an empty table -- which is exactly what happened once.
_ALIASES = {"firmware": ("bluetooth",), "products": ("data",)}


def _entries(blob: object, key: str) -> list[dict]:
    """Return the records from either artifact shape.

    The committed artifacts under ``reverse-artifacts/`` nest their records
    under a named key beside provenance fields; a raw API response nests them
    under ``data``. Both are accepted so the table can be regenerated either
    from the committed snapshot or from a fresh download.
    """
    if isinstance(blob, list):
        return blob
    if isinstance(blob, dict):
        for candidate in (key, *_ALIASES.get(key, ()), "data"):
            if isinstance(blob.get(candidate), list):
                return blob[candidate]
    return []


def _chip_by_radio_id(firmware: object) -> dict[str, str]:
    """Map each radioId to its mesh chip family."""
    out: dict[str, str] = {}
    for entry in _entries(firmware, "firmware"):
        if entry.get("category") not in (None, "BTF"):
            continue
        name = entry.get("firmwareName", "")
        chip = next((c for c in ("LK8620", "LK8720", "LK8728B") if c in name), None)
        if not chip:
            continue
        for rid in entry.get("supportRadioIds") or []:
            out[rid.upper()] = chip
    return out


def _effects(product: dict) -> list[dict]:
    """Wire symbols and names for the effects a model ships."""
    version = product.get("effectVersion")
    names = _FX_NAMES.get(1 if version == 1 else 0, {})
    out: list[dict] = []
    for entry in sorted(
        product.get("effectType") or [], key=lambda e: e.get("orderNum") or 0
    ):
        raw = entry.get("name")
        if not str(raw).isdigit():
            continue
        eid = int(raw)
        if eid < 1:
            continue
        out.append(
            {
                "symbol": eid - 1,
                "name": names.get(eid, f"Effect {eid}"),
                # 'gear' is how many speed steps the effect offers.
                "gears": int(entry["gear"]) if str(entry.get("gear", "")).isdigit() else 1,
            }
        )
    return out


def _fan_modes(product: dict) -> list[dict]:
    """Wire codes and names for a model's fan, empty when it has no fan."""
    # The catalogue's own English names are shouty and inconsistent ("OFF",
    # "Lowspeed"), so tidy the known codes and fall back to the catalogue for
    # anything unrecognised.
    #
    # Code 0 is rendered "Silent" here, deliberately differing from the
    # catalogue's English "OFF". Its other eight languages all say *mute*
    # (zh 静音, ko 무음, de Stummschaltung, es Silencio, fr Silencieux,
    # it Mut, ja ミュート), and Chinese is the source language, so "OFF" is a
    # bad translation. It is also the dangerous direction to get wrong: "Off"
    # on a several-hundred-watt fixture reads as "stop the cooling fan", which
    # is not what the light does.
    #
    # Note the vendor app *does* show the catalogue's English, so an English
    # user of the Godox app sees "OFF" where this shows "Silent". That is a
    # deliberate divergence, not a match.
    tidy = {0: "Silent", 1: "Automatic", 2: "Low", 4: "Medium", 3: "High"}
    modes = product.get("fanSpeedModes") or []
    out = [
        {
            "code": m["code"],
            "name": tidy.get(m["code"]) or m.get("nameEn") or f"Mode {m['code']}",
        }
        for m in modes
        if isinstance(m.get("code"), int)
    ]
    # Present them in a sensible order rather than the catalogue's: Silent
    # first, then Automatic, then ascending speed (2 Low, 4 Medium, 3 High).
    order = {0: 0, 1: 1, 2: 2, 4: 3, 3: 4}
    return sorted(out, key=lambda m: order.get(m["code"], 99))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--products",
        type=Path,
        default=Path("reverse-artifacts/product-catalogue.json"),
        help="filtered product catalogue (default: the committed artifact)",
    )
    ap.add_argument(
        "--firmware",
        type=Path,
        default=Path("reverse-artifacts/firmware-coverage.json"),
        help="firmware coverage (default: the committed artifact)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("custom_components/godox_mesh/capabilities_data.json"),
    )
    args = ap.parse_args()

    products = _entries(json.loads(args.products.read_text()), "products")
    chips = _chip_by_radio_id(json.loads(args.firmware.read_text()))

    table: dict[str, dict] = {}
    skipped: list[str] = []
    for product in products:
        rid = (product.get("radioId") or "").upper()
        if not rid or rid not in chips:
            continue  # not a Bluetooth-mesh product
        ct = product.get("colorTemp") or {}
        # The catalogue is inconsistently typed: most models give integers, but
        # some give the same numbers as strings ("1800"/"10000"). An earlier
        # isinstance(int) check silently dropped those, and both happened to be
        # 1800-10000 K models -- the widest range in the range -- which then
        # fell back to a default narrower than the light actually is.
        lo, hi = _as_kelvin(ct.get("min")), _as_kelvin(ct.get("max"))
        if lo is None or hi is None or lo > hi:
            skipped.append(f"{rid} {product.get('productName')}")
            continue
        battery = _has_battery(product)
        fan_modes = _fan_modes(product)
        table[rid] = {
            "name": _tidy_name(product.get("productName")),
            "min_kelvin": lo,
            "max_kelvin": hi,
            "fan": bool(fan_modes),
            "fan_modes": fan_modes,
            "battery": battery,
            "chip": chips[rid],
            "effects": _effects(product),
        }

    if not table:
        sys.exit(
            "refusing to write an empty capability table -- the inputs parsed to "
            "nothing. Check that the artifact keys still match (see _ALIASES)."
        )

    ordered = {k: dict(sorted(v.items())) for k, v in sorted(table.items())}
    # Provenance in the file itself: opening it should say where it came from
    # and how to rebuild it, without having to find this script first. The key
    # is underscore-prefixed so it cannot collide with a 4-hex radioId.
    document = {
        "_generated_by": "scripts/generate_capabilities.py",
        "_inputs": [str(args.products), str(args.firmware)],
        "_note": (
            "Generated -- do not hand-edit. Regenerate with "
            "`uv run python scripts/generate_capabilities.py`."
        ),
        **ordered,
    }
    args.output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")

    with_fan = sum(1 for v in table.values() if v["fan"])
    with_fx = sum(1 for v in table.values() if v["effects"])
    with_batt = sum(1 for v in table.values() if v["battery"])
    print(f"wrote {args.output} with {len(table)} models")
    print(f"  with a fan     : {with_fan}")
    print(f"  with effects   : {with_fx}")
    print(f"  battery-capable: {with_batt}")
    if skipped:
        print(f"  skipped (bad colorTemp): {len(skipped)} -> {', '.join(skipped[:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
