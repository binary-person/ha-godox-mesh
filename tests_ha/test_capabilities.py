"""Tests for data-driven per-model capabilities."""

from __future__ import annotations

import pytest
from custom_components.godox_mesh.capabilities import (
    DEFAULT_CAPABILITIES,
    GodoxCapabilities,
    capabilities_for_radio_id,
    generic_class_from_device_uuid,
    known_models,
)
from homeassistant.components.light import ColorMode


def test_known_model_has_its_real_cct_range() -> None:
    """The SL200III Bi is bi-colour 2800-6500 K, with no controllable fan."""
    caps = capabilities_for_radio_id("003F")
    assert caps.name == "SL200IIIBi"
    assert (caps.min_kelvin, caps.max_kelvin) == (2800, 6500)
    # It has a cooling fan, but exposes no controllable speeds: its catalogue
    # fanSpeedModes list is empty. An earlier version of the capability table
    # read a "hasFan" field that does not exist in the catalogue, and so marked
    # every one of the 184 models as having a controllable fan.
    assert caps.has_fan is False
    assert caps.fan_modes == ()
    assert caps.color_mode is ColorMode.COLOR_TEMP


def test_known_model_offers_its_own_named_effects() -> None:
    """Effects come from the model, named, not from one list shared by all."""
    caps = capabilities_for_radio_id("003F")

    names = [effect.name for effect in caps.effects]
    assert "Lightning" in names
    assert "Candle" in names
    # Wire symbols are the catalogue ids minus one, which for this model is
    # exactly the set its firmware's 0xF3 handler compares against.
    assert [effect.symbol for effect in caps.effects] == [1, 3, 4, 5, 6, 7, 8]


def test_a_model_with_a_fan_lists_its_speeds() -> None:
    """The M600D has controllable fan speeds; they carry their wire codes."""
    caps = capabilities_for_radio_id("000A")

    assert caps.has_fan is True
    assert [(m.code, m.name) for m in caps.fan_modes] == [
        (0, "Silent"),
        (1, "Automatic"),
        (2, "Low"),
        (3, "High"),
    ]


def test_wide_range_model_keeps_its_wide_range() -> None:
    """A 1800-10000 K light must not be clamped to the old hardcoded 2800-6500."""
    caps = capabilities_for_radio_id("00B6")  # SL200 RF
    assert (caps.min_kelvin, caps.max_kelvin) == (1800, 10000)


def test_daylight_model_is_brightness_only() -> None:
    """A fixed-CCT (daylight) light exposes brightness, not a useless slider."""
    caps = capabilities_for_radio_id("000E")  # SL100D, 5600 K fixed
    assert caps.min_kelvin == caps.max_kelvin == 5600
    assert caps.is_daylight is True
    assert caps.color_mode is ColorMode.BRIGHTNESS


def test_unknown_model_falls_back_to_a_safe_default() -> None:
    """An unrecognised radioId must still produce a working colour-temp light."""
    caps = capabilities_for_radio_id("ZZZZ")
    assert caps is not DEFAULT_CAPABILITIES or caps == DEFAULT_CAPABILITIES
    assert caps.color_mode is ColorMode.COLOR_TEMP
    assert caps.min_kelvin < caps.max_kelvin
    assert caps.name is None  # no invented product name


def test_none_radio_id_falls_back_to_default() -> None:
    caps = capabilities_for_radio_id(None)
    assert caps == DEFAULT_CAPABILITIES


def test_radio_id_is_case_insensitive() -> None:
    assert capabilities_for_radio_id("003f") == capabilities_for_radio_id("003F")


def test_known_models_are_enumerable_for_a_picker() -> None:
    """The config flow needs (radioId, label) pairs to offer a model picker."""
    models = known_models()
    assert ("003F", "SL200IIIBi") in [(rid, m.name) for rid, m in models.items()]
    assert len(models) > 150


def test_generic_class_from_device_uuid() -> None:
    """The first bytes of the provisioning UUID give the broad control class.

    Layout (confirmed from PrivateDevice.filter): vid = uuid[0:2] little-endian
    (0x0211 Telink), pid = uuid[2] — 1=ct, 2=hsl, 7=panel.
    """
    ct = bytes([0x11, 0x02, 0x01]) + bytes(13)
    hsl = bytes([0x11, 0x02, 0x02]) + bytes(13)
    panel = bytes([0x11, 0x02, 0x07]) + bytes(13)
    assert generic_class_from_device_uuid(ct) == "ct"
    assert generic_class_from_device_uuid(hsl) == "hsl"
    assert generic_class_from_device_uuid(panel) == "panel"


def test_generic_class_rejects_non_telink_or_short_uuid() -> None:
    assert generic_class_from_device_uuid(bytes([0x99, 0x99, 0x01])) is None
    assert generic_class_from_device_uuid(b"\x11\x02") is None
    assert generic_class_from_device_uuid(None) is None


def test_capabilities_is_immutable() -> None:
    caps = capabilities_for_radio_id("003F")
    with pytest.raises(Exception):
        caps.min_kelvin = 1000  # type: ignore[misc]
    assert isinstance(caps, GodoxCapabilities)


def test_model_names_use_one_spelling_of_each_suffix() -> None:
    """Godox's catalogue is inconsistent; the model picker should not be.

    Their data spells the bi-colour suffix "Bi" 57 times, "BI" twice and "bi"
    twice, which showed up verbatim in the config flow's dropdown. The generator
    normalises to the dominant spelling.
    """
    import re

    from custom_components.godox_mesh.capabilities import known_models

    names = [caps.name or "" for caps in known_models().values()]
    spellings = {m.group(0) for name in names for m in re.finditer(r"[Bb][Ii]", name)}

    assert spellings <= {"Bi"}, f"mixed bi-colour spellings in the picker: {spellings}"
