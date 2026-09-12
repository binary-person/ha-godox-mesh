"""Provisioning PDU builders and parsers for BT Mesh PB-GATT provisioning.

All PDUs follow the proxy framing defined in Mesh Profile spec section 6.6:

    [SAR|MessageType (1 byte)] [PDU type (1 byte)] [payload (N bytes)]

The ``MessageType`` for provisioning PDUs is always ``0x03``
(``PROXY_TYPE_PROVISIONING``).  Builders include this byte; parsers expect it
to have already been stripped (i.e. they receive ``[PDU type][payload]``).

PDU type codes (Mesh Profile § 5.4.1)
--------------------------------------
0x00  Invite          1-byte payload
0x01  Capabilities    11-byte payload
0x02  Start           5-byte payload
0x03  Public Key      64-byte payload
0x04  Input Complete  0-byte payload (OOB flows only)
0x05  Confirmation    16-byte payload
0x06  Random          16-byte payload
0x07  Data            33-byte payload (25 data + 8 MIC)
0x08  Complete        0-byte payload
0x09  Failed          1-byte error code
"""
from __future__ import annotations

from dataclasses import dataclass

PROXY_TYPE_PROVISIONING = 0x03

# PDU type codes
PDU_INVITE        = 0x00
PDU_CAPABILITIES  = 0x01
PDU_START         = 0x02
PDU_PUBLIC_KEY    = 0x03
PDU_INPUT_COMPLETE = 0x04  # OOB auth only
PDU_CONFIRMATION  = 0x05
PDU_RANDOM        = 0x06
PDU_DATA          = 0x07
PDU_COMPLETE      = 0x08
PDU_FAILED        = 0x09


class ProvisioningFailed(Exception):
    """Raised when device sends Provisioning Failed PDU.

    Parameters
    ----------
    error_code : int
        Error code from the device (1 byte, spec § 5.4.1.9).
    """

    def __init__(self, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(f"Provisioning failed with error code {error_code:#04x}")


@dataclass
class ProvisioningCapabilities:
    """Parsed Provisioning Capabilities PDU (device → provisioner).

    Attributes
    ----------
    num_elements : int
        Number of elements in the node (1 byte).
    algorithms : int
        Supported algorithms bitmask (2 bytes, big-endian).
    pub_key_type : int
        Supported public-key types bitmask (1 byte).
    static_oob_type : int
        Static OOB information bitmask (1 byte).
    output_oob_size : int
        Maximum output OOB size (1 byte).
    output_oob_action : int
        Supported output OOB actions (2 bytes, big-endian).
    input_oob_size : int
        Maximum input OOB size (1 byte).
    input_oob_action : int
        Supported input OOB actions (2 bytes, big-endian).
    raw : bytes
        Original 11-byte payload required for ConfirmationInputs construction.
    """

    num_elements: int
    algorithms: int
    pub_key_type: int
    static_oob_type: int
    output_oob_size: int
    output_oob_action: int
    input_oob_size: int
    input_oob_action: int
    raw: bytes


# --- Builders (provisioner → device) ---

def _wrap(pdu_type: int, payload: bytes) -> bytes:
    """Wrap a payload in a proxy provisioning PDU frame.

    Parameters
    ----------
    pdu_type : int
        Provisioning PDU type byte (e.g. ``PDU_INVITE``).
    payload : bytes
        PDU-specific payload.

    Returns
    -------
    bytes
        ``[0x03, pdu_type] + payload`` — ready to write to ``0x2ADB``.
    """
    return bytes([PROXY_TYPE_PROVISIONING, pdu_type]) + payload


def build_invite(attention_duration: int = 0) -> bytes:
    """Build a Provisioning Invite PDU.

    Parameters
    ----------
    attention_duration : int
        Attention timer value in seconds (0–255, default 0).

    Returns
    -------
    bytes
        3-byte PDU: ``[0x03, 0x00, attention_duration]``.

    Examples
    --------
    >>> build_invite(0).hex()
    '030000'
    >>> len(build_invite(5))
    3
    """
    return _wrap(PDU_INVITE, bytes([attention_duration]))


def build_start(
    algorithm: int = 0,
    pub_key_type: int = 0,
    auth_method: int = 0,
    auth_action: int = 0,
    auth_size: int = 0,
) -> bytes:
    """Build a Provisioning Start PDU (No-OOB defaults).

    Parameters
    ----------
    algorithm : int
        Key derivation algorithm index (0 = FIPS P-256).
    pub_key_type : int
        Public key type (0 = No OOB).
    auth_method : int
        Authentication method (0 = No OOB).
    auth_action : int
        Authentication action (0 for No-OOB).
    auth_size : int
        Authentication size (0 for No-OOB).

    Returns
    -------
    bytes
        7-byte PDU: proxy header (2) + 5-byte payload.

    Examples
    --------
    >>> build_start().hex()
    '03020000000000'
    >>> len(build_start())
    7
    """
    return _wrap(PDU_START, bytes([algorithm, pub_key_type, auth_method, auth_action, auth_size]))


def build_public_key(pub_key_bytes: bytes) -> bytes:
    """Build a Provisioning Public Key PDU.

    Parameters
    ----------
    pub_key_bytes : bytes
        Raw 64-byte uncompressed P-256 public key (X||Y, 32 bytes each).

    Returns
    -------
    bytes
        66-byte PDU: proxy header (2) + 64-byte public key.

    Raises
    ------
    ValueError
        If ``pub_key_bytes`` is not exactly 64 bytes.

    Examples
    --------
    >>> len(build_public_key(bytes(64)))
    66
    """
    if len(pub_key_bytes) != 64:
        raise ValueError(f"pub_key_bytes must be 64 bytes, got {len(pub_key_bytes)}")
    return _wrap(PDU_PUBLIC_KEY, pub_key_bytes)


def build_confirmation(confirmation: bytes) -> bytes:
    """Build a Provisioning Confirmation PDU.

    Parameters
    ----------
    confirmation : bytes
        16-byte AES-CMAC confirmation value.

    Returns
    -------
    bytes
        18-byte PDU: proxy header (2) + 16-byte confirmation.

    Raises
    ------
    ValueError
        If ``confirmation`` is not exactly 16 bytes.

    Examples
    --------
    >>> len(build_confirmation(bytes(16)))
    18
    """
    if len(confirmation) != 16:
        raise ValueError(f"confirmation must be 16 bytes, got {len(confirmation)}")
    return _wrap(PDU_CONFIRMATION, confirmation)


def build_random(random_bytes: bytes) -> bytes:
    """Build a Provisioning Random PDU.

    Parameters
    ----------
    random_bytes : bytes
        16 bytes of provisioner random value.

    Returns
    -------
    bytes
        18-byte PDU: proxy header (2) + 16-byte random.

    Raises
    ------
    ValueError
        If ``random_bytes`` is not exactly 16 bytes.

    Examples
    --------
    >>> len(build_random(bytes(16)))
    18
    """
    if len(random_bytes) != 16:
        raise ValueError(f"random must be 16 bytes, got {len(random_bytes)}")
    return _wrap(PDU_RANDOM, random_bytes)


def build_data(encrypted_data: bytes) -> bytes:
    """Build a Provisioning Data PDU.

    Parameters
    ----------
    encrypted_data : bytes
        33-byte AES-CCM ciphertext: 25 bytes encrypted data + 8-byte MIC.

    Returns
    -------
    bytes
        35-byte PDU: proxy header (2) + 33-byte encrypted data.

    Raises
    ------
    ValueError
        If ``encrypted_data`` is not exactly 33 bytes.

    Examples
    --------
    >>> len(build_data(bytes(33)))
    35
    """
    if len(encrypted_data) != 33:
        raise ValueError(f"encrypted_data must be 33 bytes, got {len(encrypted_data)}")
    return _wrap(PDU_DATA, encrypted_data)


# --- Parsers (device → provisioner) ---
# Input is the raw payload bytes AFTER stripping the pdu_type byte.

def parse_capabilities(payload: bytes) -> ProvisioningCapabilities:
    """Parse a Capabilities payload (device → provisioner).

    Parameters
    ----------
    payload : bytes
        11-byte Capabilities payload (PDU type byte already stripped).

    Returns
    -------
    ProvisioningCapabilities
        Decoded fields plus the original raw bytes for ConfirmationInputs.

    Raises
    ------
    ValueError
        If ``payload`` is not exactly 11 bytes.

    Examples
    --------
    >>> caps = parse_capabilities(bytes(11))
    >>> caps.num_elements
    0
    """
    if len(payload) != 11:
        raise ValueError(f"Capabilities payload must be 11 bytes, got {len(payload)}")
    return ProvisioningCapabilities(
        num_elements=payload[0],
        algorithms=int.from_bytes(payload[1:3], "big"),
        pub_key_type=payload[3],
        static_oob_type=payload[4],
        output_oob_size=payload[5],
        output_oob_action=int.from_bytes(payload[6:8], "big"),
        input_oob_size=payload[8],
        input_oob_action=int.from_bytes(payload[9:11], "big"),
        raw=bytes(payload),
    )


def parse_public_key(payload: bytes) -> bytes:
    """Parse a Public Key payload.

    Parameters
    ----------
    payload : bytes
        64-byte uncompressed P-256 public key (X||Y).

    Returns
    -------
    bytes
        64-byte raw public key, copied.

    Raises
    ------
    ValueError
        If ``payload`` is not exactly 64 bytes.

    Examples
    --------
    >>> len(parse_public_key(bytes(64)))
    64
    """
    if len(payload) != 64:
        raise ValueError(f"Public key payload must be 64 bytes, got {len(payload)}")
    return bytes(payload)


def parse_confirmation(payload: bytes) -> bytes:
    """Parse a Confirmation payload.

    Parameters
    ----------
    payload : bytes
        16-byte AES-CMAC confirmation value.

    Returns
    -------
    bytes
        16-byte confirmation value, copied.

    Raises
    ------
    ValueError
        If ``payload`` is not exactly 16 bytes.

    Examples
    --------
    >>> len(parse_confirmation(bytes(16)))
    16
    """
    if len(payload) != 16:
        raise ValueError(f"Confirmation must be 16 bytes, got {len(payload)}")
    return bytes(payload)


def parse_random(payload: bytes) -> bytes:
    """Parse a Random payload.

    Parameters
    ----------
    payload : bytes
        16-byte random value from the device.

    Returns
    -------
    bytes
        16-byte random value, copied.

    Raises
    ------
    ValueError
        If ``payload`` is not exactly 16 bytes.

    Examples
    --------
    >>> len(parse_random(bytes(16)))
    16
    """
    if len(payload) != 16:
        raise ValueError(f"Random must be 16 bytes, got {len(payload)}")
    return bytes(payload)


def parse_pdu(raw: bytes) -> tuple[int, bytes]:
    """Parse a provisioning PDU received from the device (proxy header already stripped).

    Parameters
    ----------
    raw : bytes
        Bytes starting with the PDU type byte, followed by the payload.

    Returns
    -------
    tuple[int, bytes]
        ``(pdu_type, payload)`` where *pdu_type* is one of the ``PDU_*``
        constants and *payload* is the remaining bytes.

    Raises
    ------
    ProvisioningFailed
        If the device sent a ``PDU_FAILED`` (``0x09``) PDU.

    Examples
    --------
    >>> parse_pdu(bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
    (1, b'\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00')
    """
    pdu_type = raw[0]
    payload = raw[1:]
    if pdu_type == PDU_FAILED:
        error_code = payload[0] if payload else 0xFF
        raise ProvisioningFailed(error_code)
    return pdu_type, payload


# Alias for test compatibility
parse_pdu_type = parse_pdu
