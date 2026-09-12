"""Tests for Provisioning PDU builders and parsers."""
import pytest
from godox_mesh_bt.provisioning_pdu import (
    build_invite,
    build_start,
    build_public_key,
    build_confirmation,
    build_random,
    build_data,
    parse_capabilities,
    parse_public_key,
    parse_confirmation,
    parse_random,
    ProvisioningCapabilities,
    ProvisioningFailed,
    PROXY_TYPE_PROVISIONING,
)

# --- Builders ---

def test_build_invite_structure():
    pdu = build_invite(attention_duration=0)
    assert pdu == bytes([0x03, 0x00, 0x00])

def test_build_invite_attention():
    pdu = build_invite(attention_duration=5)
    assert pdu[2] == 5

def test_build_start_no_oob():
    """No OOB: algorithm=0, pub_key=0, auth_method=0, action=0, size=0."""
    pdu = build_start(algorithm=0, pub_key_type=0, auth_method=0, auth_action=0, auth_size=0)
    assert pdu == bytes([0x03, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00])

def test_build_public_key_length():
    pub_bytes = bytes(64)
    pdu = build_public_key(pub_bytes)
    assert len(pdu) == 66  # 1 proxy + 1 type + 64 key
    assert pdu[0] == 0x03
    assert pdu[1] == 0x03

def test_build_public_key_payload():
    pub_bytes = bytes(range(64))
    pdu = build_public_key(pub_bytes)
    assert pdu[2:] == pub_bytes

def test_build_confirmation_length():
    pdu = build_confirmation(bytes(16))
    assert len(pdu) == 18  # 1 proxy + 1 type + 16
    assert pdu[1] == 0x05

def test_build_random_length():
    pdu = build_random(bytes(16))
    assert len(pdu) == 18
    assert pdu[1] == 0x06

def test_build_data_length():
    pdu = build_data(bytes(33))
    assert len(pdu) == 35  # 1 proxy + 1 type + 33
    assert pdu[1] == 0x07

# --- Parsers ---

def test_parse_capabilities_from_bytes():
    """Parse a Capabilities PDU payload (11 bytes, no proxy prefix)."""
    # num_elements=1, algorithms=0x0001 (P-256 FIPS), pub_key_type=0, static_oob=0,
    # output_oob_size=0, output_oob_action=0, input_oob_size=0, input_oob_action=0
    raw = bytes([0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    caps = parse_capabilities(raw)
    assert isinstance(caps, ProvisioningCapabilities)
    assert caps.num_elements == 1
    assert caps.algorithms == 0x0001
    assert caps.raw == raw

def test_parse_public_key_64_bytes():
    raw = bytes(range(64))
    key_bytes = parse_public_key(raw)
    assert key_bytes == raw

def test_parse_public_key_wrong_length_raises():
    with pytest.raises(ValueError, match="64"):
        parse_public_key(bytes(32))

def test_parse_confirmation_16_bytes():
    raw = bytes(range(16))
    assert parse_confirmation(raw) == raw

def test_parse_random_16_bytes():
    raw = bytes(range(16))
    assert parse_random(raw) == raw

def test_parse_failed_raises():
    with pytest.raises(ProvisioningFailed, match="0x04"):
        raise ProvisioningFailed(0x04)

def test_proxy_type_provisioning_constant():
    assert PROXY_TYPE_PROVISIONING == 0x03
