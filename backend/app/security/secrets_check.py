"""
OmniTrack AI — startup secret strength check.

The app refuses to start when a secret is empty, a known placeholder that was
ever committed to the repository, or shorter than 32 bytes. Secrets come from
backend/.env only (generate them with ``python scripts/generate_secrets.py``).

The check runs in the FastAPI lifespan (not at import) so that pure unit tests
can import ``app.config`` without a populated .env.
"""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import unquote, urlparse

MIN_SECRET_BYTES = 32

# Every placeholder that has appeared in this repository's history or docs.
# Matching is case-insensitive on the stripped value.
KNOWN_DEFAULTS = {
    "",
    "change-this-in-production",
    "your-super-secret-jwt-key-change-in-production",
    "0123456789abcdef0123456789abcdef",
    "0123456789abcdef0123456789abcdef0",
    "omnitrack_secret",
    "secret",
    "changeme",
    "password",
}

# Substrings that mark an obviously placeholder value even when long enough.
_PLACEHOLDER_MARKERS = ("change-this", "change-in-production", "changeme", "your-super-secret", "replace-me")


class WeakSecretError(RuntimeError):
    """Raised when one or more secrets are missing, default or too short."""


def _problem(name: str, value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if raw.lower() in KNOWN_DEFAULTS:
        return f"{name} is empty or a known default"
    lowered = raw.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return f"{name} looks like a placeholder"
    if len(raw.encode("utf-8")) < MIN_SECRET_BYTES:
        return f"{name} is shorter than {MIN_SECRET_BYTES} bytes"
    if len(set(raw)) < 8:
        return f"{name} has too little variety (repeated characters)"
    return None


def database_password(database_url: str) -> Optional[str]:
    try:
        parsed = urlparse(database_url.replace("+asyncpg", "").replace("+psycopg2", ""))
    except ValueError:
        return None
    if parsed.scheme.startswith("sqlite"):
        return None  # file DB: no password to check
    return unquote(parsed.password) if parsed.password is not None else ""


def collect_secret_problems(settings) -> List[str]:
    """Return a list of human-readable problems (never containing secret values)."""
    problems: List[str] = []
    for name in ("JWT_SECRET_KEY", "AES_SECRET_KEY", "AUDIT_HMAC_KEY"):
        issue = _problem(name, getattr(settings, name, ""))
        if issue:
            problems.append(issue)
    db_password = database_password(getattr(settings, "DATABASE_URL", ""))
    if db_password is not None:
        issue = _problem("DATABASE_URL password", db_password)
        if issue:
            problems.append(issue)
    jwt_key = (getattr(settings, "JWT_SECRET_KEY", "") or "").strip()
    for other in ("AES_SECRET_KEY", "AUDIT_HMAC_KEY"):
        if jwt_key and jwt_key == (getattr(settings, other, "") or "").strip():
            problems.append(f"JWT_SECRET_KEY must differ from {other}")
    return problems


def check_secrets_or_raise(settings) -> None:
    if not getattr(settings, "ENFORCE_SECRET_STRENGTH", True):
        return
    problems = collect_secret_problems(settings)
    if problems:
        raise WeakSecretError(
            "Refusing to start with weak secrets: "
            + "; ".join(problems)
            + ". Run `python scripts/generate_secrets.py` (writes backend/.env) and restart."
        )
