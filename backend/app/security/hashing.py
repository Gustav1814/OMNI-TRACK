"""
OmniTrack AI — SHA-256 Hash Chain
Tamper-evident audit trail — each entry includes hash of previous entry
"""

import hashlib
import json
from typing import Optional, Dict, Any


def compute_hash(payload: Dict[str, Any], previous_hash: Optional[str] = None) -> str:
    """
    Compute SHA-256 hash of payload concatenated with previous hash.
    This creates an immutable chain — modifying any entry breaks the chain.
    """
    data_str = json.dumps(payload, sort_keys=True, default=str)
    chain_input = f"{data_str}|{previous_hash or 'GENESIS'}"
    return hashlib.sha256(chain_input.encode("utf-8")).hexdigest()


def verify_chain(entries: list) -> Dict[str, Any]:
    """
    Verify integrity of the entire audit chain.
    Returns {"valid": bool, "broken_at": int | None, "total": int}
    """
    for i, entry in enumerate(entries):
        expected_prev = entries[i - 1]["current_hash"] if i > 0 else None
        if entry.get("previous_hash") != expected_prev:
            return {"valid": False, "broken_at": i, "total": len(entries)}

        payload = {
            "event_type": entry["event_type"],
            "user_id": entry.get("user_id"),
            "description": entry.get("description"),
            "timestamp": entry.get("timestamp"),
        }
        recomputed = compute_hash(payload, entry.get("previous_hash"))
        if recomputed != entry["current_hash"]:
            return {"valid": False, "broken_at": i, "total": len(entries)}

    return {"valid": True, "broken_at": None, "total": len(entries)}
