"""
OmniTrack AI — AES-256-CBC Encryption
PyCryptodome-based metadata encryption for audit logs
"""

import base64
import json
from typing import Any, Dict
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
from app.config import settings


def get_key() -> bytes:
    """Derive 32-byte AES key from config."""
    key = settings.AES_SECRET_KEY.encode("utf-8")
    # Ensure exactly 32 bytes
    return key[:32].ljust(32, b'\0')


def encrypt_data(data: Dict[str, Any]) -> str:
    """
    AES-256-CBC encrypt a dictionary.
    Returns base64-encoded string: IV (16 bytes) + ciphertext.
    """
    key = get_key()
    iv = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = json.dumps(data, default=str).encode("utf-8")
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def decrypt_data(encrypted_str: str) -> Dict[str, Any]:
    """
    Decrypt an AES-256-CBC encrypted string back to a dictionary.
    """
    key = get_key()
    raw = base64.b64decode(encrypted_str)
    iv = raw[:16]
    ciphertext = raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode("utf-8"))
