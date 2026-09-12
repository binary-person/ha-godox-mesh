"""Model identification from the advertisement, rather than from a name list."""

from __future__ import annotations

import pytest
from custom_components.godox_mesh.capabilities import (
    capabilities_for_radio_id,
    radio_id_from_manufacturer_data,
)


def _advert(radio_id: str, company: int = 0x0211) -> dict[int, bytes]:
    """Build manufacturer data carrying *radio_id* the way Godox lights do.

    The vendor app reads bytes 7 and 6 of the blob including the company id;
    bleak strips the company id, so those land at payload offsets 5 and 4.
    """
    value = int(radio_id, 16)
    payload = bytearray(16)
    payload[4] = value & 0xFF
    payload[5] = (value >> 8) & 0xFF
    return {company: bytes(payload)}


@pytest.mark.parametrize(
    ("radio_id", "expected_name"),
    [
        ("003F", "SL200IIIBi"),
        ("003A", "SL60IIBi"),
        ("000A", "M600D"),  # a fan model
        ("002A", "TP2R"),  # a single-speed-effects model
    ],
)
def test_any_model_is_identified_from_its_advertisement(
    radio_id: str, expected_name: str
) -> None:
    """Detection must not be limited to the lights this was developed against."""
    detected = radio_id_from_manufacturer_data(_advert(radio_id))

    assert detected == radio_id
    assert capabilities_for_radio_id(detected).name == expected_name


def test_detection_does_not_depend_on_the_company_id() -> None:
    """The offsets are relative to the blob, whatever company id carries it."""
    assert radio_id_from_manufacturer_data(_advert("003F", company=0x1234)) == "003F"


@pytest.mark.parametrize(
    "manufacturer_data",
    [None, {}, {0x0211: b""}, {0x0211: b"\x00" * 8}],
)
def test_missing_or_short_data_yields_nothing(manufacturer_data) -> None:
    """Too short to carry a model id must be None, never a wrong guess."""
    assert radio_id_from_manufacturer_data(manufacturer_data) is None


def test_an_unknown_model_id_is_returned_but_unmapped() -> None:
    """A light newer than this build still identifies; it just has no entry."""
    detected = radio_id_from_manufacturer_data(_advert("FFFE"))

    assert detected == "FFFE"
    assert capabilities_for_radio_id(detected).name is None


def test_every_model_in_the_table_round_trips() -> None:
    """No model in the shipped table is unidentifiable from its advertisement."""
    from custom_components.godox_mesh.capabilities import known_models

    for radio_id in known_models():
        assert radio_id_from_manufacturer_data(_advert(radio_id)) == radio_id


def test_the_detected_model_is_suggested_in_the_form() -> None:
    """The detected model must reach the rendered field, not just validation.

    `vol.Optional(key, default=X)` looks like it pre-fills a form and does not:
    it applies only at validation time, when the key is absent. Populating what
    the user actually sees requires `description={"suggested_value": X}`. This
    shipped once with `default=`, so detection worked, its answer was dropped
    before render, and a blank dropdown was indistinguishable from detection
    having failed.
    """
    from custom_components.godox_mesh.config_flow import GodoxConfigFlow
    from custom_components.godox_mesh.const import CONF_RADIO_ID

    flow = GodoxConfigFlow()
    flow._discovery = _FakeDiscovery(_advert("003F"))
    flow._title = "GD_LED"

    schema = flow._show_model_form()["data_schema"].schema
    key = next(k for k in schema if k == CONF_RADIO_ID)

    assert (key.description or {}).get("suggested_value") == "003F", (
        "the detected model must be offered as suggested_value, or the form "
        "renders blank and detection appears broken"
    )


class _FakeDiscovery:
    """Just the one attribute the detector reads."""

    def __init__(self, manufacturer_data: dict[int, bytes]) -> None:
        self.manufacturer_data = manufacturer_data


def test_the_discovery_card_shows_the_model_not_gd_led() -> None:
    """Every Godox light advertises as GD_LED, so the card must say more.

    With two lights on one network the discovery cards were identical, both
    titled "GD_LED", and the only way to tell them apart was the MAC in the
    next step.
    """
    from custom_components.godox_mesh.config_flow import _display_name

    assert _display_name(_FakeServiceInfo(_advert("003F"))) == "SL200IIIBi (EEFF)"
    assert _display_name(_FakeServiceInfo(_advert("003A"))) == "SL60IIBi (EEFF)"


def test_an_unknown_model_falls_back_to_the_advertised_name() -> None:
    """A light newer than the table still gets a usable label."""
    from custom_components.godox_mesh.config_flow import _display_name

    assert _display_name(_FakeServiceInfo({})) == "GD_LED (EEFF)"
    # With nothing else to go on the label is the address; do not repeat it.
    assert _display_name(_FakeServiceInfo({}, name="")) == "AA:BB:CC:DD:EE:FF"


class _FakeServiceInfo:
    """Only the three attributes _display_name reads."""

    def __init__(self, manufacturer_data, name="GD_LED"):
        self.manufacturer_data = manufacturer_data
        self.name = name
        self.address = "AA:BB:CC:DD:EE:FF"


def test_two_lights_of_one_model_get_distinguishable_labels() -> None:
    """The reason the suffix exists: identical models, identical cards."""
    from custom_components.godox_mesh.config_flow import _display_name

    first = _FakeServiceInfo(_advert("003F"))
    second = _FakeServiceInfo(_advert("003F"))
    second.address = "AA:BB:CC:DD:11:22"

    assert _display_name(first) != _display_name(second)
    assert _display_name(second) == "SL200IIIBi (1122)"
