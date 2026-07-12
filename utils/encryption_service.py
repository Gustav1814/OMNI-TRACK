"""
utils/services/encryption_service.py
Single Responsibility: AES-192-CBC encryption/decryption of sensitive strings.

Used to encrypt public RTSP URLs before returning them in API responses,
so credentials are never exposed in plaintext over the wire.
"""
import base64
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class EncryptionService:
    """
    AES-192-CBC encryption with PKCS7 padding.

    Key must be exactly 24 bytes (provided as 48-character hex string).
    Each encrypt() call generates a random 16-byte IV for semantic security.
    Output format: base64( IV || ciphertext )
    """

    _AES_BLOCK_SIZE = 128  # bits (AES block size is always 128)

    def __init__(self, hex_key: str) -> None:
        """
        Args:
            hex_key: 48-character hex string representing a 24-byte AES-192 key.

        Raises:
            ValueError: If the key is not exactly 48 hex characters (24 bytes).
        """
        try:
            key_bytes = bytes.fromhex(hex_key)
        except ValueError as e:
            raise ValueError("AIS_ENCRYPTION_KEY must be a valid hex string") from e

        if len(key_bytes) != 24:
            raise ValueError(
                f"AES-192 requires exactly 24 bytes (48 hex chars), "
                f"got {len(key_bytes)} bytes ({len(hex_key)} hex chars)"
            )
        self._key = key_bytes

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string using AES-192-CBC.

        Args:
            plaintext: The string to encrypt (e.g. an RTSP URL with credentials).

        Returns:
            Base64-encoded string of (IV + ciphertext).
        """
        iv = os.urandom(16)

        # PKCS7 padding to AES block size
        padder = padding.PKCS7(self._AES_BLOCK_SIZE).padder()
        padded_data = padder.update(plaintext.encode("utf-8")) + padder.finalize()

        # Encrypt
        cipher = Cipher(
            algorithms.AES(self._key),
            modes.CBC(iv),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # Concatenate IV + ciphertext and base64-encode
        return base64.urlsafe_b64encode(iv + ciphertext).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt a base64-encoded AES-192-CBC ciphertext.

        Args:
            encrypted: Base64 string previously produced by encrypt().

        Returns:
            The original plaintext string.

        Raises:
            ValueError: If decryption or unpadding fails (tampered/invalid data).
        """
        try:
            raw = base64.urlsafe_b64decode(encrypted)
        except Exception as e:
            raise ValueError("Invalid base64 encoding in encrypted payload") from e

        if len(raw) < 32:
            raise ValueError("Encrypted payload too short (need at least IV + 1 block)")

        iv = raw[:16]
        ciphertext = raw[16:]

        # Decrypt
        cipher = Cipher(
            algorithms.AES(self._key),
            modes.CBC(iv),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove PKCS7 padding
        unpadder = padding.PKCS7(self._AES_BLOCK_SIZE).unpadder()
        data = unpadder.update(padded_data) + unpadder.finalize()

        plaintext: str = data.decode("utf-8")
        return plaintext
