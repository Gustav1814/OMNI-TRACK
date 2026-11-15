"""
OmniTrack AI - Alert evidence (snapshots / clips) browsing and media.

Files are AES-256-GCM encrypted at rest (app/services/evidence.py) and decrypted
per request. Media GETs accept ?token= (app-wide for GET/HEAD) so <img>/<video>
can load them; /file honours HTTP Range so <video> can seek.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models.alerts import EvidenceItem
from app.models.user import User
from app.security.dependencies import get_current_user
from app.security.encryption import DecryptionError
from app.services import evidence as ev

router = APIRouter(prefix="/api/evidence", tags=["Evidence"])

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CACHE_MAX_BYTES = 64 * 1024 * 1024
_cache: "OrderedDict[str, bytes]" = OrderedDict()
_cache_size = 0
_cache_lock = threading.Lock()


def _cache_get(key: str) -> Optional[bytes]:
    with _cache_lock:
        data = _cache.get(key)
        if data is not None:
            _cache.move_to_end(key)
        return data


def _cache_put(key: str, data: bytes) -> None:
    global _cache_size
    if len(data) > _CACHE_MAX_BYTES // 4:
        return
    with _cache_lock:
        if key in _cache:
            return
        _cache[key] = data
        _cache_size += len(data)
        while _cache_size > _CACHE_MAX_BYTES and _cache:
            _, old = _cache.popitem(last=False)
            _cache_size -= len(old)


def _item_dict(r: EvidenceItem) -> dict:
    return {
        "id": r.id, "source": r.source, "source_id": r.source_id, "event_uid": r.event_uid,
        "camera_id": r.camera_id, "track_id": r.track_id, "kind": r.kind,
        "media_type": ev.media_type_for(r.path),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "bytes": r.bytes,
    }


async def _load(rel: str, encrypted: bool) -> bytes:
    key = f"{rel}:{int(encrypted)}"
    data = _cache_get(key)
    if data is not None:
        return data
    try:
        data = await run_in_threadpool(ev.read_item_bytes, rel, encrypted)
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="Evidence file no longer exists")
    except ValueError:
        raise HTTPException(status_code=404, detail="Evidence not found")
    except DecryptionError:
        logger.error(f"evidence: cannot decrypt {rel} (wrong AES key or tampered file)")
        raise HTTPException(status_code=410, detail="Evidence file cannot be decrypted")
    _cache_put(key, data)
    return data


async def _get_item(db: AsyncSession, item_id: int) -> EvidenceItem:
    row = await db.get(EvidenceItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return row


@router.get("")
async def list_evidence(
    camera_id: Optional[int] = None,
    source: Optional[Literal["zone_event", "fire_incident", "humanless"]] = None,
    source_id: Optional[int] = None,
    kind: Optional[Literal["snapshot", "clip"]] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    conds = []
    if camera_id is not None:
        conds.append(EvidenceItem.camera_id == camera_id)
    if source:
        conds.append(EvidenceItem.source == source)
    if source_id is not None:
        conds.append(EvidenceItem.source_id == source_id)
    if kind:
        conds.append(EvidenceItem.kind == kind)
    total = int((await db.execute(select(func.count()).select_from(EvidenceItem).where(*conds))).scalar_one())
    rows = (
        await db.execute(
            select(EvidenceItem).where(*conds)
            .order_by(EvidenceItem.created_at.desc(), EvidenceItem.id.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    out = {"items": [_item_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}
    if total == 0:
        out["status"] = "no_data"
    return out


@router.get("/{item_id}/file")
async def evidence_file(
    item_id: int,
    request: Request,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = await _get_item(db, item_id)
    data = await _load(row.path, bool(row.encrypted))
    media_type = ev.media_type_for(row.path)
    size = len(data)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    if download:
        ext = {"image/jpeg": "jpg", "video/mp4": "mp4", "video/webm": "webm"}.get(media_type, "bin")
        headers["Content-Disposition"] = f'attachment; filename="evidence_{row.id}_{row.kind}.{ext}"'

    rng = request.headers.get("range")
    if rng:
        m = _RANGE.match(rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            n = int(m.group(2))
            start, end = max(0, size - n), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(content=data[start:end + 1], status_code=206, media_type=media_type, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)


@router.get("/{item_id}/thumb")
async def evidence_thumb(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = await _get_item(db, item_id)
    data = await _load(ev.thumb_rel_path(row.path), bool(row.encrypted))
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
