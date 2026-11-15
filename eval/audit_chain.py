"""Audit-log tamper detection rate (HMAC hash chain).

Proposal criterion: 100% of tampering attempts on the audit log are detected.

Method: a synthetic 1,000-entry chain is built with the app's own functions
(app.security.audit_chain: canonical_payload / compute_hmac / verify_entries)
under a random key, then --trials independent copies are tampered with, one
attack each, and verified:

  mutate      change one field of a random row (no key -> cannot re-sign)
  forge       change a row and re-sign it with a guessed key
  delete      remove a random row
  insert      insert a forged row between two rows
  swap        swap two adjacent rows (ids kept with the rows)
  truncate    delete the newest rows (caught only by the signed anchor)
  downgrade   rewrite a row as a legacy unkeyed SHA-256 row

The untouched chain must verify (false-positive check). Runs on CPU in seconds,
no database involved.

    python eval/audit_chain.py
"""

from __future__ import annotations

import copy
import hashlib
import random
import secrets
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from common import base_parser, log, write_result

ATTACKS = ["mutate", "forge", "delete", "insert", "swap", "truncate", "downgrade"]


def build_chain(n: int, key: bytes, rng: random.Random):
    from app.security.audit_chain import ChainEntry, canonical_payload, compute_hmac  # noqa: PLC0415

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows, prev = [], None
    for i in range(1, n + 1):
        e = ChainEntry(
            id=i, event_type=rng.choice(["login", "camera_added", "export", "settings_changed", "search"]),
            user_id=rng.randint(1, 20), description=f"synthetic event {i}", timestamp=t0 + timedelta(seconds=7 * i),
            encrypted_metadata=secrets.token_hex(16), ip_address=f"10.0.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
            user_agent="eval", current_hash="", previous_hash=prev,
        )
        e.current_hash = compute_hmac(canonical_payload(e.event_type, e.user_id, e.description, e.timestamp,
                                                        e.encrypted_metadata, e.ip_address, e.user_agent), prev, key)
        rows.append(e)
        prev = e.current_hash
    return rows


def tamper(rows, attack: str, key: bytes, rng: random.Random):
    from app.security.audit_chain import LEGACY_ALG, canonical_payload, compute_hmac  # noqa: PLC0415

    rows = copy.deepcopy(rows)
    i = rng.randrange(1, len(rows) - 1)
    if attack == "mutate":
        field = rng.choice(["event_type", "user_id", "description", "timestamp", "ip_address", "encrypted_metadata"])
        old = getattr(rows[i], field)
        if field == "user_id":
            new = (old or 0) + 1
        elif field == "timestamp":
            new = old + timedelta(seconds=1)
        else:
            new = f"{old}x"
        setattr(rows[i], field, new)
    elif attack == "forge":
        e = rows[i]
        e.description = "nothing happened"
        guessed = hashlib.sha256(b"omnitrack-audit-v2|" + b"changeme").digest()
        e.current_hash = compute_hmac(canonical_payload(e.event_type, e.user_id, e.description, e.timestamp,
                                                        e.encrypted_metadata, e.ip_address, e.user_agent),
                                      e.previous_hash, guessed)
    elif attack == "delete":
        del rows[i]
    elif attack == "insert":
        fake = replace(rows[i], description="injected", current_hash=secrets.token_hex(32),
                       previous_hash=rows[i].current_hash)
        rows.insert(i + 1, fake)
    elif attack == "swap":
        rows[i], rows[i + 1] = rows[i + 1], rows[i]
    elif attack == "truncate":
        del rows[-rng.randint(1, 5):]
    elif attack == "downgrade":
        e = rows[i]
        e.description = "downgraded"
        e.hash_alg = LEGACY_ALG
        e.current_hash = hashlib.sha256(e.description.encode()).hexdigest()
    return rows


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--entries", type=int, default=1000)
    p.add_argument("--trials", type=int, default=100, help="tampered copies per attack type")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    started = time.time()
    from app.security.audit_chain import verify_entries  # noqa: PLC0415

    rng = random.Random(args.seed)
    key = secrets.token_bytes(32)
    rows = build_chain(args.entries, key, rng)
    anchor = {"status": "ok", "last_id": rows[-1].id, "last_hash": rows[-1].current_hash}
    clean = verify_entries(rows, key, anchor)
    per_attack = {}
    for attack in ATTACKS:
        detected = 0
        for _ in range(args.trials):
            r = verify_entries(tamper(rows, attack, key, rng), key, anchor)
            detected += not r["valid"]
        per_attack[attack] = round(detected / args.trials, 4)
    total = args.trials * len(ATTACKS)
    rate = sum(v * args.trials for v in per_attack.values()) / total
    metrics = {
        "detection_rate": round(rate, 4),
        "per_attack": per_attack,
        "clean_chain_valid": bool(clean["valid"]),
        "entries": args.entries,
        "trials_per_attack": args.trials,
        "total_attempts": total,
    }
    log(str(metrics))
    if not clean["valid"]:
        raise SystemExit(f"untouched chain failed verification: {clean}")
    write_result(
        "audit_chain",
        value=rate,
        metrics=metrics,
        dataset={"name": f"synthetic audit chain ({args.entries} entries, seed {args.seed})", "split": "n/a",
                 "source": "generated with app.security.audit_chain", "n_items": args.entries},
        model={"name": "app.security.audit_chain (HMAC-SHA256 chain + signed anchor)"},
        device="cpu",
        started=started,
        notes=["Truncation of the newest rows is only detectable through the signed anchor file; "
               "the run supplies the anchor exactly as read_anchor() returns it."],
    )


if __name__ == "__main__":
    main()
