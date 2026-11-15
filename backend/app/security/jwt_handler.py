"""
OmniTrack AI — JWT + password handling.

Token model
-----------
* access token  : 15 min (ACCESS_TOKEN_EXPIRE_MINUTES), claim ``type="access"``,
  carries ``ver`` = the user's ``token_version`` so role changes, deactivation
  and "log out everywhere" invalidate outstanding access tokens immediately.
* refresh token : 7 days (REFRESH_TOKEN_EXPIRE_DAYS), claim ``type="refresh"``,
  carries a ``jti`` recorded server-side in ``refresh_tokens``. Every refresh
  rotates it: the presented jti is revoked and a new one issued in the same
  family. Presenting an already-rotated jti is treated as theft and revokes
  the whole family (see routers/auth.py).

Every token has ``iss``/``aud`` so a token minted for another service sharing
the secret is rejected, and ``iat``/``exp``/``jti``.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings

_BCRYPT_MAX_BYTES = 72
TOKEN_ISSUER = "omnitrack"
TOKEN_AUDIENCE = "omnitrack-api"
ACCESS = "access"
REFRESH = "refresh"


def _password_digest(password: str) -> bytes:
    """
    Bcrypt accepts at most 72 bytes. Pre-hash long passwords with SHA-256
    so verification stays deterministic without silent truncation.
    """
    raw = password.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    return hashlib.sha256(raw).digest()


def hash_password(password: str) -> str:
    digest = _password_digest(password)
    return bcrypt.hashpw(digest, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_password_digest(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A valid bcrypt hash used to keep login timing identical for unknown users.
_DUMMY_HASH = bcrypt.hashpw(b"omnitrack-timing-equaliser", bcrypt.gensalt(rounds=12)).decode("utf-8")


def burn_password_check(plain: str) -> None:
    """Spend the same CPU as a real check (prevents username enumeration by timing)."""
    verify_password(plain, _DUMMY_HASH)


def _encode(claims: Dict[str, Any]) -> str:
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    user_id: int,
    role: str,
    token_version: int = 0,
    expires_delta: Optional[timedelta] = None,
) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return _encode({
        "sub": str(user_id),
        "role": role,
        "ver": int(token_version or 0),
        "type": ACCESS,
        "jti": uuid.uuid4().hex,
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": exp,
    })


def create_refresh_token(user_id: int, jti: str, family_id: str, expires_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    return _encode({
        "sub": str(user_id),
        "type": REFRESH,
        "jti": jti,
        "fam": family_id,
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": expires_at,
    })


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def decode_token(token: str, expected_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Validate signature, exp, iss, aud and (optionally) the ``type`` claim."""
    if not token or not settings.JWT_SECRET_KEY:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
    except JWTError:
        return None
    if expected_type is not None and payload.get("type") != expected_type:
        return None
    if payload.get("type") not in (ACCESS, REFRESH):
        return None
    return payload
