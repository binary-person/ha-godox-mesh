"""Tests for the Godox V2 status request/response and the wider command set.

Mapped from a UL150Bi II firmware image and confirmed against an SL200III Bi;
see docs/state-readback-investigation.md.
"""

from __future__ import annotations

import pytest

from godox_mesh_bt.protocol import (
    SUB_EFFECT,
    SUB_FAN,
    SUB_POWER,
    SUB_SET_CCT,
    SUB_STATUS_REQUEST,
    SUB_STATUS_CCT,
    SUB_STATUS_EFFECT,
    StatusResponse,
    build_effect_command,
    build_fan_command,
    build_status_request,
    build_v2_command,
    parse_status_response,
)


def test_sub_command_constants_match_the_firmware_dispatch() -> None:
    """The values the light's receive dispatch actually tests for."""
    assert (SUB_POWER, SUB_SET_CCT, SUB_EFFECT, SUB_FAN, SUB_STATUS_REQUEST) == (
        0xFE,
        0xF0,
        0xF3,
        0xF5,
        0xFD,
    )


def test_status_request_is_a_well_formed_v2_frame() -> None:
    frame = build_status_request()

    assert len(frame) == 8
    assert frame[0] == SUB_STATUS_REQUEST
    # Byte 1 must be non-zero: zero selects the echo reply rather than status.
    assert frame[1] != 0
    # The end byte selects which record to report. Padding it with 0xFF gets no
    # reply at all from real hardware; the 0xA0 record is the one that answers.
    assert frame == build_v2_command(SUB_STATUS_REQUEST, 0xA0, bytes([0x01]))


def test_parses_a_real_cct_status_reply() -> None:
    """Captured from an SL200III Bi answering a 0xFD request."""
    reply = parse_status_response(bytes.fromhex("a00a1b32ffff01f9"))

    assert reply.sub_command == SUB_STATUS_CCT
    assert reply.brightness == 10
    assert reply.cct == 2700
    assert reply.effect is None
    assert reply.raw.hex() == "a00a1b32ffff01f9"


def test_parses_an_effect_status_reply() -> None:
    """The firmware's other branch: 0xA3 reports effect rather than colour."""
    frame = build_v2_command(SUB_STATUS_EFFECT, 0x01, bytes([50, 4, 7, 0xFF]))
    reply = parse_status_response(frame)

    assert reply.sub_command == SUB_STATUS_EFFECT
    assert reply.brightness == 50
    assert reply.effect == 4
    assert reply.cct is None


def test_status_response_rejects_a_bad_checksum() -> None:
    bad = bytearray(bytes.fromhex("a00a1b32ffff01f9"))
    bad[-1] ^= 0xFF
    with pytest.raises(ValueError, match="checksum"):
        parse_status_response(bytes(bad))


def test_status_response_rejects_an_unknown_sub_command() -> None:
    frame = build_v2_command(0x12, 0x00, bytes([1, 2, 3]))
    with pytest.raises(ValueError, match="not a status response"):
        parse_status_response(frame)


@pytest.mark.parametrize("effect", [1, 3, 4, 5, 6, 7, 8])
def test_effect_command_accepts_the_ids_the_firmware_dispatches(effect: int) -> None:
    frame = build_effect_command(effect, brightness=80)

    assert frame[0] == SUB_EFFECT
    assert frame[1] == 80
    assert frame[2] == effect


def test_effect_command_accepts_any_byte_symbol() -> None:
    """Effect sets are per-model, so the builder cannot police the symbol.

    EFFECT_IDS is only a fallback for unknown models, not a universal list.
    """
    assert build_effect_command(2, brightness=50)[2] == 2
    assert build_effect_command(17, brightness=50)[2] == 17
    with pytest.raises(ValueError, match="effect"):
        build_effect_command(256, brightness=50)

def test_effect_command_validates_brightness() -> None:
    with pytest.raises(ValueError, match="brightness"):
        build_effect_command(1, brightness=101)


@pytest.mark.parametrize("mode", [0, 1, 2, 3, 4])
def test_fan_command_covers_every_state(mode: int) -> None:
    frame = build_fan_command(mode)

    assert frame[0] == SUB_FAN
    assert frame[1] == mode


def test_fan_command_accepts_medium_speed() -> None:
    """Code 4 is Medium; the vendor app emits it between Low (2) and High (3)."""
    assert build_fan_command(4)[1] == 4


def test_fan_command_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="fan"):
        build_fan_command(5)


def test_effect_command_carries_speed_in_byte_two() -> None:
    """The vendor app sends [brightness, symbol, speed]; byte 2 is not the id."""
    frame = build_effect_command(4, brightness=80, speed=2)

    assert frame[1] == 80
    assert frame[2] == 4
    assert frame[3] == 2


def test_status_response_round_trips_through_build_v2_command() -> None:
    """Anything the parser accepts must be something the builder can produce."""
    frame = build_v2_command(SUB_STATUS_CCT, 0x01, bytes([42, 45, 50, 0xFF]))
    reply = parse_status_response(frame)

    assert reply.brightness == 42
    assert reply.cct == 4500


def test_status_response_is_immutable() -> None:
    reply = parse_status_response(bytes.fromhex("a00a1b32ffff01f9"))
    with pytest.raises(Exception):
        reply.brightness = 99  # type: ignore[misc]
    assert isinstance(reply, StatusResponse)
