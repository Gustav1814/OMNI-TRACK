"""
OmniTrack AI — encryption at rest (AES-256-GCM).

Format v2 (current):  ``v2:`` + base64( nonce[12] | ciphertext | tag[16] )
  * fresh 96-bit random nonce for every record (never reused under one key);
  * optional associated data (AAD) binds a ciphertext to its purpose/location so
    a blob cannot be swapped between records undetected;
  * key = AES_SECRET_KEY decoded as 64 hex chars or base64 of 32 bytes, else
    HKDF-SHA256 of the passphrase (>= 32 bytes enforced at startup).

Legacy formats (read-only, for rows written before 004): ``gcm:`` (GCM with the
raw key truncated/padded to 32 bytes) and unprefixed CBC.

Used for: audit metadata, face crops / snapshots (``write_encrypted_image`` /
``read_encrypted_image``). Re-ID embeddings are NOT encrypted because pgvector's
HNSW index must read the raw vectors; see docs/SECURITY.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import unpad

from app.config import settings

V2_PREFIX = "v2:"
BLOB_MAGIC = b"OTENC2\x00"
NONCE_BYTES = 12
TAG_BYTES = 16


class DecryptionError(ValueError):
    """Ciphertext is corrupt, tampered, or was encrypted under another key."""


@lru_cache(maxsize=8)
def _derive_key(secret: str) -> bytes:
    raw = secret.strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) == 32:
            return decoded
    except (binascii.Error, ValueError):
        pass
    return HKDF(raw.encode("utf-8"), 32, salt=b"omnitrack-aes-v2", hashmod=SHA256, context=b"records-at-rest")


def get_key() -> bytes:
    if not settings.AES_SECRET_KEY:
        raise RuntimeError("AES_SECRET_KEY is not configured")
    return _derive_key(settings.AES_SECRET_KEY)


def _legacy_key() -> bytes:
    return settings.AES_SECRET_KEY.encode("utf-8")[:32].ljust(32, b"\0")


# ─────────────────────────── bytes API ───────────────────────────

def encrypt_bytes(plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
    """Return nonce | ciphertext | tag."""
    nonce = get_random_bytes(NONCE_BYTES)
    cipher = AES.new(get_key(), AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + ciphertext + tag


def decrypt_bytes(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise DecryptionError("ciphertext too short")
    nonce, body, tag = blob[:NONCE_BYTES], blob[NONCE_BYTES:-TAG_BYTES], blob[-TAG_BYTES:]
    cipher = AES.new(get_key(), AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    try:
        return cipher.decrypt_and_verify(body, tag)
    except ValueError as e:
        raise DecryptionError("authentication failed") from e


# ─────────────────────────── dict API (audit metadata) ───────────────────────────

def encrypt_data(data: Dict[str, Any], aad: Optional[bytes] = None) -> str:
    plaintext = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return V2_PREFIX + base64.b64encode(encrypt_bytes(plaintext, aad)).decode("ascii")


def decrypt_data(encrypted_str: str, aad: Optional[bytes] = None) -> Dict[str, Any]:
    """Decrypt a stored record. Raises DecryptionError if it cannot be authenticated."""
    try:
        if encrypted_str.startswith(V2_PREFIX):
            plaintext = decrypt_bytes(base64.b64decode(encrypted_str[len(V2_PREFIX):]), aad)
        elif encrypted_str.startswith("gcm:"):
            raw = base64.b64decode(encrypted_str[4:])
            cipher = AES.new(_legacy_key(), AES.MODE_GCM, nonce=raw[:12])
            plaintext = cipher.decrypt_and_verify(raw[28:], raw[12:28])
        else:
            raw = base64.b64decode(encrypted_str)
            cipher = AES.new(_legacy_key(), AES.MODE_CBC, raw[:16])
            plaintext = unpad(cipher.decrypt(raw[16:]), AES.block_size)
        return json.loads(plaintext.decode("utf-8"))
    except DecryptionError:
        raise
    except (ValueError, KeyError, binascii.Error) as e:
        raise DecryptionError(str(e)) from e


def try_decrypt_data(encrypted_str: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best effort for display: None when absent or not decryptable with the current key."""
    if not encrypted_str:
        return None
    try:
        return decrypt_data(encrypted_str)
    except DecryptionError:
        return None


# ─────────────────────────── files (face crops / snapshots) ───────────────────────────

def _file_aad(path: Path) -> bytes:
    # Bind the ciphertext to its file name so two encrypted crops cannot be swapped.
    return b"omnitrack-file|" + Path(path).name.encode("utf-8")


def write_encrypted_file(path: Path, data: bytes) -> Path:
    """Atomically write ``data`` encrypted (MAGIC | nonce | ct | tag), mode 0600."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(BLOB_MAGIC + encrypt_bytes(data, _file_aad(path)))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def read_encrypted_file(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    if not raw.startswith(BLOB_MAGIC):
        raise DecryptionError("not an OmniTrack encrypted file")
    return decrypt_bytes(raw[len(BLOB_MAGIC):], _file_aad(path))


def write_encrypted_image(path: Path, image_bgr, quality: int = 90) -> Path:
    """JPEG-encode an OpenCV BGR image and store it encrypted (use a ``.jpg.enc`` name)."""
    import cv2  # noqa: PLC0415 - keep module importable without OpenCV

    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return write_encrypted_file(path, buf.tobytes())


def read_encrypted_image(path: Path) -> bytes:
    """Return the decrypted JPEG bytes (serve with media_type image/jpeg)."""
    return read_encrypted_file(path)
