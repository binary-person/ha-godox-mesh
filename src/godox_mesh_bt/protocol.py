"""Build and parse Godox vendor payloads.

Examples
--------
>>> build_v2_command(0xFE, 0xFF, bytes([0x00])).hex()
'fe00ffffffffff7f'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from godox_mesh_bt.crypto import checksum

logger = logging.getLogger(__name__)

# V2 sub-commands, taken from the receive dispatch of a UL150Bi II firmware
# image. These are the only values the light tests for; anything else falls
# through unhandled. See docs/state-readback-investigation.md.
SUB_ACK: Final = 0xE0
"""Acknowledges an unsolicited 0xB0 notification and clears its retry counter."""

SUB_SET_CCT: Final = 0xF0
"""Brightness and colour temperature."""

SUB_EFFECT: Final = 0xF3
"""Lighting effect, with its own brightness."""

SUB_UNKNOWN_F4: Final = 0xF4
"""Dispatched but its handler only touches unidentified state."""

SUB_FAN: Final = 0xF5
"""Fan or cooling mode, four states."""

SUB_UNKNOWN_FA: Final = 0xFA
"""Dispatched to the same stub as 0xF4."""

SUB_STATUS_REQUEST: Final = 0xFD
"""Asks the light to report its state. The reply arrives on RESPONSE_OPCODE."""

SUB_POWER: Final = 0xFE
"""Power. Polarity is reversed: data byte 0x00 turns the light on."""

# Sub-commands the light sends back.
SUB_STATUS_CCT: Final = 0xA0
"""Status reply while in colour-temperature mode."""

SUB_STATUS_EFFECT: Final = 0xA3
"""Status reply while an effect is running."""

SUB_STATUS_ECHO: Final = 0xAD
"""Reply to a status request whose first data byte is zero."""

SUB_STATUS_BATTERY: Final = 0xA6
"""Battery/power reply, sent when a status request carries the 0xA6 end byte."""

SUB_STATUS_ACK: Final = 0xE0
"""Acknowledges a status reply so the light stops retransmitting it."""

SUB_STATUS_VERSION: Final = 0xAF
"""Version reply family. Sub-type 0x20 carries the BLE firmware version."""

SUB_EVENT: Final = 0xB0
"""Unsolicited notification of a locally-made change, retried until acked."""

#: A small fallback effect set for models with no catalogue data. These values
#: are the SL200III Bi's own symbols, kept only because something must be
#: offered when the model is unknown. It is **not** a universal
#: list: Godox publishes a per-model effect set (16 distinct sets across the
#: supported range, symbols spanning 0-19), so prefer the model's own list.
EFFECT_IDS: Final = (1, 3, 4, 5, 6, 7, 8)

#: Fan or cooling mode codes accepted by the 0xF5 handler. Code 4 was missing
#: here until it was read back out of the vendor app, which maps its own
#: (mode, speed) pair onto these five codes.
FAN_MODES: Final = (0, 1, 2, 3, 4)

#: Human names for :data:`FAN_MODES`. The order is not monotonic in speed --
#: 4 sits between 2 and 3 -- which is why the firmware appears to "permute" the
#: values. Code 0 is *silent*, not off: the vendor catalogue's English label
#: reads "OFF" but its Chinese (the source language), Korean, German, Spanish,
#: French, Italian and Japanese all say "mute".
#:
#: The Home Assistant integration builds its own per-model list from the
#: capability table; this is for direct library users.
FAN_MODE_NAMES: Final = {
    0: "Silent",
    1: "Automatic",
    2: "Low",
    4: "Medium",
    3: "High",
}

#: Data byte the light returns in the unused positions of a status reply.
_STATUS_PAD: Final = 0xFF



@dataclass(frozen=True)
class BatteryPower:
    """Parsed battery and power status fields.

    Parameters
    ----------
    state
        Raw power-state byte reported by the light.
    hour
        Remaining runtime hours reported by the light.
    minute
        Remaining runtime minutes reported by the light.
    option
        Battery option bit extracted from the response.
    power_percent
        Battery power percentage.

    Examples
    --------
    >>> parse_battery_power_response(bytes.fromhex("a600000040000000")).power_percent
    64
    """

    state: int
    hour: int
    minute: int
    option: int
    power_percent: int


@dataclass(frozen=True)
class GodoxV2Payload:
    """Decoded fixed-width Godox V2 command payload.

    Parameters
    ----------
    model
        Godox model or command family byte.
    data
        Five command data bytes.
    end_byte
        Command terminator or brightness decimal byte.
    checksum
        CRC-8 checksum byte.

    Examples
    --------
    >>> parse_v2_payload(bytes.fromhex("fe00ffffffffff7f")).model
    254
    """

    model: int
    data: bytes
    end_byte: int
    checksum: int


def validate_brightness(value: int) -> int:
    """Validate a Godox brightness percentage.

    Parameters
    ----------
    value
        Brightness percentage from 0 through 100.

    Returns
    -------
    int
        The validated brightness value.

    Examples
    --------
    >>> validate_brightness(50)
    50
    >>> validate_brightness(101)
    Traceback (most recent call last):
    ...
    ValueError: brightness must be between 0 and 100
    """

    if not 0 <= value <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return value


#: What the wire can carry: colour temperature travels as one byte of hundreds
#: of Kelvin. These are *protocol* limits, deliberately not any one light's
#: limits -- Godox mesh models range from 1800 K to 10000 K, and hardcoding the
#: 2800-6500 K of the lights this library was developed against rejected valid
#: commands for half the range.
CCT_MIN_KELVIN: Final = 100
CCT_MAX_KELVIN: Final = 25500


def validate_cct(
    value: int,
    *,
    min_kelvin: int = CCT_MIN_KELVIN,
    max_kelvin: int = CCT_MAX_KELVIN,
) -> int:
    """Validate a Godox correlated color temperature value.

    Parameters
    ----------
    value
        Color temperature in Kelvin.
    min_kelvin, max_kelvin
        The accepted range. Defaults to what the protocol can encode; pass the
        model's own range (from the capability table) to reject values that
        particular light will not honour.

    Returns
    -------
    int
        The validated color temperature.

    Examples
    --------
    >>> validate_cct(5600)
    5600
    >>> validate_cct(1800)          # a 1800-10000 K light, rejected before
    1800
    >>> validate_cct(2700, min_kelvin=2800, max_kelvin=6500)
    Traceback (most recent call last):
    ...
    ValueError: CCT must be between 2800K and 6500K
    """

    if not min_kelvin <= value <= max_kelvin:
        raise ValueError(f"CCT must be between {min_kelvin}K and {max_kelvin}K")
    return value


def build_v2_command(model: int, end_byte: int, data: bytes) -> bytes:
    """Build an 8-byte Godox V2 command payload.

    Parameters
    ----------
    model
        Godox model or command family byte.
    end_byte
        End byte to place before the checksum.
    data
        Up to five command data bytes; shorter values are padded with ``0xFF``.

    Returns
    -------
    bytes
        Packed V2 payload as ``model + padded data + end_byte + checksum``.

    Examples
    --------
    >>> build_v2_command(0xFE, 0xFF, bytes([0x00])).hex()
    'fe00ffffffffff7f'
    """
    if len(data) > 5:
        raise ValueError("V2 command data must be at most 5 bytes")

    # Pad data with 0xFF up to 5 bytes
    padded_data = data + b"\xFF" * (5 - len(data))
    payload = bytes([model]) + padded_data + bytes([end_byte])
    crc = checksum(payload)
    command = payload + bytes([crc])
    logger.debug("built V2 command model=0x%02x len=%d", model, len(command))
    return command


def parse_v2_payload(payload: bytes) -> GodoxV2Payload:
    """Parse and validate an 8-byte Godox V2 command payload.

    Parameters
    ----------
    payload
        Full V2 payload including checksum.

    Returns
    -------
    GodoxV2Payload
        Parsed payload fields.

    Examples
    --------
    >>> parsed = parse_v2_payload(bytes.fromhex("f032383200000032"))
    >>> parsed.data.hex()
    '3238320000'
    """

    if len(payload) != 8:
        raise ValueError("V2 payload must be exactly 8 bytes")

    expected = checksum(payload[:7])
    actual = payload[7]
    if actual != expected:
        raise ValueError("invalid V2 payload checksum")

    parsed = GodoxV2Payload(
        model=payload[0],
        data=payload[1:6],
        end_byte=payload[6],
        checksum=actual,
    )
    logger.debug("parsed V2 payload model=0x%02x", parsed.model)
    return parsed


def build_v3_command(cmd: int, data: bytes) -> bytes:
    """Build a variable-length Godox V3 command payload.

    Parameters
    ----------
    cmd
        V3 command byte.
    data
        Command data bytes.

    Returns
    -------
    bytes
        Packed V3 payload with length and checksum bytes.

    Examples
    --------
    >>> build_v3_command(0xA6, b"\\x01").hex()
    'a6040142'
    """
    length = len(data) + 3
    payload = bytes([cmd, length]) + data
    crc = checksum(payload)
    command = payload + bytes([crc])
    logger.debug("built V3 command cmd=0x%02x len=%d", cmd, len(command))
    return command


def parse_battery_power_response(data: bytes) -> BatteryPower:
    """Parse a Godox V3 battery/power response.

    Parameters
    ----------
    data
        Raw response bytes beginning with ``0xA6``.

    Returns
    -------
    BatteryPower
        Parsed power status.

    Examples
    --------
    >>> parse_battery_power_response(bytes.fromhex("a600000040000000"))
    BatteryPower(state=0, hour=0, minute=0, option=0, power_percent=64)
    """

    if len(data) < 8:
        raise ValueError("battery response must be at least 8 bytes")
    if data[0] != 0xA6:
        raise ValueError("battery response must start with 0xA6")

    parsed = BatteryPower(
        state=data[1],
        hour=data[2],
        minute=data[3],
        option=data[4] >> 7,
        power_percent=data[4] & 0x7F,
    )
    logger.debug("parsed battery response percent=%d", parsed.power_percent)
    return parsed




@dataclass(frozen=True)
class StatusResponse:
    """A status reply from the light.

    Sent in answer to :func:`build_status_request`, on the response opcode
    rather than the request opcode. Which fields are populated depends on what
    the light is doing: colour temperature mode reports ``cct``, effect mode
    reports ``effect``.

    Parameters
    ----------
    sub_command
        ``SUB_STATUS_CCT`` or ``SUB_STATUS_EFFECT``.
    brightness
        Brightness percentage the light reports.
    cct
        Colour temperature in Kelvin, or ``None`` in effect mode.
    effect
        Effect identifier, or ``None`` in colour temperature mode.
    extra
        The third data byte. In colour temperature mode this mirrors the value
        sent as the third byte of a ``0xF0`` command.
    raw
        The complete eight-byte reply, checksum included.

    Examples
    --------
    >>> parse_status_response(bytes.fromhex("a00a1b32ffff01f9")).cct
    2700
    """

    sub_command: int
    brightness: int
    cct: int | None
    effect: int | None
    extra: int
    raw: bytes


def build_status_request(
    data_byte: int = 0x01, record: int = SUB_STATUS_CCT
) -> bytes:
    """Build a request asking the light to report its state.

    The end byte selects *which* record the light reports, and this matters: a
    request padded with 0xFF gets no reply at all on real hardware, while
    selecting the ``0xA0`` record returns live brightness. Verified on an
    SL200III Bi.

    Parameters
    ----------
    data_byte
        First data byte. Must be non-zero: the firmware treats zero as a
        request for an echo reply rather than a status report.
    record
        Which status record to ask for. Defaults to the brightness/colour
        record, :data:`SUB_STATUS_CCT`.

    Returns
    -------
    bytes
        Eight-byte V2 frame. Send it with the request opcode; the answer comes
        back on the response opcode.

    Examples
    --------
    >>> build_status_request().hex()
    'fd01ffffffffa095'
    """

    if not 0 < data_byte <= 0xFF:
        raise ValueError("status request data byte must be between 1 and 255")
    return build_v2_command(SUB_STATUS_REQUEST, record, bytes([data_byte]))


def build_battery_request() -> bytes:
    """Build a request asking a battery-powered light to report its charge.

    Mirrors the Godox app's ``getBatteryPower``: sub-command ``0xFD``, first data
    byte ``0x01``, and the end byte ``0xA6`` which selects the battery record.
    The reply is a ``0xA6`` frame parsed by :func:`parse_battery_power_response`,
    and arrives on the response opcode.

    Returns
    -------
    bytes
        Eight-byte V2 frame.

    Examples
    --------
    >>> build_battery_request().hex()
    'fd01ffffffffa648'
    """

    return build_v2_command(SUB_STATUS_REQUEST, SUB_STATUS_BATTERY, bytes([0x01]))


def build_version_request() -> bytes:
    """Build a request asking a light for its BLE firmware version.

    Mirrors the Godox app's ``getFirmwareVersion``: sub-command ``0xFD`` with
    data byte ``0x02``. The reply is an ``0xAF 0x20`` frame; parse it with
    :func:`parse_version_response`. The readback firmware patch makes a light
    report version ``1``, which is how the patch is detected.

    Returns
    -------
    bytes
        Eight-byte V2 frame.

    Examples
    --------
    >>> build_version_request().hex()
    'fd02ffffffffff56'
    """

    return build_v2_command(SUB_STATUS_REQUEST, 0xFF, bytes([0x02]))


def parse_version_response(payload: bytes) -> int:
    """Return the BLE firmware version from an ``0xAF 0x20`` reply.

    Parameters
    ----------
    payload
        The V2 frame carried by a response-opcode message.

    Returns
    -------
    int
        The reported version. Stock Godox firmware reports a value like 102
        (``0x66``); the readback patch reports ``1``.

    Raises
    ------
    ValueError
        If the frame is not an ``0xAF 0x20`` version reply.

    Examples
    --------
    >>> parse_version_response(bytes.fromhex("af20000066ffff00"))
    102
    """

    if len(payload) < 5 or payload[0] != SUB_STATUS_VERSION or payload[1] != 0x20:
        raise ValueError("not a version reply (expected 0xAF 0x20)")
    return payload[4]


def build_status_ack(record: int = SUB_STATUS_CCT) -> bytes:
    """Build an acknowledgement that stops the light retransmitting a reply.

    The light repeats a status reply until it is acknowledged, so an unacked
    answer keeps arriving and can be mistaken for the answer to a later query.

    Examples
    --------
    >>> build_status_ack(0xA0).hex()
    'e0a0ffffffffffa7'
    """

    return build_v2_command(SUB_STATUS_ACK, _STATUS_PAD, bytes([record]))


def parse_status_response(payload: bytes) -> StatusResponse:
    """Parse a status reply from the light.

    Parameters
    ----------
    payload
        The eight-byte V2 frame carried by a response-opcode message.

    Returns
    -------
    StatusResponse
        Parsed reply.

    Raises
    ------
    ValueError
        If the frame is malformed, the checksum is wrong, or the sub-command is
        not one the light uses for status.

    Examples
    --------
    >>> reply = parse_status_response(bytes.fromhex("a00a1b32ffff01f9"))
    >>> reply.brightness, reply.cct
    (10, 2700)
    """

    parsed = parse_v2_payload(payload)
    if parsed.model not in (SUB_STATUS_CCT, SUB_STATUS_EFFECT):
        raise ValueError(
            f"0x{parsed.model:02x} is not a status response sub-command"
        )

    brightness = parsed.data[0]
    second = parsed.data[1]
    is_cct = parsed.model == SUB_STATUS_CCT
    response = StatusResponse(
        sub_command=parsed.model,
        brightness=brightness,
        # The light reports colour temperature in hundreds of Kelvin. It is
        # deliberately not range-checked here: a light may report a value
        # outside the range it accepts in commands, and a status reading should
        # be reported as received rather than rejected.
        cct=second * 100 if is_cct else None,
        effect=None if is_cct else second,
        extra=parsed.data[2],
        raw=bytes(payload),
    )
    logger.debug("parsed status response sub=0x%02x", parsed.model)
    return response


def build_effect_command(effect: int, brightness: int, speed: int = 0) -> bytes:
    """Build a lighting effect command.

    Parameters
    ----------
    effect
        Effect symbol. Which symbols a light dispatches is model-specific; an
        unrecognised one is silently ignored by the firmware. Note that the
        vendor app derives this from the catalogue's effect id, subtracting one
        on newer models -- see :func:`godox_mesh_bt.protocol.EFFECT_IDS`.
    brightness
        Brightness percentage from 0 through 100.
    speed
        Effect speed. The number of usable steps is per-effect (the catalogue
        calls it ``gear``); 0 is always valid.

    Returns
    -------
    bytes
        Eight-byte V2 frame.

    Examples
    --------
    >>> build_effect_command(4, brightness=80).hex()
    'f3500400ffffff54'
    >>> build_effect_command(4, brightness=80, speed=2).hex()
    'f3500402ffffff53'
    """

    if not 0 <= effect <= 0xFF:
        raise ValueError(f"effect must be 0-255, got {effect}")
    if not 0 <= speed <= 0xFF:
        raise ValueError(f"speed must be 0-255, got {speed}")
    validate_brightness(brightness)
    # Data byte 0 is brightness, byte 1 the effect symbol and byte 2 the speed.
    # An earlier version of this function repeated the effect in byte 2, which
    # happened to look right because the two are equal for some effects; the
    # vendor app sends [brightness, symbol, speed, 0xFF, 0xFF].
    return build_v2_command(
        SUB_EFFECT, _STATUS_PAD, bytes([brightness, effect, speed])
    )


def build_fan_command(mode: int) -> bytes:
    """Build a fan or cooling mode command.

    Parameters
    ----------
    mode
        One of :data:`FAN_MODES`. The firmware maps these through a permutation
        internally, so the meaning of each value is not necessarily ordered.

    Returns
    -------
    bytes
        Eight-byte V2 frame.

    Examples
    --------
    >>> build_fan_command(2).hex()
    'f502ffffffffffa7'
    """

    if mode not in FAN_MODES:
        raise ValueError(f"fan mode must be one of {FAN_MODES}, got {mode}")
    return build_v2_command(SUB_FAN, _STATUS_PAD, bytes([mode]))
