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

from ._lib.protocol import EFFECT_IDS, FX_V3, FX_V3_SPEED_MAX

_DATA_PATH = Path(__file__).parent / "capabilities_data.json"
_CHIPS_PATH = Path(__file__).parent / "color_chips_data.json"

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
    effect_version: int = 0

    @property
    def label(self) -> str:
        """The name to show in a light's effect list.

        An effect with a small, discrete set of gears says so, because the
        count is per-effect and there is nowhere else the user would see it
        before choosing. An effect on the newer generation has a continuous
        0-100 range instead, where "(101 gears)" would be noise, and the
        single-gear majority of the older generation is left plain too -- so
        the annotation means something whenever it appears.
        """
        if 0 < self.speed_max < 10:
            return f"{self.name} ({self.speed_max + 1} gears)"
        return self.name

    @property
    def speed_max(self) -> int:
        """Highest speed value this effect accepts, 0 when it has no speed.

        The two effect generations count differently. On the older one the
        catalogue's ``gear`` is a step count and the vendor app uses
        ``gear - 1`` as the slider maximum, so gears of 0 or 1 mean one fixed
        speed. The newer one puts a 0-100 value in its V3 frame and reports
        ``gear: 0`` for every effect, so deriving the maximum from ``gear``
        there would hide the speed control entirely -- which it did.
        """
        if self.effect_version == 1:
            return FX_V3_SPEED_MAX
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
class GodoxOption:
    """One choice in a catalogue option list, with the code the wire carries.

    Shared by control mode, mains frequency and dimming smoothness, which all
    have the same shape in the catalogue.
    """

    code: int
    name: str


@dataclass(frozen=True, slots=True)
class GodoxColorChip:
    """One lighting gel a model can emulate.

    Parameters
    ----------
    label
        Display name, built from the gel's brand, reference type and number --
        what it is called on the physical filter.
    brand, number, sub_brand
        What goes on the wire. See
        :func:`godox_mesh_bt.protocol.build_color_chip_command`.
    """

    label: str
    brand: int
    number: int
    sub_brand: int = 0


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
    modes
        The control modes the vendor app offers for this model, by name --
        ``cct``, ``hsi``, ``rgb``, ``xy``, ``effects``, ``color_chip`` and so
        on. This is what decides whether a light gets a colour wheel.
    gm_min, gm_max
        Green/magenta tint range. Equal values mean the model has no tint
        control, which is the majority.
    rgb_display
        Wire format for direct channel control: 0 is one byte per channel,
        1 and 2 are sixteen-bit values scaled to 0-1000.
    rgb_channels
        Channel layouts the model accepts -- ``RGBW``, ``RGBWW``, ``RGBACL``.
    brightness_steps
        1000 on models that accept tenths of a percent, 100 otherwise.
    effect_version
        Which effect frame the model takes -- 0 for ``0xF3``, 1 for the V3
        ``0xF7`` form. See :func:`godox_mesh_bt.protocol.build_fx_command`.
    color_chip_version
        Which gel catalogue and frame the model uses, when it has gels at all.

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
    modes: frozenset[str] = frozenset({"cct"})
    gm_min: int = 0
    gm_max: int = 0
    rgb_display: int = 0
    rgb_channels: tuple[str, ...] = ()
    brightness_steps: int = 100
    effect_version: int = 0
    color_chip_version: int = 0
    control_modes: tuple[GodoxOption, ...] = ()
    frequencies: tuple[GodoxOption, ...] = ()
    smoothness_modes: tuple[GodoxOption, ...] = ()
    attachment: bool = False
    selfie_min_kelvin: int = 0
    selfie_max_kelvin: int = 0
    #: Out-of-the-box readback/polling defaults for this model, from verified
    #: findings. They pre-fill the checkboxes when a light of this model is
    #: first configured; a node's own stored value wins once it has one.
    readback_default: bool = False
    poll_cct_default: bool = True
    poll_brightness_default: bool = True
    #: A one-line known quirk for this model, from ``docs/model_notes.json``.
    #: Shown on the settings step so the pre-filled defaults are explained
    #: (why colour-temperature polling is off for a model that reads back
    #: stale, say). Empty for a model no one has recorded a note against.
    note: str = ""

    @property
    def has_selfie_cct(self) -> bool:
        """Whether the model has a second, narrower colour-temperature range."""
        return self.selfie_min_kelvin != self.selfie_max_kelvin

    def option_by_name(
        self, options: tuple[GodoxOption, ...], name: str
    ) -> GodoxOption | None:
        """Return the option with this display name, or ``None``."""
        return next((o for o in options if o.name == name), None)

    @property
    def color_chips(self) -> tuple[GodoxColorChip, ...]:
        """The gels this model can emulate, empty when it has none.

        A gel is named on the wire by a brand code and a number, not by its
        colour, so the list has to come from Godox's own catalogue -- which is
        why it is a separate generated table rather than part of a model's
        entry: the same few hundred gels are shared across every model on a
        catalogue version.
        """
        if "color_chip" not in self.modes:
            return ()
        return _color_chips().get(str(self.color_chip_version), ())

    def color_chip_by_label(self, label: str) -> GodoxColorChip | None:
        """Return the gel with this display label, or ``None``."""
        return next((c for c in self.color_chips if c.label == label), None)

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
    def has_tint(self) -> bool:
        """Whether the model has a green/magenta tint control."""
        return self.gm_min != self.gm_max

    @property
    def supports_cct(self) -> bool:
        """Whether the light has a usable colour-temperature range."""
        return not self.is_daylight

    @property
    def rgb_color_mode(self) -> ColorMode | None:
        """Which Home Assistant colour mode this model's RGB channels map to.

        ``RGBW`` and ``RGBWW`` have exact Home Assistant equivalents. ``RGBACL``
        -- red/green/blue plus amber, cyan and lime -- has none, so a model that
        offers *only* that layout gets no direct-channel control here; its hue
        and saturation still work through HSI.
        """
        if "RGBW" in self.rgb_channels:
            return ColorMode.RGBW
        if "RGBWW" in self.rgb_channels:
            return ColorMode.RGBWW
        return None

    @property
    def supports_xy(self) -> bool:
        """Whether the model accepts the CIE xy command."""
        return "xy" in self.modes

    def color_modes_for(self, *, use_xy: bool = False) -> set[ColorMode]:
        """Colour modes to advertise, honouring the entry's xy preference.

        With *use_xy* the hue/saturation and direct-channel modes are replaced
        rather than joined, because Home Assistant resolves a colour wheel's
        ``hs_color`` against RGB, RGBW, RGBWW and only then XY. A light
        advertising any of those alongside XY would never reach its xy command
        from the dashboard -- the mode has to be the only colour mode for the
        wheel to land on it.
        """
        if use_xy and self.supports_xy:
            modes = {ColorMode.XY}
            if self.supports_cct:
                modes.add(ColorMode.COLOR_TEMP)
            return modes
        return self.color_modes

    @property
    def color_modes(self) -> set[ColorMode]:
        """The Home Assistant colour modes this light should advertise.

        Built from the vendor catalogue's own ``modeType`` list, so a model
        gets exactly the modes its own app offers. ``xy`` is deliberately not
        included even for the 40 models that list it: Home Assistant derives
        ``xy_color`` from any colour mode, and advertising a second redundant
        mode only gives the user a way to pick a worse one.
        """
        modes: set[ColorMode] = set()
        if self.supports_cct:
            modes.add(ColorMode.COLOR_TEMP)
        if "hsi" in self.modes:
            modes.add(ColorMode.HS)
        if "rgb" in self.modes and (rgb := self.rgb_color_mode) is not None:
            modes.add(rgb)
        # Home Assistant rejects BRIGHTNESS alongside anything else, so it is
        # only ever the sole entry.
        return modes or {ColorMode.BRIGHTNESS}

    @property
    def color_mode(self) -> ColorMode:
        """The colour mode a light starts in, and its only one if it has one.

        Colour temperature wins when the model has it, because that is what a
        video light is normally set by; the colour modes are reached by
        setting a colour.
        """
        modes = self.color_modes
        for preferred in (
            ColorMode.COLOR_TEMP,
            ColorMode.HS,
            ColorMode.RGBW,
            ColorMode.RGBWW,
        ):
            if preferred in modes:
                return preferred
        return ColorMode.BRIGHTNESS


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


def _options(raw: object) -> tuple[GodoxOption, ...]:
    """Parse one of the catalogue's option lists from the table."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        GodoxOption(code=int(o["code"]), name=str(o["name"]))
        for o in raw
        if isinstance(o, dict) and o.get("code") is not None
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
                    symbol=e["symbol"],
                    name=e["name"],
                    gears=e.get("gears", 1),
                    effect_version=entry.get("effect_version", 0),
                )
                for e in entry.get("effects") or ()
                # A newer-generation model can only be sent effects this
                # library has a V3 frame for. Offering one it cannot build
                # would fail at the moment the user picked it.
                if entry.get("effect_version", 0) != 1 or e["symbol"] in FX_V3
            ),
            fan_modes=tuple(
                GodoxFanMode(code=m["code"], name=m["name"])
                for m in entry.get("fan_modes") or ()
            ),
            modes=frozenset(entry.get("modes") or ("cct",)),
            gm_min=int(entry.get("gm_min", 0)),
            gm_max=int(entry.get("gm_max", 0)),
            rgb_display=int(entry.get("rgb_display", 0)),
            rgb_channels=tuple(entry.get("rgb_channels") or ()),
            brightness_steps=int(entry.get("brightness_steps", 100)),
            effect_version=int(entry.get("effect_version", 0)),
            color_chip_version=int(entry.get("color_chip_version", 0)),
            control_modes=_options(entry.get("control_modes")),
            frequencies=_options(entry.get("frequencies")),
            smoothness_modes=_options(entry.get("smoothness_modes")),
            attachment=bool(entry.get("attachment")),
            selfie_min_kelvin=int(entry.get("selfie_min_kelvin", 0)),
            selfie_max_kelvin=int(entry.get("selfie_max_kelvin", 0)),
            readback_default=bool(entry.get("readback_default", False)),
            poll_cct_default=bool(entry.get("poll_cct_default", True)),
            poll_brightness_default=bool(entry.get("poll_brightness_default", True)),
            note=str(entry.get("note") or ""),
        )
        for rid, entry in raw.items()
    }


def _load_color_chips() -> dict[str, tuple[GodoxColorChip, ...]]:
    """Parse the gel catalogue from disk. Read at import, like the main table."""
    if not _CHIPS_PATH.exists():
        return {}
    raw = json.loads(_CHIPS_PATH.read_text()).get("versions") or {}
    return {
        version: tuple(
            GodoxColorChip(
                label=chip["label"],
                brand=chip["brand"],
                number=chip["number"],
                sub_brand=chip.get("sub_brand", 0),
            )
            for chip in chips
        )
        for version, chips in raw.items()
    }


#: Parsed once at import; see _load_table for why this is not lazy.
_TABLE: dict[str, GodoxCapabilities] = _load_table()
_CHIPS: dict[str, tuple[GodoxColorChip, ...]] = _load_color_chips()


def _color_chips() -> dict[str, tuple[GodoxColorChip, ...]]:
    """The gel catalogue, keyed by catalogue version as a string."""
    return _CHIPS


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
    two-byte company id. Bleak strips that company id and hands it back as the
    dict key, so the blob is reassembled here -- company id back on the front --
    rather than shifting the offsets, to keep this readable against the original.

    The "company id" is *not* a registered vendor identifier here (it is not
    Telink's ``0x0211``): these lights put the low two bytes of their own BLE
    address there, little-endian, so it differs per light. That does not matter
    -- the model id is at a fixed offset in the reassembled blob whatever the key
    is -- which is why every key present is tried rather than a fixed one.

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
    >>> # The key is the light's own BLE address suffix, not a registered vendor
    >>> # id; the radioId sits at a fixed offset regardless of the key.
    >>> radio_id_from_manufacturer_data({0x3412: bytes(4) + b"\x3f\x00" + bytes(2)})
    '003F'
    >>> radio_id_from_manufacturer_data({0x3412: b"\x00"}) is None
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
