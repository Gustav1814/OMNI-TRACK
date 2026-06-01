"""
OmniTrack AI - authenticated audit metadata encryption.

New values use AES-256-GCM and are prefixed with ``gcm:``. The decryptor keeps
legacy CBC support so older development audit rows still remain readable.
"""

import base64
import json
from typing import Any, Dict

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from app.config import settings


def get_key() -> bytes:
    """Derive a stable 32-byte AES key from config."""
    key = settings.AES_SECRET_KEY.encode("utf-8")
    return key[:32].ljust(32, b"\0")


def encrypt_data(data: Dict[str, Any]) -> str:
    """
    AES-256-GCM encrypt a dictionary.

    Returns: gcm:base64(nonce + tag + ciphertext).
    """
    key = get_key()
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return "gcm:" + base64.b64encode(nonce + tag + ciphertext).decode("utf-8")


def decrypt_data(encrypted_str: str) -> Dict[str, Any]:
    """Decrypt audit metadata. Supports AES-GCM plus legacy CBC payloads."""
    key = get_key()
    if encrypted_str.startswith("gcm:"):
        raw = base64.b64decode(encrypted_str[4:])
        nonce = raw[:12]
        tag = raw[12:28]
        ciphertext = raw[28:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return json.loads(plaintext.decode("utf-8"))

    raw = base64.b64decode(encrypted_str)
    iv = raw[:16]
    ciphertext = raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode("utf-8"))
