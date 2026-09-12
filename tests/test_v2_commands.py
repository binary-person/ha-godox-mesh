from __future__ import annotations

import pytest

from godox_mesh_bt.protocol import (
    GodoxV2Payload,
    build_v2_command,
    parse_battery_power_response,
    parse_v2_payload,
)


def test_build_v2_power_on() -> None:
    # GodoxCommandApi.changeLightSwitch(address, true)
    # model=0xFE, end_byte=0xFF, data=[0x00, 0xFF, 0xFF, 0xFF, 0xFF]
    # FE 00 FF FF FF FF FF 7F
    command = build_v2_command(0xFE, 0xFF, bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
    assert command.hex().upper() == "FE00FFFFFFFFFF7F"


def test_build_v2_power_off() -> None:
    # GodoxCommandApi.changeLightSwitch(address, false)
    # model=0xFE, end_byte=0xFF, data=[0x01, 0xFF, 0xFF, 0xFF, 0xFF]
    command = build_v2_command(0xFE, 0xFF, bytes([0x01, 0xFF, 0xFF, 0xFF, 0xFF]))
    # FE 01 FF FF FF FF FF <CRC8>
    from godox_mesh_bt.crypto import checksum
    expected_crc = checksum(bytes.fromhex("FE01FFFFFFFFFF"))
    assert command.hex().upper() == f"FE01FFFFFFFFFF{expected_crc:02X}"


def test_build_v2_change_cct() -> None:
    # GodoxCommandApi.changeLightCCT(address, brightness, brightness_point, temperature, gm, circle, gm2)
    # model=240 (0xF0), brightness_point=0, data=[brightness, temp, gm, circle, gm2]
    # Captured app packets use gm=50, circle=0, gm2=0 for CCT commands.
    command = build_v2_command(0xF0, 0, bytes([50, 56, 50, 0, 0]))
    # Expected: F0 32 38 32 00 00 00 <CRC8>
    from godox_mesh_bt.crypto import checksum
    expected_crc = checksum(bytes.fromhex("F0323832000000"))
    assert command.hex().upper() == f"F0323832000000{expected_crc:02X}"


def test_build_v2_get_battery_power() -> None:
    # GodoxCommandApi.getBatteryPower(address)
    # model=0xFD (253), end_byte=0xE5 (229), data=[0x01]
    command = build_v2_command(0xFD, 0xE5, bytes([0x01]))
    # Expected: FD 01 FF FF FF FF E5 <CRC8>
    from godox_mesh_bt.crypto import checksum
    expected_crc = checksum(bytes.fromhex("FD01FFFFFFFFE5"))
    assert command.hex().upper() == f"FD01FFFFFFFFE5{expected_crc:02X}"


def test_parse_battery_power_response() -> None:
    response = parse_battery_power_response(bytes.fromhex("A6010203D2FFFFFF"))

    assert response.state == 1
    assert response.hour == 2
    assert response.minute == 3
    assert response.option == 1
    assert response.power_percent == 82


def test_parse_battery_power_response_rejects_short_payload() -> None:
    with pytest.raises(ValueError, match="battery response must be at least 8 bytes"):
        parse_battery_power_response(bytes.fromhex("A6010203D2"))


def test_parse_battery_power_response_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="battery response must start with 0xA6"):
        parse_battery_power_response(bytes.fromhex("FE010203D2FFFFFF"))


def test_parse_v2_payload_decodes_live_a0_response() -> None:
    payload = parse_v2_payload(bytes.fromhex("A00A1B32FFFF0345"))

    assert payload == GodoxV2Payload(
        model=0xA0,
        data=bytes.fromhex("0A1B32FFFF"),
        end_byte=0x03,
        checksum=0x45,
    )


def test_parse_v2_payload_rejects_bad_checksum() -> None:
    with pytest.raises(ValueError, match="invalid V2 payload checksum"):
        parse_v2_payload(bytes.fromhex("A00A1B32FFFF0300"))


@pytest.mark.parametrize(
    ("access_payload_hex", "brightness", "temperature_hundreds"),
    [
        ("f01102f03237320000005d", 50, 55),
        ("f01102f0322b3200000000", 50, 43),
        ("f01102f032203200000070", 50, 32),
        ("f01102f0324132000000ac", 50, 65),
        ("f01102f001413200000018", 1, 65),
    ],
)
def test_captured_official_app_cct_sequence_uses_v2_f0_payloads(
    access_payload_hex: str,
    brightness: int,
    temperature_hundreds: int,
) -> None:
    vendor_opcode = bytes.fromhex("f01102")
    access_payload = bytes.fromhex(access_payload_hex)

    payload = parse_v2_payload(access_payload.removeprefix(vendor_opcode))

    assert access_payload.startswith(vendor_opcode)
    assert payload.model == 0xF0
    assert payload.end_byte == 0
    assert payload.data == bytes([brightness, temperature_hundreds, 50, 0, 0])
