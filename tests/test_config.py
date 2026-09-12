"""Tests for Config Server PDU builders."""

from godox_mesh_bt.config import (
    build_config_app_key_add,
    build_config_model_app_bind,
    TELINK_COMPANY_ID,
    TELINK_VENDOR_MODEL_ID,
)


def test_config_app_key_add_opcode():
    app_key = bytes(16)
    payload = build_config_app_key_add(net_key_index=0, app_key_index=0, app_key=app_key)
    assert payload[0] == 0x00  # opcode


def test_config_app_key_add_length():
    app_key = bytes(16)
    payload = build_config_app_key_add(net_key_index=0, app_key_index=0, app_key=app_key)
    assert len(payload) == 20


def test_config_app_key_add_key_indices_packed():
    """NetKeyIndex=0, AppKeyIndex=1 → [0x00, 0x10, 0x00] in the index field."""
    app_key = bytes(16)
    payload = build_config_app_key_add(net_key_index=0, app_key_index=1, app_key=app_key)
    # bytes 1-3: packed 12-bit indices
    packed = int.from_bytes(payload[1:4], "little")
    net_idx = packed & 0xFFF
    app_idx = (packed >> 12) & 0xFFF
    assert net_idx == 0
    assert app_idx == 1


def test_config_app_key_add_app_key_embedded():
    app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    payload = build_config_app_key_add(net_key_index=0, app_key_index=0, app_key=app_key)
    assert payload[4:20] == app_key


def test_config_model_app_bind_opcode():
    payload = build_config_model_app_bind(
        element_address=0x0002,
        app_key_index=0,
        company_id=TELINK_COMPANY_ID,
        model_id=TELINK_VENDOR_MODEL_ID,
    )
    assert payload[0:2] == bytes([0x80, 0x3D])


def test_config_model_app_bind_length():
    payload = build_config_model_app_bind(
        element_address=0x0002,
        app_key_index=0,
        company_id=TELINK_COMPANY_ID,
        model_id=TELINK_VENDOR_MODEL_ID,
    )
    assert len(payload) == 10


def test_config_model_app_bind_element_address():
    payload = build_config_model_app_bind(
        element_address=0x0002,
        app_key_index=0,
        company_id=TELINK_COMPANY_ID,
        model_id=TELINK_VENDOR_MODEL_ID,
    )
    assert int.from_bytes(payload[2:4], "little") == 0x0002


def test_config_model_app_bind_telink_vendor_model_bytes():
    """Telink CompanyID=0x0211, ModelID=0x0000 must produce [0x11, 0x02, 0x00, 0x00]."""
    payload = build_config_model_app_bind(
        element_address=0x0002,
        app_key_index=0,
        company_id=TELINK_COMPANY_ID,
        model_id=TELINK_VENDOR_MODEL_ID,
    )
    assert payload[6:10] == bytes([0x11, 0x02, 0x00, 0x00])
