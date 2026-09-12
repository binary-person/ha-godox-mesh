"""Build Bluetooth Mesh Config Server access payloads.

Examples
--------
>>> build_config_app_key_add(0, 0, bytes(range(16))).hex()
'00000000000102030405060708090a0b0c0d0e0f'
"""

TELINK_COMPANY_ID: int = 0x0211
TELINK_VENDOR_MODEL_ID: int = 0x0000


def build_config_app_key_add(
    net_key_index: int,
    app_key_index: int,
    app_key: bytes,
) -> bytes:
    """Build a Config App Key Add access payload.

    Parameters
    ----------
    net_key_index
        Bluetooth Mesh NetKey index.
    app_key_index
        Bluetooth Mesh AppKey index.
    app_key
        Sixteen-byte AppKey value.

    Returns
    -------
    bytes
        Config App Key Add access payload.

    Examples
    --------
    >>> build_config_app_key_add(0, 0, bytes(range(16))).hex()
    '00000000000102030405060708090a0b0c0d0e0f'
    """
    if len(app_key) != 16:
        raise ValueError(f"app_key must be 16 bytes, got {len(app_key)}")
    packed = (net_key_index & 0xFFF) | ((app_key_index & 0xFFF) << 12)
    index_bytes = packed.to_bytes(3, "little")
    return bytes([0x00]) + index_bytes + app_key


def build_config_model_app_bind(
    element_address: int,
    app_key_index: int,
    company_id: int,
    model_id: int,
) -> bytes:
    """Build a Config Model App Bind payload for a vendor model.

    Parameters
    ----------
    element_address
        Unicast address of the element that owns the model.
    app_key_index
        AppKey index to bind.
    company_id
        Vendor company identifier.
    model_id
        Vendor model identifier.

    Returns
    -------
    bytes
        Config Model App Bind access payload.

    Examples
    --------
    >>> build_config_model_app_bind(2, 0, TELINK_COMPANY_ID, TELINK_VENDOR_MODEL_ID).hex()
    '803d0200000011020000'
    """
    return (
        bytes([0x80, 0x3D])
        + element_address.to_bytes(2, "little")
        + app_key_index.to_bytes(2, "little")
        + company_id.to_bytes(2, "little")
        + model_id.to_bytes(2, "little")
    )
