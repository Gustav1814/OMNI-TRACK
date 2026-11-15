"""Tamper-evidence of the HMAC-SHA256 audit chain (pure; no database).

A 1,000-entry chain is built exactly as AuditService.log_event does, then every
possible single mutation / deletion / reorder is applied and verified:
detection must be 100%.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.security.audit_chain import (
    HMAC_ALG,
    LEGACY_ALG,
    ChainEntry,
    advance_anchor,
    audit_key_bytes,
    hash_entry,
    legacy_prefix_base,
    read_anchor,
    set_anchor_base,
    verify_entries,
)

N = 1000
KEY = audit_key_bytes("test-audit-key-0123456789-abcdefghijklmnop")
WRONG_KEY = audit_key_bytes("attacker-guess-key-0123456789-abcdefghijkl")


def build_chain(n: int = N, key: bytes = KEY) -> list[ChainEntry]:
    rng = random.Random(1234)
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    out: list[ChainEntry] = []
    prev = None
    for i in range(1, n + 1):
        e = ChainEntry(
            id=i, event_type=rng.choice(["LOGIN", "LOGIN_FAILED", "CAMERA_UPDATE", "REFRESH"]),
            user_id=rng.choice([None, 1, 2, 3]), description=f"event {i}",
            timestamp=t0 + timedelta(seconds=i), encrypted_metadata=rng.choice([None, f"ct{i:04d}"]),
            ip_address=f"10.0.0.{i % 250}", user_agent="pytest", current_hash="",
            previous_hash=prev, hash_alg=HMAC_ALG,
        )
        e.current_hash = hash_entry(e, key)
        prev = e.current_hash
        out.append(e)
    return out


@pytest.fixture(scope="module")
def chain() -> list[ChainEntry]:
    return build_chain()


def test_intact_chain_verifies(chain):
    r = verify_entries(chain, KEY)
    assert r["valid"] and r["total"] == N and r["hmac_entries"] == N


def _mutations(e: ChainEntry):
    yield dataclasses.replace(e, description=(e.description or "") + "x")
    yield dataclasses.replace(e, event_type="ADMIN_GRANT")
    yield dataclasses.replace(e, user_id=(e.user_id or 0) + 1)
    yield dataclasses.replace(e, ip_address="203.0.113.9")
    yield dataclasses.replace(e, timestamp=e.timestamp + timedelta(microseconds=1))
    yield dataclasses.replace(e, encrypted_metadata=(e.encrypted_metadata or "") + "A")


def test_every_mutation_detected(chain):
    trials = detected = 0
    for i in range(N):
        for m in _mutations(chain[i]):
            tampered = chain[:i] + [m] + chain[i + 1:]
            r = verify_entries(tampered, KEY)
            trials += 1
            detected += (not r["valid"]) and r["broken_at"] == chain[i].id
    assert detected == trials == N * 6


def test_mutation_with_recomputed_hash_without_key_detected(chain):
    """Attacker edits a row and re-chains everything after it with a guessed key."""
    rng = random.Random(7)
    for i in rng.sample(range(N), 50):
        forged = [dataclasses.replace(e) for e in chain]
        forged[i].description = "forged"
        for j in range(i, N):
            forged[j].previous_hash = forged[j - 1].current_hash if j else None
            forged[j].current_hash = hash_entry(forged[j], WRONG_KEY)
        r = verify_entries(forged, KEY)
        assert not r["valid"] and r["broken_at"] == forged[i].id


def test_every_deletion_detected(chain):
    anchor_path_status = {"status": "ok", "last_id": chain[-1].id, "last_hash": chain[-1].current_hash}
    for i in range(N):
        tampered = chain[:i] + chain[i + 1:]
        r = verify_entries(tampered, KEY, anchor=anchor_path_status)
        assert not r["valid"], f"deletion of row {i + 1} not detected"


def test_tail_truncation_needs_anchor(chain):
    truncated = chain[:-5]
    assert verify_entries(truncated, KEY)["valid"]  # a bare hash chain cannot see this…
    anchor = {"status": "ok", "last_id": chain[-1].id, "last_hash": chain[-1].current_hash}
    r = verify_entries(truncated, KEY, anchor=anchor)  # …the signed anchor does
    assert not r["valid"] and "truncated" in r["reason"]


def test_every_adjacent_reorder_detected(chain):
    for i in range(N - 1):
        # (a) rows physically swapped (ids out of order)
        swapped = chain[:i] + [chain[i + 1], chain[i]] + chain[i + 2:]
        assert not verify_entries(swapped, KEY)["valid"]
        # (b) contents swapped but ids rewritten to stay increasing
        a = dataclasses.replace(chain[i + 1], id=chain[i].id)
        b = dataclasses.replace(chain[i], id=chain[i + 1].id)
        relabeled = chain[:i] + [a, b] + chain[i + 2:]
        assert not verify_entries(relabeled, KEY)["valid"]


def test_random_reorders_detected(chain):
    rng = random.Random(99)
    for _ in range(200):
        i, j = sorted(rng.sample(range(N), 2))
        perm = list(chain)
        perm[i], perm[j] = perm[j], perm[i]
        assert not verify_entries(perm, KEY)["valid"]


def test_wrong_key_rejects_whole_chain(chain):
    r = verify_entries(chain, WRONG_KEY)
    assert not r["valid"] and r["broken_at"] == 1


def test_anchor_signed_and_monotonic(tmp_path, chain):
    p = tmp_path / "anchor.json"
    assert read_anchor(p, KEY)["status"] == "missing"
    assert advance_anchor(p, chain[-1].id, chain[-1].current_hash, KEY)
    assert not advance_anchor(p, 10, chain[9].current_hash, KEY)  # never moves backwards
    a = read_anchor(p, KEY)
    assert a["status"] == "ok" and a["last_id"] == N
    assert read_anchor(p, WRONG_KEY)["status"] == "invalid"
    p.write_text(p.read_text().replace(f'"last_id": {N}', '"last_id": 5'))
    assert read_anchor(p, KEY)["status"] == "invalid"  # editing the anchor breaks its signature


# ───────────── signed base: excluding the unkeyed pre-HMAC prefix ─────────────

def _with_broken_legacy_prefix(n_legacy: int = 7, n_keyed: int = 50) -> list[ChainEntry]:
    """Legacy rows whose stored hashes no longer match (e.g. user_id nulled), then keyed rows."""
    out: list[ChainEntry] = []
    prev = None
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(1, n_legacy + 1):
        e = ChainEntry(id=i, event_type="LOGIN", user_id=None, description=f"old {i}",
                       timestamp=t0 + timedelta(seconds=i), encrypted_metadata=None, ip_address=None,
                       user_agent=None, current_hash=f"{i:064x}", previous_hash=prev, hash_alg=LEGACY_ALG)
        prev = e.current_hash
        out.append(e)
    for i in range(n_legacy + 1, n_legacy + n_keyed + 1):
        e = ChainEntry(id=i, event_type="LOGIN", user_id=1, description=f"new {i}",
                       timestamp=t0 + timedelta(days=1, seconds=i), encrypted_metadata=None,
                       ip_address="10.0.0.1", user_agent="pytest", current_hash="",
                       previous_hash=prev, hash_alg=HMAC_ALG)
        e.current_hash = hash_entry(e, KEY)
        prev = e.current_hash
        out.append(e)
    return out


def _based_anchor(tmp_path, entries):
    base = legacy_prefix_base(entries)
    p = tmp_path / "anchor.json"
    set_anchor_base(p, base, entries[-1].id, entries[-1].current_hash, KEY)
    return p, base


def test_signed_base_excludes_only_legacy_prefix(tmp_path):
    entries = _with_broken_legacy_prefix()
    assert not verify_entries(entries, KEY)["valid"]  # broken at #1 without a base
    p, base = _based_anchor(tmp_path, entries)
    assert base == {"base_id": 8, "base_prev_hash": entries[6].current_hash, "archived_before": 7}
    r = verify_entries(entries, KEY, read_anchor(p, KEY))
    assert r["valid"] and r["excluded_legacy"] == 7 and r["hmac_entries"] == 50


def test_signed_base_survives_anchor_advance(tmp_path):
    entries = _with_broken_legacy_prefix()
    p, _ = _based_anchor(tmp_path, entries)
    assert advance_anchor(p, entries[-1].id + 1, "f" * 64, KEY)
    a = read_anchor(p, KEY)
    assert a["status"] == "ok" and a["base_id"] == 8


def test_signed_base_still_detects_keyed_tampering(tmp_path):
    entries = _with_broken_legacy_prefix()
    p, _ = _based_anchor(tmp_path, entries)
    anchor = read_anchor(p, KEY)
    for i in range(7, len(entries)):
        t = list(entries)
        t[i] = dataclasses.replace(t[i], description="edited")
        assert verify_entries(t, KEY, anchor)["broken_at"] == t[i].id
    # deleting the base row (oldest keyed row) is caught
    assert not verify_entries(entries[:7] + entries[8:], KEY, anchor)["valid"]
    # downgrading a keyed row to "legacy" is caught
    t = list(entries)
    t[20] = dataclasses.replace(t[20], hash_alg=LEGACY_ALG)
    assert not verify_entries(t, KEY, anchor)["valid"]
    # a keyed row hidden below the base is caught
    t = list(entries)
    t[3] = dataclasses.replace(t[3], hash_alg=HMAC_ALG)
    assert not verify_entries(t, KEY, anchor)["valid"]
    # forging the base in the anchor file breaks its signature
    p.write_text(p.read_text().replace('"base_id": 8', '"base_id": 30'))
    assert read_anchor(p, KEY)["status"] == "invalid"


def test_legacy_prefix_base_refuses_keyed_rows(chain):
    assert legacy_prefix_base(chain) is None  # first row already keyed: nothing to exclude



def test_base_cannot_hide_legacy_rows_after_a_keyed_row(tmp_path):
    mixed = _with_broken_legacy_prefix()
    mixed[2] = dataclasses.replace(mixed[2], hash_alg=HMAC_ALG)  # a keyed row among legacy ones
    p, base = _based_anchor(tmp_path, mixed)
    assert base["base_id"] == 3  # the base is always the FIRST keyed row…
    r = verify_entries(mixed, KEY, read_anchor(p, KEY))
    assert not r["valid"]  # …so the legacy rows after it are still flagged
