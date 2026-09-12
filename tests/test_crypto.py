from __future__ import annotations

from godox_mesh_bt.crypto import (
    GODOX_CRC8_TABLE,
    aes_ccm_decrypt,
    aes_ccm_encrypt,
    k2,
    k3,
    k4,
    pack_proxy_config_pdu,
)


def test_godox_crc8_table_matches_apk() -> None:
    # Verified literals from com.godox.agm.CRC8Util.java
    # Index 7 is R.styleable... = 131
    # Index 8 is CipherSuite... = 194 (matching poly 0x8c LSB first)
    expected_start = [0, 94, 188, 226, 97, 63, 221, 131, 194, 156, 126, 32, 163, 253, 31, 65]
    assert GODOX_CRC8_TABLE[:16] == expected_start
    assert len(GODOX_CRC8_TABLE) == 256


def test_k2_derivation() -> None:
    # Reference values from tasks.md and Telink implementation
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    nid, enc_key, priv_key = k2(net_key)

    assert nid == 0x59
    assert enc_key.hex() == "459dece2bbfdc5a7e7e3ba3d172a5bcd"
    assert priv_key.hex() == "68a78e1bb5bca29dd926c3737af4b285"


def test_k3_derivation() -> None:
    # Reference value for NetKey 125b33087af5d8f300114c2d4891378b
    net_key = bytes.fromhex("125b33087af5d8f300114c2d4891378b")
    network_id = k3(net_key)
    assert network_id.hex() == "760552b77673d397"


def test_k4_derivation() -> None:
    # Reference value for AppKey 414bf26e7af1eb6a0f642628470ebf8d
    app_key = bytes.fromhex("414bf26e7af1eb6a0f642628470ebf8d")
    aid = k4(app_key)
    assert aid == 0x21


def test_aes_ccm_round_trip() -> None:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    nonce = bytes.fromhex("00000000000000000000000000")  # 13 bytes
    plaintext = b"hello mesh"
    mic_length = 4

    ciphertext = aes_ccm_encrypt(key, nonce, plaintext, mic_length)
    assert len(ciphertext) == len(plaintext) + mic_length

    decrypted = aes_ccm_decrypt(key, nonce, ciphertext, mic_length)
    assert decrypted == plaintext


def test_pack_proxy_config_pdu_matches_expected_lengths() -> None:
    net_key = bytes.fromhex("bc6bf99f840f9ca379562468a110d2a9")
    filter_type_pdu = pack_proxy_config_pdu(
        opcode=0x00,
        parameters=b"\x01",
        net_key=net_key,
        iv_index=0,
        seq=300,
        src=0x0001,
    )
    whitelist_pdu = pack_proxy_config_pdu(
        opcode=0x01,
        parameters=b"\x00\x01\xff\xff",
        net_key=net_key,
        iv_index=0,
        seq=301,
        src=0x0001,
    )

    assert filter_type_pdu[0] == 0x02
    assert whitelist_pdu[0] == 0x02
    assert len(filter_type_pdu) == 20
    assert len(whitelist_pdu) == 23


def test_encrypt_decrypt_device_key_pdu_roundtrip() -> None:
    """Device key encrypt/decrypt is symmetric."""
    from godox_mesh_bt.crypto import decrypt_device_key_pdu, encrypt_device_key_pdu

    device_key = bytes.fromhex("6277be2be27af9818c3d79b62a2a8ae7")
    plaintext = bytes.fromhex("0880ff")  # Config CDG payload
    seq = 50000
    src = 0x0001
    dst = 0x0002
    iv_index = 0
    encrypted = encrypt_device_key_pdu(plaintext, device_key, iv_index, seq, src, dst)
    decrypted = decrypt_device_key_pdu(encrypted, device_key, iv_index, seq, src, dst)
    assert decrypted == plaintext


def test_encrypt_device_key_pdu_header_is_akf_zero() -> None:
    """Device key PDU header byte must have AKF=0 and AID=0, so header=0x00."""
    from godox_mesh_bt.crypto import encrypt_device_key_pdu

    device_key = bytes.fromhex("6277be2be27af9818c3d79b62a2a8ae7")
    encrypted = encrypt_device_key_pdu(b"\x00\x01\x02", device_key, 0, 1, 0x0001, 0x0002)
    # First byte is the transport header: AKF(1bit)|AID(6bits)|0(1bit)
    # AKF=0, AID=0 → 0x00
    assert encrypted[0] == 0x00


def test_encrypt_device_key_pdu_different_seq_produces_different_ciphertext() -> None:
    """Different sequence numbers must produce different ciphertexts (nonce includes SEQ)."""
    from godox_mesh_bt.crypto import encrypt_device_key_pdu

    device_key = bytes.fromhex("6277be2be27af9818c3d79b62a2a8ae7")
    plaintext = b"\x08\x80\xff"
    enc1 = encrypt_device_key_pdu(plaintext, device_key, 0, 100, 0x0001, 0x0002)
    enc2 = encrypt_device_key_pdu(plaintext, device_key, 0, 101, 0x0001, 0x0002)
    assert enc1 != enc2


def test_k1_basic() -> None:
    """k1 test vector from BT Mesh spec section 8.1.2."""
    from godox_mesh_bt.crypto import k1, aes_cmac

    N    = bytes.fromhex("3216d1509884b533248541792b877f98")
    SALT = bytes.fromhex("2ba14ffa0df84a2831938d57d276cab4")
    P    = b"smk2"
    result = k1(N, SALT, P)
    T = aes_cmac(SALT, N)
    expected = aes_cmac(T, P)
    assert result == expected


def test_k1_provisioning_labels() -> None:
    """k1 with provisioning labels produces 16-byte results."""
    from godox_mesh_bt.crypto import k1

    secret = bytes(32)
    salt   = bytes(16)
    for label in (b"prsk", b"prsn", b"prdk", b"prck"):
        result = k1(secret, salt, label)
        assert len(result) == 16


def test_k1_session_nonce_is_bytes_3_to_15() -> None:
    """Session nonce is k1(secret, salt, b'prsn')[3:]."""
    from godox_mesh_bt.crypto import k1

    full = k1(bytes(32), bytes(16), b"prsn")
    nonce = full[3:]
    assert len(nonce) == 13


def test_generate_p256_keypair_returns_bytes() -> None:
    from godox_mesh_bt.crypto import generate_p256_keypair

    private_key, pub_bytes = generate_p256_keypair()
    assert len(pub_bytes) == 64


def test_ecdh_shared_secret_is_32_bytes() -> None:
    from godox_mesh_bt.crypto import generate_p256_keypair, ecdh_shared_secret

    priv_a, pub_a = generate_p256_keypair()
    priv_b, pub_b = generate_p256_keypair()
    secret_ab = ecdh_shared_secret(priv_a, pub_b)
    secret_ba = ecdh_shared_secret(priv_b, pub_a)
    assert len(secret_ab) == 32
    assert secret_ab == secret_ba


def test_ecdh_different_pairs_produce_different_secrets() -> None:
    from godox_mesh_bt.crypto import generate_p256_keypair, ecdh_shared_secret

    priv_a, pub_a = generate_p256_keypair()
    priv_b, pub_b = generate_p256_keypair()
    priv_c, pub_c = generate_p256_keypair()
    assert ecdh_shared_secret(priv_a, pub_b) != ecdh_shared_secret(priv_a, pub_c)


# PROV-CRYPTO-03: Confirmation value computation


def test_compute_confirmation_salt_length() -> None:
    from godox_mesh_bt.crypto import compute_confirmation_salt

    inputs = bytes(145)
    salt = compute_confirmation_salt(inputs)
    assert len(salt) == 16


def test_compute_confirmation_salt_uses_s1() -> None:
    """confirmation_salt = s1(confirmation_inputs)."""
    from godox_mesh_bt.crypto import compute_confirmation_salt, s1

    inputs = bytes(range(145))
    assert compute_confirmation_salt(inputs) == s1(inputs)


def test_compute_confirmation_value_length() -> None:
    from godox_mesh_bt.crypto import compute_confirmation_value

    ecdh_secret = bytes(32)
    conf_salt = bytes(16)
    random_16 = bytes(16)
    result = compute_confirmation_value(ecdh_secret, conf_salt, random_16)
    assert len(result) == 16


def test_compute_confirmation_value_deterministic() -> None:
    from godox_mesh_bt.crypto import compute_confirmation_value

    secret = bytes(range(32))
    salt = bytes(range(16))
    rand = bytes(range(16, 32))
    r1 = compute_confirmation_value(secret, salt, rand)
    r2 = compute_confirmation_value(secret, salt, rand)
    assert r1 == r2


def test_build_confirmation_inputs_length() -> None:
    from godox_mesh_bt.crypto import build_confirmation_inputs

    invite = bytes([0x00])
    caps = bytes(11)
    start = bytes([0, 0, 0, 0, 0])
    prov_pk = bytes(64)
    dev_pk = bytes(64)
    result = build_confirmation_inputs(invite, caps, start, prov_pk, dev_pk)
    assert len(result) == 145
    assert result == invite + caps + start + prov_pk + dev_pk


# PROV-CRYPTO-04: Session key, nonce, device key, data encryption


def test_compute_provision_salt_length() -> None:
    from godox_mesh_bt.crypto import compute_provision_salt

    conf_salt = bytes(16)
    prov_rand = bytes(16)
    dev_rand = bytes(16)
    result = compute_provision_salt(conf_salt, prov_rand, dev_rand)
    assert len(result) == 16


def test_derive_session_key_length() -> None:
    from godox_mesh_bt.crypto import derive_session_key

    secret = bytes(32)
    salt = bytes(16)
    assert len(derive_session_key(secret, salt)) == 16


def test_derive_session_nonce_is_13_bytes() -> None:
    from godox_mesh_bt.crypto import derive_session_nonce

    secret = bytes(32)
    salt = bytes(16)
    nonce = derive_session_nonce(secret, salt)
    assert len(nonce) == 13


def test_derive_device_key_length() -> None:
    from godox_mesh_bt.crypto import derive_device_key

    secret = bytes(32)
    salt = bytes(16)
    assert len(derive_device_key(secret, salt)) == 16


def test_encrypt_provisioning_data_length() -> None:
    """Encrypted provisioning data = 25 bytes plaintext + 8-byte tag = 33 bytes."""
    from godox_mesh_bt.crypto import encrypt_provisioning_data

    net_key = bytes(16)
    key_index = 0
    flags = 0
    iv_index = 0
    unicast = 0x0002
    session_key = bytes(16)
    session_nonce = bytes(13)
    ciphertext = encrypt_provisioning_data(
        net_key, key_index, flags, iv_index, unicast, session_key, session_nonce
    )
    assert len(ciphertext) == 33


def test_encrypt_provisioning_data_uses_aes_ccm() -> None:
    """Decrypt with the same key/nonce should recover plaintext."""
    from godox_mesh_bt.crypto import encrypt_provisioning_data
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM

    net_key = bytes(range(16))
    key_index = 0x0000
    flags = 0x00
    iv_index = 0x00000000
    unicast = 0x0001
    session_key = bytes(16)
    session_nonce = bytes(13)
    ciphertext = encrypt_provisioning_data(
        net_key, key_index, flags, iv_index, unicast, session_key, session_nonce
    )
    aesccm = AESCCM(session_key, tag_length=8)
    plaintext = aesccm.decrypt(session_nonce, ciphertext, None)
    expected = (
        net_key
        + key_index.to_bytes(2, "big")
        + bytes([flags])
        + iv_index.to_bytes(4, "big")
        + unicast.to_bytes(2, "big")
    )
    assert plaintext == expected
