"""Per-model capabilities, keyed by Godox ``radioId``.

The Godox mesh protocol is identical across the whole product range; what
differs between models is capability metadata — the colour-temperature range,
whether the light is fixed-daylight or bi-colour, and whether it has a fan.
That metadata lives in the Godox app's ``product.json`` and is distilled into
``capabilities_data.json`` beside this module, keyed by the 4-hex ``radioId``.

So the integration gives a light the right controls from data, without a line
of per-model code: look up the ``radioId``, read the range and flags, build the
entity. A model this table does not know still works, on a safe default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.light import ColorMode

from ._lib.protocol import EFFECT_IDS

_DATA_PATH = Path(__file__).parent / "capabilities_data.json"

# Telink company identifier, little-endian, at the head of the provisioning
# Device UUID. The third byte is the generic device class.
_TELINK_VID = 0x0211
_GENERIC_CLASS = {1: "ct", 2: "hsl", 7: "panel", 513: "lpn", 769: "switch"}

# Used when a model is unknown. This is the range of the two lights this
# integration was developed against, and it is **not** a superset: over 60
# models go wider in one or both directions (the range spans 1800-10000 K), so
# an unknown light of those models is clamped to less than it can do. It is a
# conservative default rather than a correct one -- the real fix is for the
# model to be identified, which normally happens automatically from the
# advertisement (see :func:`radio_id_from_manufacturer_data`).
_DEFAULT_MIN_KELVIN = 2800
_DEFAULT_MAX_KELVIN = 6500


@dataclass(frozen=True, slots=True)
class GodoxEffect:
    """One lighting effect a model supports.

    Parameters
    ----------
    symbol
        The value that goes on the wire, which is the catalogue's effect id
        minus one -- the offset the vendor app's own ``changeLightFX`` caller
        applies, and the one the MCUs' effect accept-lists expect.
    name
        Human name, from the vendor app's own effect table.
    gears
        How many speed steps the effect offers.
    """

    symbol: int
    name: str
    gears: int = 1

    @property
    def label(self) -> str:
        """The name to show in a light's effect list.

        Multi-speed effects say so, because the number of speeds is per-effect
        and there is nowhere else the user would see it before choosing. Single
        speed effects -- the majority -- are left plain, so the annotation
        means something when it appears.
        """
        if self.speed_max > 0:
            return f"{self.name} ({self.speed_max + 1} speeds)"
        return self.name

    @property
    def speed_max(self) -> int:
        """Highest speed value this effect accepts, 0 when it has no speed.

        The catalogue's ``gear`` is a count, and the vendor app uses
        ``gear - 1`` as the slider maximum -- so gears of 0 or 1 mean the
        effect runs at one fixed speed.
        """
        return max(0, self.gears - 1)


@dataclass(frozen=True, slots=True)
class GodoxFanMode:
    """One fan speed a model supports.

    Parameters
    ----------
    code
        The value that goes on the wire. Not ordered by speed: 2 is Low,
        4 Medium and 3 High.
    name
        Human name, as the vendor app labels it.
    """

    code: int
    name: str


@dataclass(frozen=True, slots=True)
class GodoxCapabilities:
    """What controls a given light should expose.

    Parameters
    ----------
    name
        Product name from ``product.json``, or ``None`` for an unknown model.
    min_kelvin, max_kelvin
        Colour-temperature range the light accepts. Equal values mean a
        fixed-daylight light with no colour-temperature control.
    has_fan
        Whether the light exposes *controllable* fan speeds. Many lights have a
        cooling fan with no mesh control over it; those are false here.
    effects
        The lighting effects this model supports, in the vendor app's order.
        Empty when the model has none or is unknown.
    fan_modes
        The fan speeds this model supports. Empty when it has no fan.
    chip
        The Telink BLE chip family (``LK8620`` / ``LK8720`` / ``LK8728B``), or
        ``None`` when unknown. Informational.

    Examples
    --------
    >>> caps = capabilities_for_radio_id("003F")
    >>> caps.name, caps.min_kelvin, caps.max_kelvin
    ('SL200IIIBi', 2800, 6500)
    """

    name: str | None
    min_kelvin: int
    max_kelvin: int
    has_fan: bool
    has_battery: bool
    chip: str | None
    effects: tuple[GodoxEffect, ...] = ()
    fan_modes: tuple[GodoxFanMode, ...] = ()

    @property
    def max_effect_speed(self) -> int:
        """Highest speed any of this model's effects accepts, 0 for none."""
        return max((effect.speed_max for effect in self.effects), default=0)

    def effect_by_name(self, name: str) -> GodoxEffect | None:
        """Return the effect with this name, plain or annotated.

        Both forms are accepted: the light's effect list shows the annotated
        label, but a restored state or an automation written before the
        annotation existed will use the bare name.
        """
        return next(
            (e for e in self.effects if name in (e.name, e.label)), None
        )

    def fan_mode_by_name(self, name: str) -> GodoxFanMode | None:
        """Return the fan mode with this display name, or ``None``."""
        return next((m for m in self.fan_modes if m.name == name), None)

    @property
    def is_daylight(self) -> bool:
        """Whether the light has a single fixed colour temperature."""
        return self.min_kelvin == self.max_kelvin

    @property
    def color_mode(self) -> ColorMode:
        """The Home Assistant colour mode this light should present."""
        return ColorMode.BRIGHTNESS if self.is_daylight else ColorMode.COLOR_TEMP


#: Effects offered when the model is unknown. Named by symbol, because without
#: knowing the model we cannot know what each one looks like. An unsupported
#: symbol is ignored by the firmware, so offering these is harmless.
_FALLBACK_EFFECTS = tuple(
    GodoxEffect(symbol=symbol, name=f"Effect {symbol}") for symbol in EFFECT_IDS
)

DEFAULT_CAPABILITIES = GodoxCapabilities(
    name=None,
    min_kelvin=_DEFAULT_MIN_KELVIN,
    max_kelvin=_DEFAULT_MAX_KELVIN,
    has_fan=False,
    has_battery=False,
    chip=None,
    effects=_FALLBACK_EFFECTS,
)


def _load_table() -> dict[str, GodoxCapabilities]:
    """Parse the capability table from disk.

    Called once at import, never from the event loop. Home Assistant imports
    integrations in an executor, so reading the file here is safe -- doing it
    lazily on first use is not, and produced a "Detected blocking call to
    read_text ... inside the event loop" warning the first time a light was
    set up.
    """
    raw = json.loads(_DATA_PATH.read_text())
    # Underscore-prefixed keys carry provenance, not models.
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    return {
        rid.upper(): GodoxCapabilities(
            name=entry.get("name"),
            min_kelvin=entry["min_kelvin"],
            max_kelvin=entry["max_kelvin"],
            has_fan=bool(entry.get("fan")),
            has_battery=bool(entry.get("battery")),
            chip=entry.get("chip"),
            effects=tuple(
                GodoxEffect(
                    symbol=e["symbol"], name=e["name"], gears=e.get("gears", 1)
                )
                for e in entry.get("effects") or ()
            ),
            fan_modes=tuple(
                GodoxFanMode(code=m["code"], name=m["name"])
                for m in entry.get("fan_modes") or ()
            ),
        )
        for rid, entry in raw.items()
    }


#: Parsed once at import; see _load_table for why this is not lazy.
_TABLE: dict[str, GodoxCapabilities] = _load_table()


def _table() -> dict[str, GodoxCapabilities]:
    """The capability table, keyed by ``radioId``."""
    return _TABLE


def capabilities_for_radio_id(radio_id: str | None) -> GodoxCapabilities:
    """Return the capabilities for a ``radioId``, or a safe default.

    Parameters
    ----------
    radio_id
        The 4-hex model id (case-insensitive), or ``None``.

    Returns
    -------
    GodoxCapabilities
        The model's capabilities, or :data:`DEFAULT_CAPABILITIES` when the id is
        ``None`` or not in the table.

    Examples
    --------
    >>> capabilities_for_radio_id("000E").is_daylight
    True
    >>> capabilities_for_radio_id(None) is DEFAULT_CAPABILITIES
    True
    """
    if not radio_id:
        return DEFAULT_CAPABILITIES
    return _table().get(radio_id.upper(), DEFAULT_CAPABILITIES)


def known_models() -> dict[str, GodoxCapabilities]:
    """Return every known model keyed by ``radioId`` (for a model picker).

    Examples
    --------
    >>> "003F" in known_models()
    True
    """
    return dict(_table())


def radio_id_from_manufacturer_data(
    manufacturer_data: dict[int, bytes] | None,
) -> str | None:
    """Return the Godox ``radioId`` carried in a BLE advertisement, if present.

    Godox puts the model id in its manufacturer-specific advertising data, and
    its own app reads it from there to decide what a device is -- so this
    identifies any of the 190 mesh models without a per-model name list.

    The vendor app reads bytes 7 and 6 of the manufacturer blob *including* the
    two-byte company id. Bleak strips the company id and hands it back as the
    dict key, so the blob is reassembled here rather than shifting the offsets,
    to keep this readable against the original.

    Parameters
    ----------
    manufacturer_data
        ``{company_id: payload}`` from a BLE advertisement, or ``None``.

    Returns
    -------
    str | None
        The 4-hex radioId, or ``None`` when no entry is long enough to carry
        one. A returned id is not guaranteed to be in the capability table.

    Examples
    --------
    >>> radio_id_from_manufacturer_data({0x0211: bytes(4) + b"\x3f\x00" + bytes(4)})
    '003F'
    >>> radio_id_from_manufacturer_data({0x0211: b"\x00"}) is None
    True
    >>> radio_id_from_manufacturer_data(None) is None
    True
    """
    for company_id, payload in (manufacturer_data or {}).items():
        blob = company_id.to_bytes(2, "little") + bytes(payload)
        # The app requires length > 9 before trusting these offsets.
        if len(blob) <= 9:
            continue
        radio_id = f"{blob[7]:02X}{blob[6]:02X}"
        if radio_id != "0000":
            return radio_id
    return None


def generic_class_from_device_uuid(device_uuid: bytes | None) -> str | None:
    """Return the broad control class from a provisioning Device UUID.

    The Godox/Telink Device UUID begins with the Telink company id
    (little-endian) and a one-byte device class. This is a coarse signal —
    ``ct`` (colour temperature), ``hsl`` (full colour), ``panel`` — available at
    pairing time before the exact model is known.

    Parameters
    ----------
    device_uuid
        The 16-byte Device UUID, or ``None``.

    Returns
    -------
    str | None
        ``"ct"``, ``"hsl"``, ``"panel"``, ``"lpn"``, ``"switch"``, or ``None``
        if the UUID is missing, too short, or not a Telink device.

    Examples
    --------
    >>> generic_class_from_device_uuid(bytes([0x11, 0x02, 0x01]) + bytes(13))
    'ct'
    """
    if not device_uuid or len(device_uuid) < 3:
        return None
    vid = device_uuid[0] | (device_uuid[1] << 8)
    if vid != _TELINK_VID:
        return None
    return _GENERIC_CLASS.get(device_uuid[2])
