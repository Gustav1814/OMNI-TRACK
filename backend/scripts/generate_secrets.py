#!/usr/bin/env python
"""
Generate strong secrets into backend/.env (gitignored) — values are never printed.

    python scripts/generate_secrets.py                       # fill missing/weak app secrets
    python scripts/generate_secrets.py --rotate-db-password  # also rotate the Postgres password
    python scripts/generate_secrets.py --rotate-jwt          # new JWT + admin-session keys (logs everyone out)

Behaviour
* JWT_SECRET_KEY, ADMIN_SESSION_SECRET, AES_SECRET_KEY and AUDIT_HMAC_KEY are
  generated only when missing, a known placeholder or < 32 bytes — an existing
  strong value is kept. AES_SECRET_KEY / AUDIT_HMAC_KEY are never rotated
  silently: a new AES key makes stored ciphertext unreadable and a new audit key
  makes every existing chain entry fail verification (``--force-aes`` /
  ``--force-audit`` exist for a deliberate reset).
* ``--rotate-db-password`` runs ``ALTER USER`` inside the Postgres container
  (``docker exec``; local socket auth, the SQL goes over stdin so the password
  never appears in a process list) and then rewrites DATABASE_URL in .env.
  Already-running backends keep their open pool connections but must be
  restarted to reconnect.
* .env is written atomically with mode 0600; other lines are preserved.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.security.secrets_check import _problem, database_password  # noqa: E402

ENV_PATH = BACKEND_ROOT / ".env"
EXAMPLE_PATH = BACKEND_ROOT / ".env.example"
_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.is_file():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {}
    for ln in lines:
        m = _LINE.match(ln)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return lines, values


def _set(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for ln in lines:
        m = _LINE.match(ln)
        if m and m.group(1) == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    return out


def _write_env(lines: list[str]) -> None:
    tmp = ENV_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, ENV_PATH)
    os.chmod(ENV_PATH, 0o600)


def _is_gitignored(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=BACKEND_ROOT,
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _with_password(url: str, password: str) -> str:
    parts = urlsplit(url)
    user = parts.username or "omnitrack"
    host = parts.hostname or "localhost"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _rotate_db_password(url: str, container: str, context: str | None) -> str:
    user = urlsplit(url).username or "omnitrack"
    db = (urlsplit(url).path or "/omnitrack_db").lstrip("/") or "omnitrack_db"
    new_pw = secrets.token_urlsafe(32)  # 43 chars, URL-safe, no quoting issues in SQL
    sql = f"ALTER USER \"{user}\" WITH PASSWORD '{new_pw}';\n"
    cmd = ["docker"] + (["--context", context] if context else []) + [
        "exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", user, "-d", db,
    ]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"ALTER USER failed (exit {r.returncode}): {r.stderr.strip()[:300]}")
    return new_pw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rotate-db-password", action="store_true")
    ap.add_argument("--db-container", default="omnitrack-db")
    ap.add_argument("--docker-context", default=os.environ.get("DOCKER_CONTEXT") or None)
    ap.add_argument("--rotate-jwt", action="store_true", help="new JWT + admin session keys")
    ap.add_argument("--force-aes", action="store_true", help="DANGER: stored ciphertext becomes unreadable")
    ap.add_argument("--force-audit", action="store_true", help="DANGER: existing audit chain stops verifying")
    args = ap.parse_args()

    if not _is_gitignored(ENV_PATH):
        print(f"Refusing: {ENV_PATH} is not gitignored — secrets could be committed.", file=sys.stderr)
        return 2

    lines, env = _read_env(ENV_PATH)
    if not lines and EXAMPLE_PATH.is_file():
        lines, _ = _read_env(EXAMPLE_PATH)  # start from the documented template
    changed: list[str] = []

    def ensure(key: str, gen, force: bool = False) -> None:
        nonlocal lines
        if force or _problem(key, env.get(key)) is not None:
            lines = _set(lines, key, gen())
            changed.append(key)

    ensure("JWT_SECRET_KEY", lambda: secrets.token_urlsafe(48), args.rotate_jwt)
    ensure("ADMIN_SESSION_SECRET", lambda: secrets.token_urlsafe(48), args.rotate_jwt)
    ensure("AES_SECRET_KEY", lambda: secrets.token_hex(32), args.force_aes)  # 32 random bytes as hex
    ensure("AUDIT_HMAC_KEY", lambda: secrets.token_urlsafe(48), args.force_audit)

    # Keep any DATABASE_URL already in .env; otherwise the template / settings default.
    db_url = env.get("DATABASE_URL")
    if not db_url:
        _, ex = _read_env(EXAMPLE_PATH)
        db_url = ex.get("DATABASE_URL") or "postgresql+asyncpg://omnitrack:@localhost:5432/omnitrack_db"
    db_weak = _problem("DATABASE_URL password", database_password(db_url)) is not None
    if args.rotate_db_password:
        new_pw = _rotate_db_password(db_url, args.db_container, args.docker_context)
        lines = _set(lines, "DATABASE_URL", _with_password(db_url, new_pw))
        changed.append("DATABASE_URL (Postgres password rotated)")
    elif "DATABASE_URL" not in env:
        lines = _set(lines, "DATABASE_URL", db_url)

    _write_env(lines)
    print(f"Wrote {ENV_PATH} (mode 600). Updated: {', '.join(changed) or 'nothing — all secrets already strong'}")
    if db_weak and not args.rotate_db_password:
        print("WARNING: the database password is a default/weak value; the backend will refuse to start.\n"
              "         Run again with --rotate-db-password (Postgres container must be running).")
    if "AES_SECRET_KEY" in changed and env.get("AES_SECRET_KEY"):
        print("NOTE: AES key changed — data encrypted with the previous key can no longer be decrypted.")
    if "AUDIT_HMAC_KEY" in changed and env.get("AUDIT_HMAC_KEY"):
        print("NOTE: audit key changed — existing audit entries will fail verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
