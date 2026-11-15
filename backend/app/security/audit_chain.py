"""
OmniTrack AI — tamper-evident audit chain (HMAC-SHA256).

Each row stores ``current_hash = HMAC-SHA256(AUDIT_HMAC_KEY, canonical(payload) | previous_hash)``.
Without the server key nobody can recompute a valid hash, so a row that is
edited, deleted or moved breaks verification:

* mutate  -> the row's recomputed HMAC no longer matches ``current_hash``
* delete  -> the next row's ``previous_hash`` no longer points at its predecessor
* reorder -> ``previous_hash`` links no longer follow ``id`` order
* tail truncation (deleting the newest rows) -> caught by the signed anchor
  file which records the newest (id, hash) ever committed.

Rows written before migration 004 used an unkeyed SHA-256 (``hash_alg='sha256'``).
They are verified with the legacy formula and reported separately because an
unkeyed chain is only tamper-*evident* against careless edits, not against an
attacker who can recompute SHA-256.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence


try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # Windows (demo PC)
    _fcntl = None
    import msvcrt as _msvcrt


def _lock_file(fh) -> None:
    """Exclusive, blocking, cross-process lock on an open file (POSIX flock / Windows msvcrt)."""
    if _fcntl is not None:
        _fcntl.flock(fh, _fcntl.LOCK_EX)
        return
    fh.seek(0)
    while True:
        try:
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_LOCK, 1)
            return
        except OSError:  # LK_LOCK gives up after ~10 s; keep waiting
            continue


def _unlock_file(fh) -> None:
    if _fcntl is not None:
        _fcntl.flock(fh, _fcntl.LOCK_UN)
        return
    fh.seek(0)
    _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)

HMAC_ALG = "hmac-sha256"
LEGACY_ALG = "sha256"
GENESIS = "GENESIS"


@dataclass
class ChainEntry:
    """Plain view of one audit row (decoupled from SQLAlchemy for pure verification)."""

    id: int
    event_type: str
    user_id: Optional[int]
    description: Optional[str]
    timestamp: Any
    encrypted_metadata: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    current_hash: str
    previous_hash: Optional[str]
    hash_alg: str = HMAC_ALG

    @classmethod
    def from_row(cls, row: Any) -> "ChainEntry":
        return cls(
            id=row.id,
            event_type=row.event_type,
            user_id=row.user_id,
            description=row.description,
            timestamp=row.timestamp,
            encrypted_metadata=row.encrypted_metadata,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
            current_hash=row.current_hash,
            previous_hash=row.previous_hash,
            hash_alg=getattr(row, "hash_alg", None) or LEGACY_ALG,
        )


def _iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def canonical_payload(
    event_type: str,
    user_id: Optional[int],
    description: Optional[str],
    timestamp: Any,
    encrypted_metadata: Optional[str],
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> bytes:
    payload = {
        "v": 2,
        "event_type": event_type,
        "user_id": user_id,
        "description": description,
        "timestamp": _iso(timestamp),
        "encrypted_metadata": encrypted_metadata,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def audit_key_bytes(key: Optional[str] = None) -> bytes:
    if key is None:
        from app.config import settings  # noqa: PLC0415 - avoid import cycle at module load

        key = settings.AUDIT_HMAC_KEY
    if not key:
        raise RuntimeError("AUDIT_HMAC_KEY is not configured")
    # Domain-separate so the same bytes reused elsewhere cannot forge audit rows.
    return hashlib.sha256(b"omnitrack-audit-v2|" + key.encode("utf-8")).digest()


def compute_hmac(payload: bytes, previous_hash: Optional[str], key: bytes) -> str:
    msg = payload + b"|" + (previous_hash or GENESIS).encode("ascii")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def hash_entry(entry: ChainEntry, key: bytes) -> str:
    return compute_hmac(
        canonical_payload(
            entry.event_type, entry.user_id, entry.description, entry.timestamp,
            entry.encrypted_metadata, entry.ip_address, entry.user_agent,
        ),
        entry.previous_hash,
        key,
    )


def _legacy_hashes(entry: ChainEntry) -> Iterable[str]:
    """Both payload shapes the pre-004 code ever wrote (unkeyed SHA-256)."""
    full = {
        "event_type": entry.event_type,
        "user_id": entry.user_id,
        "description": entry.description,
        "timestamp": _iso(entry.timestamp),
        "encrypted_metadata": entry.encrypted_metadata,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
    }
    short = {k: full[k] for k in ("event_type", "user_id", "description", "timestamp")}
    for payload in (full, short):
        data = json.dumps(payload, sort_keys=True, default=str)
        yield hashlib.sha256(f"{data}|{entry.previous_hash or GENESIS}".encode("utf-8")).hexdigest()


# ─────────────────────────────── anchor ───────────────────────────────

def _anchor_sig(body: Dict[str, Any], key: bytes) -> str:
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, b"anchor|" + msg, hashlib.sha256).hexdigest()


def read_anchor(path: Path, key: bytes) -> Dict[str, Any]:
    """Return {'status': 'ok'|'missing'|'invalid', ...}. Never raises."""
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError):
        return {"status": "invalid", "reason": "unreadable"}
    body = _anchor_body(raw)
    if not isinstance(raw.get("sig"), str) or not hmac.compare_digest(raw["sig"], _anchor_sig(body, key)):
        return {"status": "invalid", "reason": "bad signature"}
    return {"status": "ok", **body}


_BASE_FIELDS = ("base_id", "base_prev_hash", "archived_before", "based_at")


def _anchor_body(raw: Dict[str, Any]) -> Dict[str, Any]:
    body = {k: raw.get(k) for k in ("last_id", "last_hash", "updated_at")}
    # Optional signed chain base (see set_anchor_base); only present once rebased,
    # so anchors written before the field existed keep verifying.
    if raw.get("base_id") is not None:
        body.update({k: raw.get(k) for k in _BASE_FIELDS})
    return body


def _write_anchor(path: Path, body: Dict[str, Any], key: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({**body, "sig": _anchor_sig(body, key)}, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def advance_anchor(path: Path, last_id: int, last_hash: str, key: bytes) -> bool:
    """Monotonically move the anchor forward (never backwards). Process-safe via flock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock:
        _lock_file(lock)
        try:
            current = read_anchor(path, key)
            if current.get("status") == "ok" and int(current.get("last_id") or 0) >= last_id:
                return False
            body = {
                "last_id": int(last_id),
                "last_hash": last_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if current.get("status") == "ok" and current.get("base_id") is not None:
                body.update({k: current.get(k) for k in _BASE_FIELDS})
            _write_anchor(path, body, key)
            return True
        finally:
            _unlock_file(lock)


def legacy_prefix_base(entries: Sequence["ChainEntry"]) -> Optional[Dict[str, Any]]:
    """Where a keyed chain may be based: the first HMAC row, if every row before it is legacy.

    Legacy rows were hashed WITHOUT a key, so anyone with DB write access could
    always rewrite them; excluding them loses no tamper evidence. Keyed rows can
    never be excluded this way (the base must be the *first* HMAC row).
    """
    for idx, e in enumerate(entries):
        if e.hash_alg == HMAC_ALG:
            if idx == 0 or any(x.hash_alg != LEGACY_ALG for x in entries[:idx]):
                return None
            return {"base_id": e.id, "base_prev_hash": e.previous_hash, "archived_before": idx}
    return None


def set_anchor_base(path: Path, base: Dict[str, Any], last_id: int, last_hash: str, key: bytes) -> Dict[str, Any]:
    """Sign a chain base into the anchor (rows with id < base_id are excluded from verification)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock:
        _lock_file(lock)
        try:
            current = read_anchor(path, key)
            if current.get("status") == "invalid":
                raise RuntimeError(f"refusing to rebase over an invalid anchor ({current.get('reason')})")
            if current.get("status") == "ok" and int(current.get("last_id") or 0) > last_id:
                raise RuntimeError("anchor is ahead of the database (rows truncated?) — refusing to rebase")
            body = {
                "last_id": int(last_id),
                "last_hash": last_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "base_id": int(base["base_id"]),
                "base_prev_hash": base["base_prev_hash"],
                "archived_before": int(base.get("archived_before") or 0),
                "based_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_anchor(path, body, key)
            return body
        finally:
            _unlock_file(lock)


# ───────────────────────────── verification ─────────────────────────────

def verify_entries(
    entries: Sequence[ChainEntry],
    key: bytes,
    anchor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify a full chain ordered by id ascending.

    Returns a dict with ``valid``, ``broken_at`` (row id or None), ``reason``,
    ``total``, ``hmac_entries``, ``legacy_entries`` and ``anchor`` status.
    """
    total = len(entries)
    result: Dict[str, Any] = {
        "valid": True,
        "broken_at": None,
        "reason": None,
        "total": total,
        "hmac_entries": 0,
        "legacy_entries": 0,
        "first_hmac_id": None,
        "last_id": entries[-1].id if entries else None,
        "anchor": (anchor or {}).get("status", "not_checked"),
    }

    def fail(entry_id: Optional[int], reason: str) -> Dict[str, Any]:
        result.update(valid=False, broken_at=entry_id, reason=reason)
        return result

    prev_hash: Optional[str] = None
    prev_id: Optional[int] = None
    seen_hmac = False
    base_id = None
    if anchor is not None and anchor.get("status") == "ok" and anchor.get("base_id") is not None:
        # Signed base: rows before it are pre-HMAC legacy history, excluded on purpose.
        base_id = int(anchor["base_id"])
        excluded = [e for e in entries if e.id < base_id]
        entries = [e for e in entries if e.id >= base_id]
        result["excluded_legacy"] = len(excluded)
        result["base_id"] = base_id
        if any(e.hash_alg != LEGACY_ALG for e in excluded):
            return fail(next(e.id for e in excluded if e.hash_alg != LEGACY_ALG),
                        "keyed row found below the signed chain base")
        if not entries or entries[0].id != base_id:
            return fail(base_id, "chain base row missing (oldest keyed rows deleted)")
        prev_hash = anchor.get("base_prev_hash")
    for entry in entries:
        if prev_id is not None and entry.id <= prev_id:
            return fail(entry.id, "ids not strictly increasing (rows reordered)")
        if entry.previous_hash != prev_hash:
            return fail(entry.id, "previous_hash does not link to the preceding row (row deleted, inserted or reordered)")
        if entry.hash_alg == HMAC_ALG:
            if not seen_hmac:
                seen_hmac = True
                result["first_hmac_id"] = entry.id
            if not hmac.compare_digest(hash_entry(entry, key), entry.current_hash or ""):
                return fail(entry.id, "HMAC mismatch (row content modified)")
            result["hmac_entries"] += 1
        elif entry.hash_alg == LEGACY_ALG:
            if seen_hmac or base_id is not None:
                return fail(entry.id, "legacy unkeyed row after keyed rows (downgrade attempt)")
            if entry.current_hash not in set(_legacy_hashes(entry)):
                return fail(entry.id, "legacy SHA-256 mismatch (row content modified)")
            result["legacy_entries"] += 1
        else:
            return fail(entry.id, f"unknown hash_alg {entry.hash_alg!r}")
        prev_hash = entry.current_hash
        prev_id = entry.id

    if anchor is not None:
        status = anchor.get("status")
        if status == "invalid":
            return fail(None, f"anchor file invalid ({anchor.get('reason')})")
        if status == "ok":
            anchor_id = int(anchor.get("last_id") or 0)
            match = next((e for e in reversed(entries) if e.id == anchor_id), None)
            if match is None:
                return fail(anchor_id, "anchored row missing (newest rows deleted / truncated)")
            if match.current_hash != anchor.get("last_hash"):
                return fail(anchor_id, "anchored row hash differs from anchor")
    return result
