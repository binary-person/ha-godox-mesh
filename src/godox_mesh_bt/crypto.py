"""Bluetooth Mesh and Godox packet cryptography helpers.

Examples
--------
>>> checksum(bytes.fromhex("fe00ffffffffff"))
127
>>> encode_vendor_opcode(135664).hex()
'f01102'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    EllipticCurvePrivateKey,
    EllipticCurvePublicNumbers,
    generate_private_key,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

logger = logging.getLogger(__name__)


GODOX_CRC8_TABLE = [
    0, 94, 188, 226, 97, 63, 221, 131, 194, 156, 126, 32, 163, 253, 31, 65,
    157, 195, 33, 127, 252, 162, 64, 30, 95, 1, 227, 189, 62, 96, 130, 220,
    35, 125, 159, 193, 66, 28, 254, 160, 225, 191, 93, 3, 128, 222, 60, 98,
    190, 224, 2, 92, 223, 129, 99, 61, 124, 34, 192, 158, 29, 67, 161, 255,
    70, 24, 250, 164, 39, 121, 155, 197, 132, 218, 56, 102, 229, 187, 89, 7,
    219, 133, 103, 57, 186, 228, 6, 88, 25, 71, 165, 251, 120, 38, 196, 154,
    101, 59, 217, 135, 4, 90, 184, 230, 167, 249, 27, 69, 198, 152, 122, 36,
    248, 166, 68, 26, 153, 199, 37, 123, 58, 100, 134, 216, 91, 5, 231, 185,
    140, 210, 48, 110, 237, 179, 81, 15, 78, 16, 242, 172, 47, 113, 147, 205,
    17, 79, 173, 243, 112, 46, 204, 146, 211, 141, 111, 49, 178, 236, 14, 80,
    175, 241, 19, 77, 206, 144, 114, 44, 109, 51, 209, 143, 12, 82, 176, 238,
    50, 108, 142, 208, 83, 13, 239, 177, 240, 174, 76, 18, 145, 207, 45, 115,
    202, 148, 118, 40, 171, 245, 23, 73, 8, 86, 180, 234, 105, 55, 213, 139,
    87, 9, 235, 181, 54, 104, 138, 212, 149, 203, 41, 119, 244, 170, 72, 22,
    233, 183, 85, 11, 136, 214, 52, 106, 43, 117, 151, 201, 74, 20, 246, 168,
    116, 42, 200, 150, 21, 75, 169, 247, 182, 232, 10, 84, 215, 137, 107, 53,
]


@dataclass(frozen=True)
class DecryptedProxyPDU:
    """Decrypted Mesh Proxy Network PDU fields.

    Parameters
    ----------
    src
        Mesh source unicast address.
    dst
        Mesh destination address.
    seq
        Mesh sequence number.
    access_payload
        Decrypted Access Layer payload bytes. The exact byte length is command
        dependent and is preserved from the decrypted PDU.

    Examples
    --------
    >>> DecryptedProxyPDU(src=1, dst=2, seq=3, access_payload=b"abc").access_payload
    b'abc'
    """

    src: int
    dst: int
    seq: int
    access_payload: bytes


def checksum(data: bytes) -> int:
    """Calculate the Godox CRC-8 checksum.

    Parameters
    ----------
    data
        Input bytes of any length.

    Returns
    -------
    int
        CRC-8 value from 0 through 255.

    Examples
    --------
    >>> checksum(bytes.fromhex("fe00ffffffffff"))
    127
    """

    crc = 0
    for byte in data:
        crc = GODOX_CRC8_TABLE[crc ^ byte]
    logger.debug("calculated checksum for %d byte(s)", len(data))
    return crc


def aes_cmac(key: bytes, data: bytes) -> bytes:
    """Calculate AES-CMAC.

    Parameters
    ----------
    key
        16-byte AES key.
    data
        Message bytes of any length.

    Returns
    -------
    bytes
        16-byte CMAC digest.

    Examples
    --------
    >>> len(aes_cmac(bytes(16), b"message"))
    16
    """

    c = cmac.CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def k2(net_key: bytes, p: bytes = b"\x00") -> tuple[int, bytes, bytes]:
    """Derive NID, EncryptionKey, and PrivacyKey from a NetKey.

    Parameters
    ----------
    net_key
        16-byte Bluetooth Mesh Network Key.
    p
        K2 input parameter bytes. Defaults to the Bluetooth Mesh ``0x00``
        parameter used for Network PDU encryption.

    Returns
    -------
    tuple[int, bytes, bytes]
        NID, 16-byte EncryptionKey, and 16-byte PrivacyKey.

    Examples
    --------
    >>> nid, encryption_key, privacy_key = k2(bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715"))
    >>> nid
    8
    >>> len(encryption_key), len(privacy_key)
    (16, 16)
    """
    salt = aes_cmac(bytes(16), b"smk2")
    t = aes_cmac(salt, net_key)

    t1 = aes_cmac(t, p + b"\x01")
    t2 = aes_cmac(t, t1 + p + b"\x02")
    t3 = aes_cmac(t, t2 + p + b"\x03")

    nid = t1[15] & 0x7F
    encryption_key = t2
    privacy_key = t3
    logger.debug("derived k2 nid=0x%02x", nid)

    return nid, encryption_key, privacy_key


def k3(net_key: bytes) -> bytes:
    """Derive an 8-byte Network ID from a NetKey.

    Parameters
    ----------
    net_key
        16-byte Bluetooth Mesh Network Key.

    Returns
    -------
    bytes
        8-byte Network ID.

    Examples
    --------
    >>> k3(bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")).hex()
    '76d34c230a1a7b01'
    """
    salt = aes_cmac(bytes(16), b"smk3")
    t = aes_cmac(salt, net_key)
    t2 = aes_cmac(t, b"id64\x01")
    logger.debug("derived k3 network id")
    return t2[8:]


def k4(app_key: bytes) -> int:
    """Derive a 6-bit Application Key ID from an AppKey.

    Parameters
    ----------
    app_key
        16-byte Bluetooth Mesh Application Key.

    Returns
    -------
    int
        Application Identifier from 0 through 63.

    Examples
    --------
    >>> k4(bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890"))
    49
    """
    salt = aes_cmac(bytes(16), b"smk4")
    t = aes_cmac(salt, app_key)
    t2 = aes_cmac(t, b"id6\x01")
    logger.debug("derived k4 aid")
    return t2[15] & 0x3F


def k1(N: bytes, SALT: bytes, P: bytes) -> bytes:
    """BT Mesh k1 key derivation (Section 3.8.2.2).

    T  = AES-CMAC(key=SALT, data=N)
    k1 = AES-CMAC(key=T, data=P)
    """
    T = aes_cmac(SALT, N)
    return aes_cmac(T, P)


def s1(M: bytes) -> bytes:
    """BT Mesh s1 SALT generation: AES-CMAC(key=bytes(16), data=M)."""
    return aes_cmac(bytes(16), M)


def build_confirmation_inputs(
    invite_payload: bytes,
    capabilities_payload: bytes,
    start_payload: bytes,
    provisioner_pubkey: bytes,
    device_pubkey: bytes,
) -> bytes:
    """Build ConfirmationInputs (145 bytes) for BT Mesh provisioning confirmation."""
    result = invite_payload + capabilities_payload + start_payload + provisioner_pubkey + device_pubkey
    if len(result) != 145:
        raise ValueError(f"ConfirmationInputs must be 145 bytes, got {len(result)}")
    return result


def compute_confirmation_salt(confirmation_inputs: bytes) -> bytes:
    """ConfirmationSalt = s1(ConfirmationInputs)."""
    return s1(confirmation_inputs)


def compute_confirmation_value(
    ecdh_secret: bytes,
    confirmation_salt: bytes,
    random_16: bytes,
    auth_value: bytes = b"\x00" * 16,
) -> bytes:
    """Compute provisioner Confirmation value.

    ConfirmationKey = k1(ECDHSecret, ConfirmationSalt, b"prck")
    Confirmation    = aes_cmac(ConfirmationKey, random_16 + auth_value)
    """
    confirmation_key = k1(ecdh_secret, confirmation_salt, b"prck")
    return aes_cmac(confirmation_key, random_16 + auth_value)


def compute_provision_salt(
    confirmation_salt: bytes,
    provisioner_random: bytes,
    device_random: bytes,
) -> bytes:
    """ProvisionSalt = s1(ConfirmationSalt + provisioner_random + device_random)."""
    return s1(confirmation_salt + provisioner_random + device_random)


def derive_session_key(ecdh_secret: bytes, provision_salt: bytes) -> bytes:
    """SessionKey = k1(ECDHSecret, ProvisionSalt, b'prsk')."""
    return k1(ecdh_secret, provision_salt, b"prsk")


def derive_session_nonce(ecdh_secret: bytes, provision_salt: bytes) -> bytes:
    """SessionNonce = k1(ECDHSecret, ProvisionSalt, b'prsn')[3:] = 13 bytes."""
    return k1(ecdh_secret, provision_salt, b"prsn")[3:]


def derive_device_key(ecdh_secret: bytes, provision_salt: bytes) -> bytes:
    """DeviceKey = k1(ECDHSecret, ProvisionSalt, b'prdk')."""
    return k1(ecdh_secret, provision_salt, b"prdk")


def encrypt_provisioning_data(
    net_key: bytes,
    key_index: int,
    flags: int,
    iv_index: int,
    unicast_address: int,
    session_key: bytes,
    session_nonce: bytes,
) -> bytes:
    """Encrypt BT Mesh provisioning data (25-byte plaintext → 33-byte ciphertext+tag).

    Returns 33 bytes (25 ciphertext + 8-byte AES-CCM tag).
    """
    plaintext = (
        net_key
        + key_index.to_bytes(2, "big")
        + bytes([flags])
        + iv_index.to_bytes(4, "big")
        + unicast_address.to_bytes(2, "big")
    )
    aesccm = AESCCM(session_key, tag_length=8)
    return aesccm.encrypt(session_nonce, plaintext, None)


def aes_ccm_encrypt(
    key: bytes,
    nonce: bytes,
    plaintext: bytes,
    mic_length: int,
    associated_data: bytes | None = None,
) -> bytes:
    """Encrypt bytes using AES-CCM.

    Parameters
    ----------
    key
        16-byte AES key.
    nonce
        AES-CCM nonce bytes.
    plaintext
        Plaintext bytes to encrypt.
    mic_length
        Authentication tag length in bytes.
    associated_data
        Optional authenticated associated data bytes.

    Returns
    -------
    bytes
        Ciphertext bytes followed by a ``mic_length`` byte authentication tag.

    Examples
    --------
    >>> ciphertext = aes_ccm_encrypt(bytes(16), bytes(13), b"abc", 4)
    >>> aes_ccm_decrypt(bytes(16), bytes(13), ciphertext, 4)
    b'abc'
    """

    ccm = AESCCM(key, tag_length=mic_length)
    logger.debug("encrypting %d byte(s) with AES-CCM", len(plaintext))
    return ccm.encrypt(nonce, plaintext, associated_data)


def aes_ccm_decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    mic_length: int,
    associated_data: bytes | None = None,
) -> bytes:
    """Decrypt bytes using AES-CCM.

    Parameters
    ----------
    key
        16-byte AES key.
    nonce
        AES-CCM nonce bytes.
    ciphertext
        Ciphertext bytes followed by the authentication tag.
    mic_length
        Authentication tag length in bytes.
    associated_data
        Optional authenticated associated data bytes.

    Returns
    -------
    bytes
        Decrypted plaintext bytes.

    Examples
    --------
    >>> ciphertext = aes_ccm_encrypt(bytes(16), bytes(13), b"abc", 4)
    >>> aes_ccm_decrypt(bytes(16), bytes(13), ciphertext, 4)
    b'abc'
    """

    ccm = AESCCM(key, tag_length=mic_length)
    logger.debug("decrypting %d byte(s) with AES-CCM", len(ciphertext))
    return ccm.decrypt(nonce, ciphertext, associated_data)


def obfuscate(pdu: bytes, privacy_key: bytes, iv_index: int) -> bytes:
    """Obfuscate a Bluetooth Mesh Network PDU header.

    Parameters
    ----------
    pdu
        Network PDU bytes. The first 14 bytes must be present because bytes
        7 through 13 form the privacy random value.
    privacy_key
        16-byte Bluetooth Mesh PrivacyKey.
    iv_index
        Bluetooth Mesh IV Index.

    Returns
    -------
    bytes
        PDU bytes with the six-byte network header obfuscated.

    Examples
    --------
    >>> pdu = bytes(range(20))
    >>> deobfuscate(obfuscate(pdu, bytes(16), 0), bytes(16), 0) == pdu
    True
    """

    privacy_random = pdu[7:14]
    iv_index_bytes = iv_index.to_bytes(4, "big")
    privacy_plaintext = bytes(5) + iv_index_bytes + privacy_random

    cipher = Cipher(algorithms.AES(privacy_key), modes.ECB())
    encryptor = cipher.encryptor()
    pecb = encryptor.update(privacy_plaintext) + encryptor.finalize()

    obfuscated = bytes([pdu[i] ^ pecb[i - 1] for i in range(1, 7)])
    logger.debug("obfuscated network pdu with iv_index=%s", iv_index)
    return pdu[0:1] + obfuscated + pdu[7:]


def deobfuscate(pdu: bytes, privacy_key: bytes, iv_index: int) -> bytes:
    """De-obfuscate a Bluetooth Mesh Network PDU header.

    Parameters
    ----------
    pdu
        Obfuscated Network PDU bytes.
    privacy_key
        16-byte Bluetooth Mesh PrivacyKey.
    iv_index
        Bluetooth Mesh IV Index.

    Returns
    -------
    bytes
        PDU bytes with the six-byte network header restored.

    Examples
    --------
    >>> pdu = bytes(range(20))
    >>> deobfuscate(obfuscate(pdu, bytes(16), 0), bytes(16), 0) == pdu
    True
    """

    return obfuscate(pdu, privacy_key, iv_index)


def encrypt_access_pdu(
    payload: bytes,
    app_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
) -> bytes:
    """Encrypt an Access PDU using the Telink application nonce.

    Parameters
    ----------
    payload
        Plain Access Layer payload bytes.
    app_key
        16-byte Bluetooth Mesh Application Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.

    Returns
    -------
    bytes
        One-byte AKF/AID header followed by encrypted payload and a 4-byte MIC.

    Examples
    --------
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> encrypted = encrypt_access_pdu(b"abc", app_key, 0, 1, 1, 2)
    >>> decrypt_access_pdu(encrypted, app_key, 0, 1, 1, 2)
    b'abc'
    """

    # Telink App Nonce (Type 1): Type(1) || (ASZMIC<<7)(1) || SEQ(3) || SRC(2) || DST(2) || IVIndex(4)
    nonce = (
        bytes([0x01, 0x00])
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv_index.to_bytes(4, "big")
    )

    aid = k4(app_key)
    # AKF=1, AID
    header = bytes([0x40 | aid])
    return header + aes_ccm_encrypt(app_key, nonce, payload, 4)


def decrypt_access_pdu(
    encrypted_access: bytes,
    app_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
) -> bytes:
    """Decrypt an Access PDU using the Telink application nonce.

    Parameters
    ----------
    encrypted_access
        One-byte AKF/AID header followed by ciphertext and 4-byte MIC.
    app_key
        16-byte Bluetooth Mesh Application Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.

    Returns
    -------
    bytes
        Plain Access Layer payload bytes.

    Examples
    --------
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> encrypted = encrypt_access_pdu(b"abc", app_key, 0, 1, 1, 2)
    >>> decrypt_access_pdu(encrypted, app_key, 0, 1, 1, 2)
    b'abc'
    """

    nonce = (
        bytes([0x01, 0x00])
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv_index.to_bytes(4, "big")
    )

    return aes_ccm_decrypt(app_key, nonce, encrypted_access[1:], 4)


def encrypt_network_pdu(
    dst: int,
    encrypted_access: bytes,
    encryption_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    ttl: int,
    ctl: int = 0,
) -> bytes:
    """Encrypt a Network PDU body using the Telink network nonce.

    Parameters
    ----------
    dst
        Mesh destination address.
    encrypted_access
        Encrypted access bytes, including AKF/AID header and transport MIC.
    encryption_key
        16-byte Bluetooth Mesh EncryptionKey from :func:`k2`.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    ttl
        Mesh Time To Live value.
    ctl
        Network CTL bit.

    Returns
    -------
    bytes
        Network PDU header fields plus encrypted destination/access data and
        4-byte network MIC, before NID and obfuscation are added.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> _, enc_key, _ = k2(net_key)
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> access = encrypt_access_pdu(b"abc", app_key, 0, 1, 1, 2)
    >>> encrypted = encrypt_network_pdu(2, access, enc_key, 0, 1, 1, 10)
    >>> len(encrypted) > len(access)
    True
    """

    ctl_ttl = (ctl << 7) | (ttl & 0x7F)
    seq_bytes = seq.to_bytes(3, "big")
    src_bytes = src.to_bytes(2, "big")

    # Telink Network Nonce (Type 0): Type(0) || CTL/TTL(1) || SEQ(3) || SRC(2) || 0000(2) || IVIndex(4)
    nonce = (
        bytes([0x00, ctl_ttl])
        + seq_bytes
        + src_bytes
        + bytes([0x00, 0x00])
        + iv_index.to_bytes(4, "big")
    )

    plain_net_data = dst.to_bytes(2, "big") + encrypted_access
    encrypted_data = aes_ccm_encrypt(encryption_key, nonce, plain_net_data, 4)

    return bytes([ctl_ttl]) + seq_bytes + src_bytes + encrypted_data


def decrypt_network_pdu(
    deobf_pdu: bytes, encryption_key: bytes, iv_index: int
) -> tuple[int, bytes]:
    """Decrypt a de-obfuscated Network PDU.

    Parameters
    ----------
    deobf_pdu
        Network PDU bytes after NID is present and header obfuscation has been
        removed.
    encryption_key
        16-byte Bluetooth Mesh EncryptionKey from :func:`k2`.
    iv_index
        Bluetooth Mesh IV Index.

    Returns
    -------
    tuple[int, bytes]
        Destination address and encrypted access bytes.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> nid, enc_key, _ = k2(net_key)
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> access = encrypt_access_pdu(b"abc", app_key, 0, 1, 1, 2)
    >>> body = encrypt_network_pdu(2, access, enc_key, 0, 1, 1, 10)
    >>> decrypt_network_pdu(bytes([nid]) + body, enc_key, 0)[0]
    2
    """

    ctl_ttl = deobf_pdu[1]
    seq = deobf_pdu[2:5]
    src = deobf_pdu[5:7]

    nonce = bytes([0x00, ctl_ttl]) + seq + src + bytes([0x00, 0x00]) + iv_index.to_bytes(4, "big")

    encrypted_data = deobf_pdu[7:]
    decrypted = aes_ccm_decrypt(encryption_key, nonce, encrypted_data, 4)

    dst = int.from_bytes(decrypted[0:2], "big")
    encrypted_access = decrypted[2:]
    return dst, encrypted_access


def pack_proxy_network_pdu(
    access_payload: bytes,
    net_key: bytes,
    app_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
    ttl: int,
) -> bytes:
    """Pack a complete Mesh Proxy Network PDU.

    Parameters
    ----------
    access_payload
        Plain Access Layer payload bytes, such as vendor opcode plus command
        bytes.
    net_key
        16-byte Bluetooth Mesh Network Key.
    app_key
        16-byte Bluetooth Mesh Application Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.
    ttl
        Mesh Time To Live value.

    Returns
    -------
    bytes
        Complete Proxy SAR/type byte ``0x00`` followed by obfuscated Network
        PDU bytes.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> pdu = pack_proxy_network_pdu(b"abc", net_key, app_key, 0, 1, 1, 2, 10)
    >>> decrypt_proxy_network_pdu(pdu, net_key, app_key, iv_index=0).access_payload
    b'abc'
    """

    nid, enc_key, priv_key = k2(net_key)

    encrypted_access = encrypt_access_pdu(access_payload, app_key, iv_index, seq, src, dst)
    encrypted_net_header_and_data = encrypt_network_pdu(
        dst, encrypted_access, enc_key, iv_index, seq, src, ttl
    )

    # MSB is IVI
    ivi = iv_index & 1
    byte0 = (ivi << 7) | nid
    network_pdu = bytes([byte0]) + encrypted_net_header_and_data
    obf_pdu = obfuscate(network_pdu, priv_key, iv_index)

    return bytes([0x00]) + obf_pdu  # Proxy SAR=0, Type=0


def decrypt_proxy_network_pdu(
    proxy_pdu: bytes,
    net_key: bytes,
    app_key: bytes,
    *,
    iv_index: int,
) -> DecryptedProxyPDU:
    """Decrypt a complete Mesh Proxy Network PDU into its access payload.

    Parameters
    ----------
    proxy_pdu
        Complete proxy PDU bytes. The first byte must be ``0x00`` for a
        complete Network PDU.
    net_key
        16-byte Bluetooth Mesh Network Key.
    app_key
        16-byte Bluetooth Mesh Application Key.
    iv_index
        Bluetooth Mesh IV Index.

    Returns
    -------
    DecryptedProxyPDU
        Source, destination, sequence number, and Access Layer payload bytes.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> app_key = bytes.fromhex("fa0a2c615756eca3f896ce061ed4d890")
    >>> pdu = pack_proxy_network_pdu(b"abc", net_key, app_key, 0, 1, 1, 2, 10)
    >>> decrypt_proxy_network_pdu(pdu, net_key, app_key, iv_index=0).src
    1
    """

    if not proxy_pdu:
        raise ValueError("proxy PDU is empty")
    if proxy_pdu[0] != 0x00:
        raise ValueError("proxy PDU must be a complete network PDU")

    network_pdu = proxy_pdu[1:]
    _, enc_key, priv_key = k2(net_key)
    deobf_pdu = deobfuscate(network_pdu, priv_key, iv_index)
    dst, encrypted_access = decrypt_network_pdu(deobf_pdu, enc_key, iv_index)
    seq = int.from_bytes(deobf_pdu[2:5], "big")
    src = int.from_bytes(deobf_pdu[5:7], "big")
    access_payload = decrypt_access_pdu(
        encrypted_access,
        app_key,
        iv_index,
        seq,
        src,
        dst,
    )
    logger.debug("decrypted proxy pdu src=0x%04x dst=0x%04x seq=%s", src, dst, seq)
    return DecryptedProxyPDU(src=src, dst=dst, seq=seq, access_payload=access_payload)


def pack_proxy_config_pdu(
    opcode: int,
    parameters: bytes,
    net_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
) -> bytes:
    """Pack a Telink-style proxy config PDU.

    Parameters
    ----------
    opcode
        Proxy configuration opcode byte.
    parameters
        Proxy configuration parameter bytes. For whitelist add, this is a
        sequence of two-byte big-endian addresses.
    net_key
        16-byte Bluetooth Mesh Network Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.

    Returns
    -------
    bytes
        Complete proxy PDU beginning with SAR/type byte ``0x02``.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> pack_proxy_config_pdu(0, b"\\x00", net_key, 0, 1, 1)[0]
    2
    """

    seq_bytes = seq.to_bytes(3, "big")
    src_bytes = src.to_bytes(2, "big")
    dst_bytes = (0).to_bytes(2, "big")
    nid, enc_key, priv_key = k2(net_key)
    nonce = (
        bytes([0x03, 0x00])
        + seq_bytes
        + src_bytes
        + bytes([0x00, 0x00])
        + iv_index.to_bytes(4, "big")
    )

    # Payload = dst(2) + opcode(1) + parameters, matching Telink's proxy control flow.
    payload = bytes([opcode]) + parameters

    encrypted = aes_ccm_encrypt(enc_key, nonce, dst_bytes + payload, 8)

    logger.debug("packed proxy config pdu opcode=0x%02x", opcode)
    ivi = iv_index & 1
    ctl_ttl = 0x80
    network_pdu = bytes([(ivi << 7) | nid]) + bytes([ctl_ttl]) + seq_bytes + src_bytes + encrypted
    return bytes([0x02]) + obfuscate(network_pdu, priv_key, iv_index)


def pack_proxy_device_key_pdu(
    access_payload: bytes,
    net_key: bytes,
    device_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
    ttl: int = 10,
) -> bytes:
    """Pack a complete Mesh Proxy Network PDU using the device key (Config Server path).

    Parameters
    ----------
    access_payload
        Plain Config Server access payload bytes, such as the output of
        :func:`godox_mesh_bt.config.build_config_app_key_add`.
    net_key
        16-byte Bluetooth Mesh Network Key.
    device_key
        16-byte Bluetooth Mesh Device Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.
    ttl
        Mesh Time To Live value.

    Returns
    -------
    bytes
        Complete Proxy SAR/type byte ``0x00`` followed by an obfuscated Network
        PDU whose upper transport layer is encrypted with the device key.

    Examples
    --------
    >>> net_key = bytes.fromhex("98b2e7ef8211c6deca2401adbe52e715")
    >>> device_key = bytes(range(16))
    >>> pdu = pack_proxy_device_key_pdu(b"abc", net_key, device_key, 0, 1, 1, 2)
    >>> pdu[0]
    0
    """
    nid, enc_key, priv_key = k2(net_key)

    encrypted_access = encrypt_device_key_pdu(
        payload=access_payload,
        device_key=device_key,
        iv_index=iv_index,
        seq=seq,
        src=src,
        dst=dst,
    )
    encrypted_net_header_and_data = encrypt_network_pdu(
        dst, encrypted_access, enc_key, iv_index, seq, src, ttl
    )

    ivi = iv_index & 1
    byte0 = (ivi << 7) | nid
    network_pdu = bytes([byte0]) + encrypted_net_header_and_data
    obf_pdu = obfuscate(network_pdu, priv_key, iv_index)

    return bytes([0x00]) + obf_pdu  # Proxy SAR=0, Type=0


def encode_vendor_opcode(opcode: int) -> bytes:
    """Encode a 24-bit vendor opcode in Telink little-endian order.

    Parameters
    ----------
    opcode
        Vendor opcode integer from 0 through ``0xFFFFFF``.

    Returns
    -------
    bytes
        Three opcode bytes in little-endian order.

    Examples
    --------
    >>> encode_vendor_opcode(135664).hex()
    'f01102'
    """

    # Telink uses raw little-endian bytes for the 3-octet opcode
    logger.debug("encoding vendor opcode 0x%06x", opcode)
    return opcode.to_bytes(3, "little")


def build_vendor_access_payload(opcode: int, godox_payload: bytes) -> bytes:
    """Build an Access Layer payload for a Godox vendor command.

    Parameters
    ----------
    opcode
        24-bit vendor opcode integer.
    godox_payload
        Exact Godox command bytes to append after the three opcode bytes.

    Returns
    -------
    bytes
        Three-byte vendor opcode followed by the original payload bytes.

    Examples
    --------
    >>> build_vendor_access_payload(135664, b"abc").hex()
    'f01102616263'
    """

    return encode_vendor_opcode(opcode) + godox_payload


def encrypt_device_key_pdu(
    payload: bytes,
    device_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
) -> bytes:
    """Encrypt an Upper Transport PDU using a device key.

    Parameters
    ----------
    payload
        Plain Config Server access payload bytes.
    device_key
        16-byte Bluetooth Mesh Device Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.

    Returns
    -------
    bytes
        One-byte ``AKF=0, AID=0`` header followed by ciphertext and a 4-byte
        MIC.

    Examples
    --------
    >>> encrypted = encrypt_device_key_pdu(b"abc", bytes(range(16)), 0, 1, 1, 2)
    >>> decrypt_device_key_pdu(encrypted, bytes(range(16)), 0, 1, 1, 2)
    b'abc'
    """
    nonce = (
        bytes([0x02, 0x00])
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv_index.to_bytes(4, "big")
    )
    header = bytes([0x00])  # AKF=0, AID=0
    return header + aes_ccm_encrypt(device_key, nonce, payload, 4)


def decrypt_device_key_pdu(
    encrypted: bytes,
    device_key: bytes,
    iv_index: int,
    seq: int,
    src: int,
    dst: int,
) -> bytes:
    """Decrypt an Upper Transport PDU using a device key.

    Parameters
    ----------
    encrypted
        One-byte ``AKF=0, AID=0`` header followed by ciphertext and 4-byte MIC.
    device_key
        16-byte Bluetooth Mesh Device Key.
    iv_index
        Bluetooth Mesh IV Index.
    seq
        Mesh sequence number.
    src
        Mesh source unicast address.
    dst
        Mesh destination address.

    Returns
    -------
    bytes
        Plain Config Server access payload bytes.

    Examples
    --------
    >>> encrypted = encrypt_device_key_pdu(b"abc", bytes(range(16)), 0, 1, 1, 2)
    >>> decrypt_device_key_pdu(encrypted, bytes(range(16)), 0, 1, 1, 2)
    b'abc'
    """
    nonce = (
        bytes([0x02, 0x00])
        + seq.to_bytes(3, "big")
        + src.to_bytes(2, "big")
        + dst.to_bytes(2, "big")
        + iv_index.to_bytes(4, "big")
    )
    return aes_ccm_decrypt(device_key, nonce, encrypted[1:], 4)


def generate_p256_keypair() -> tuple[EllipticCurvePrivateKey, bytes]:
    """Generate a P-256 keypair.

    Returns
    -------
    tuple[EllipticCurvePrivateKey, bytes]
        ``(private_key, public_key_bytes)`` where ``public_key_bytes`` is
        X (32 bytes big-endian) + Y (32 bytes big-endian) = 64 bytes total.
    """
    private_key = generate_private_key(SECP256R1())
    nums = private_key.public_key().public_numbers()
    pub_bytes = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    return private_key, pub_bytes


def ecdh_shared_secret(private_key: EllipticCurvePrivateKey, peer_pub_bytes: bytes) -> bytes:
    """Compute ECDH shared secret from our private key and peer's raw 64-byte public key.

    Parameters
    ----------
    private_key
        A ``cryptography`` EllipticCurvePrivateKey (P-256).
    peer_pub_bytes
        64-byte raw public key: X (32 bytes) + Y (32 bytes).

    Returns
    -------
    bytes
        32-byte shared secret.
    """
    x = int.from_bytes(peer_pub_bytes[:32], "big")
    y = int.from_bytes(peer_pub_bytes[32:], "big")
    peer_pub = EllipticCurvePublicNumbers(x, y, SECP256R1()).public_key()
    return private_key.exchange(ECDH(), peer_pub)
