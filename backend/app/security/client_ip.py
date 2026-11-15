"""
OmniTrack AI — real client IP resolution.

``X-Forwarded-For`` is attacker-controlled unless it was appended by a proxy we
trust. We therefore honour it only when the TCP peer is in TRUSTED_PROXIES, and
then walk the header right-to-left skipping trusted hops: the first untrusted
address is the real client. With no trusted proxies configured the TCP peer is
always used, so spoofing X-Forwarded-For cannot dodge rate limits or lockout.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Optional, Sequence, Tuple

from starlette.requests import HTTPConnection

from app.config import settings


@lru_cache(maxsize=16)
def _networks(entries: Tuple[str, ...]):
    nets = []
    for item in entries:
        try:
            nets.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted(ip: str, nets) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets if addr.version == n.version)


def resolve_client_ip(
    peer: Optional[str],
    forwarded_for: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> str:
    nets = _networks(tuple(trusted if trusted is not None else settings.TRUSTED_PROXIES))
    peer = peer or "unknown"
    if not forwarded_for or not nets or not _is_trusted(peer, nets):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # malformed header from a trusted proxy: fall back to the peer
        if not _is_trusted(hop, nets):
            return hop
    return hops[0] if hops else peer


def client_ip(conn: HTTPConnection) -> str:
    peer = conn.client.host if conn.client else None
    return resolve_client_ip(peer, conn.headers.get("x-forwarded-for"))
