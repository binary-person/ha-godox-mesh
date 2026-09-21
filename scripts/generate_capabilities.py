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
``modes``
    ``modeType`` -- the vendor app's own list of control modes for a model, and
    the authoritative answer to "is this light full-colour?". See
    :data:`_MODE_NAMES`. These fields were absent from the first version of
    this table, which is why the integration shipped colour-temperature-only
    entities for 86 models that do HSI.
``gm_min`` / ``gm_max``
    ``greenMagenta``. Equal values (usually 0/0) mean no tint control.
``rgb_display`` / ``rgb_channels``
    ``rgbDisplay`` decides the wire format for direct channel control -- 0 is
    one byte per channel, 1 and 2 are sixteen-bit values scaled to 0-1000.
    ``rgb`` says which channel layouts the model accepts; see
    :data:`_RGB_LAYOUTS`.
``brightness_steps``
    ``luminance``: 100 for whole-percent brightness, 1000 for tenths.
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


#: ``modeType`` codes, from the vendor app's ``getSceneModeTypeList``. Note
#: that **8 is absent there too**: the app's own dispatch has no branch for it,
#: so the 34 models that list it get nothing from it. It is carried through
#: here as an unnamed code rather than silently dropped.
_MODE_NAMES: dict[int, str] = {
    1: "cct",
    4: "hsi",
    5: "rgb",
    6: "color_chip",
    7: "xy",
    9: "effects",
    16: "cct_selfie",
    17: "electronic_control",
    1000: "pixel_user",
    1001: "pixel_system",
    1002: "pixel_studio",
}

#: ``rgb`` codes to the channel layouts they accept, from the vendor app's
#: ``allRgbModes``. ``RGBW`` is red/green/blue/white; ``RGBWW`` adds a second,
#: warm white; ``RGBACL`` is red/green/blue plus amber, cyan and lime.
_RGB_LAYOUTS: dict[int, tuple[str, ...]] = {
    1: ("RGBW",),
    2: ("RGBW", "RGBWW", "RGBACL"),
    3: ("RGBW", "RGBWW"),
    4: ("RGBW", "RGBACL"),
    5: ("RGBWW",),
    6: ("RGBWW", "RGBACL"),
    7: ("RGBACL",),
}


def _as_int(value: object) -> int | None:
    """Coerce a catalogue field to an int, or None.

    The catalogue types the same field differently between models -- integers
    for some, decimal strings for others -- so nothing may assume either.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _modes(product: dict) -> list[str]:
    """Named control modes a model offers, in the catalogue's own order."""
    out: list[str] = []
    for raw in product.get("modeType") or []:
        code = _as_int(raw)
        if code is None:
            continue
        name = _MODE_NAMES.get(code, f"mode_{code}")
        if name not in out:
            out.append(name)
    return out


def _options(product: dict, field: str) -> list[dict]:
    """Wire codes and English names for one of the catalogue's option lists.

    ``controlMode``, ``smoothness`` and ``frequency`` all share a shape: a list
    of ``{code, nameEn, ...}`` that is empty on most models. The English names
    are used as-is here, unlike the fan's, which needed correcting.
    """
    out: list[dict] = []
    for option in product.get(field) or []:
        code = _as_int(option.get("code"))
        if code is None:
            continue
        name = (option.get("nameEn") or "").strip() or f"Mode {code}"
        out.append({"code": code, "name": name})
    return out


def _rgb_channels(product: dict) -> list[str]:
    """Channel layouts a model accepts for direct colour control."""
    return list(_RGB_LAYOUTS.get(_as_int(product.get("rgb")) or 0, ()))


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


#: Reference types to the sub-brand code the V3+ colour-chip frame carries,
#: decoded from the vendor app's ``ColorChipJson.getSubBrandCommand``.
_CHIP_SUB_BRAND = {
    "COR.": 0, "CAL.": 1, "COLOR.": 1, "SPC.": 2,
    "600": 2, "CINE.": 3, "COS.": 3, "700": 4,
}


def _chip_label(chip: dict) -> str:
    """A name a user can pick a gel by.

    The catalogue's own number is what the gel is called on the physical
    filter, so it leads. ``colorName`` is filled in for some entries and empty
    for most, and ``referenceType`` distinguishes the sub-catalogues a brand is
    split into from version 3 onwards.
    """
    parts = [chip.get("brandSeries") or "?"]
    if reference := (chip.get("referenceType") or ""):
        parts.append(reference)
    parts.append(str(chip.get("colorNum") or chip.get("sortNum", 0)))
    label = " ".join(parts)
    if name := (chip.get("colorName") or "").strip():
        label = f"{label} {name}"
    return label


def _color_chips(path: Path) -> dict[str, list[dict]]:
    """Gel options grouped by catalogue version, with their wire fields.

    A model's ``colorChipVersion`` picks the group; within it a gel is named on
    the wire by a brand code and its ``sortNum``, which is what the app's own
    lookup uses as the number.
    """
    if not path.exists():
        print(f"  no gel catalogue at {path} -- skipping colour chips")
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, list[dict]] = {}
    # Godox ships every V1 and V2 gel twice, byte for byte, so options are
    # deduplicated on what actually goes on the wire rather than on the label.
    seen: dict[str, dict[tuple[int, int, int], str]] = {}
    for chip in _entries(raw, "chips"):
        version = str(_as_int(chip.get("colorChipVersion")) or 0)
        series = chip.get("brandSeries") or ""
        wire = (
            1 if series.split("/")[0] == "L-GEL" else 0,
            _as_int(chip.get("sortNum")) or 0,
            _CHIP_SUB_BRAND.get(chip.get("referenceType") or "", 0),
        )
        if wire in seen.setdefault(version, {}):
            continue
        label = _chip_label(chip)
        # A Home Assistant select addresses an option by its text, so two
        # different gels may not share one. Nothing in the catalogue collides
        # today; this keeps a future one reachable rather than silently lost.
        taken = set(seen[version].values())
        if label in taken:
            suffix = 2
            while f"{label} ({suffix})" in taken:
                suffix += 1
            label = f"{label} ({suffix})"
        seen[version][wire] = label
        out.setdefault(version, []).append(
            {
                "label": label,
                "brand": wire[0],
                "number": wire[1],
                "sub_brand": wire[2],
            }
        )
    return out


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
    ap.add_argument(
        "--color-chips",
        type=Path,
        default=Path("reverse-artifacts/color-chips.json"),
        help="gel catalogue (default: the committed artifact)",
    )
    ap.add_argument(
        "--color-chip-output",
        type=Path,
        default=Path("custom_components/godox_mesh/color_chips_data.json"),
    )
    ap.add_argument(
        "--notes",
        type=Path,
        default=Path("docs/model_notes.json"),
        help="hand-written per-model notes, source of readback/CCT defaults",
    )
    args = ap.parse_args()

    products = _entries(json.loads(args.products.read_text()), "products")
    chips = _chip_by_radio_id(json.loads(args.firmware.read_text()))
    # Per-model default settings, from the curated verified-findings file. A
    # model without an entry falls back to readback off / poll_cct on.
    notes = json.loads(args.notes.read_text()) if args.notes.exists() else {}
    defaults = {
        rid.upper(): entry.get("defaults", {})
        for rid, entry in notes.items()
        if not rid.startswith("_") and isinstance(entry, dict)
    }

    table: dict[str, dict] = {}
    skipped: list[str] = []
    for product in products:
        rid = (product.get("radioId") or "").upper()
        # `chips` is the set of radioIds a mesh firmware image actually serves,
        # confirmed by a per-radioId sweep of Godox's firmware API
        # (reverse/btf-per-radioid-verification.json). A model absent from it is
        # Bluetooth but *not* mesh: `hasBtFirmware` is true for those too, so it
        # cannot be the test. The app has no mesh scan signature, handshake or
        # protocol for them, so this integration cannot reach them -- see
        # docs/model-support.md "What this integration cannot reach".
        if not rid or rid not in chips:
            continue
        ct = product.get("colorTemp") or {}
        # The catalogue is inconsistently typed: most models give integers, but
        # some give the same numbers as strings ("1800"/"10000"). An earlier
        # isinstance(int) check silently dropped those, and both happened to be
        # 1800-10000 K models -- the widest range in the range -- which then
        # fell back to a default narrower than the light actually is.
        lo, hi = _as_int(ct.get("min")), _as_int(ct.get("max"))
        if lo is None or hi is None or lo > hi:
            skipped.append(f"{rid} {product.get('productName')}")
            continue
        battery = _has_battery(product)
        fan_modes = _fan_modes(product)
        gm = product.get("greenMagenta") or {}
        gm_lo, gm_hi = _as_int(gm.get("min")) or 0, _as_int(gm.get("max")) or 0
        table[rid] = {
            "name": _tidy_name(product.get("productName")),
            "min_kelvin": lo,
            "max_kelvin": hi,
            "fan": bool(fan_modes),
            "fan_modes": fan_modes,
            "battery": battery,
            "chip": chips[rid],
            "effects": _effects(product),
            # Which effect frame the model takes. 0 is the eight-byte 0xF3
            # command, 1 the V3 0xF7 one with a per-effect selector; they are
            # not interchangeable. Every effectVersion 1 model reports
            # `gear: 0`, so the older generation's step count does not apply
            # to them -- their speed is a 0-100 value instead.
            "effect_version": 1 if product.get("effectVersion") == 1 else 0,
            "modes": _modes(product),
            "gm_min": gm_lo,
            "gm_max": gm_hi,
            # Which gel catalogue and frame this model uses. Only meaningful
            # when 'color_chip' is in `modes`.
            "color_chip_version": _as_int(product.get("colorChipVersion")) or 0,
            # The vendor app's "more settings" screen. Control mode and mains
            # frequency travel in one command, which is why the catalogue lists
            # the same models under both.
            "control_modes": _options(product, "controlMode"),
            "frequencies": _options(product, "frequency"),
            "smoothness_modes": _options(product, "smoothness"),
            # Whether the light recognises a motorised accessory bolted to it.
            "attachment": bool(product.get("attachmentSupport")),
            # A second, narrower colour-temperature range on its own command.
            # Equal bounds -- almost every model -- mean it has none.
            "selfie_min_kelvin": _as_int(ct.get("selfieMin")) or 0,
            "selfie_max_kelvin": _as_int(ct.get("selfieMax")) or 0,
            "rgb_display": _as_int(product.get("rgbDisplay")) or 0,
            "rgb_channels": _rgb_channels(product),
            # 100 or 1000; anything else is treated as whole percent.
            "brightness_steps": 1000 if _as_int(product.get("luminance")) == 1000 else 100,
            # Out-of-the-box readback/polling defaults from verified findings
            # (docs/model_notes.json). Fallback: readback off, poll_cct on.
            "readback_default": bool(defaults.get(rid, {}).get("readback", False)),
            "poll_cct_default": bool(defaults.get(rid, {}).get("poll_cct", True)),
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
    chips = _color_chips(args.color_chips)
    if chips:
        args.color_chip_output.write_text(
            json.dumps(
                {
                    "_generated_by": "scripts/generate_capabilities.py",
                    "_inputs": [str(args.color_chips)],
                    "_note": (
                        "Generated -- do not hand-edit. One entry per gel, "
                        "grouped by the catalogue version a model reports."
                    ),
                    "versions": chips,
                },
                indent=1,
                ensure_ascii=False,
            )
            + "\n"
        )
        print(
            f"wrote {args.color_chip_output} with "
            + ", ".join(f"v{k}: {len(v)}" for k, v in sorted(chips.items()))
        )

    def count(predicate) -> int:
        return sum(1 for v in table.values() if predicate(v))

    print(f"wrote {args.output} with {len(table)} models")
    print(f"  with a fan       : {count(lambda v: v['fan'])}")
    print(f"  with effects     : {count(lambda v: v['effects'])}")
    print(f"    older 0xF3 frame: {count(lambda v: v['effects'] and not v['effect_version'])}")
    print(f"    newer 0xF7 frame: {count(lambda v: v['effects'] and v['effect_version'])}")
    print(f"  battery-capable  : {count(lambda v: v['battery'])}")
    print(f"  HSI              : {count(lambda v: 'hsi' in v['modes'])}")
    print(f"  direct RGB       : {count(lambda v: 'rgb' in v['modes'])}")
    print(f"  CIE xy           : {count(lambda v: 'xy' in v['modes'])}")
    print(f"  green/magenta    : {count(lambda v: v['gm_min'] != v['gm_max'])}")
    print(f"  0.1% brightness   : {count(lambda v: v['brightness_steps'] == 1000)}")
    print(f"  colour chip      : {count(lambda v: 'color_chip' in v['modes'])}")
    print(f"  control mode     : {count(lambda v: v['control_modes'])}")
    print(f"  smoothness       : {count(lambda v: v['smoothness_modes'])}")
    print(f"  accessory toggle : {count(lambda v: v['attachment'])}")
    print(f"  selfie CCT       : {count(lambda v: v['selfie_min_kelvin'] != v['selfie_max_kelvin'])}")
    if skipped:
        print(f"  skipped (bad colorTemp): {len(skipped)} -> {', '.join(skipped[:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
