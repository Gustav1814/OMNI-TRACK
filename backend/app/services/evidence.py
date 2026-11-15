"""
Alert evidence: a snapshot + short clip around each alert / fire start, encrypted
at rest (AES-256-GCM, app/security/encryption.py) under settings.EVIDENCE_DIR.

    schedule_capture(event, camera_id, frame_provider)   # never blocks, never raises

* `event` is an analytics-engine event dict:
    - type "alert" with payload.action "open" (close events are ignored) and
      "event_uid"; linked to zone_events via event_uid (+ id once persisted);
    - type "fire" with payload.action "start" | "reopen" and payload.incident_key;
      linked to fire_incidents via incident_key (stored in evidence_items.event_uid).
  Optional: "bbox_xyxy" (+ "frame_wh") on any event draws a box on the snapshot;
  fire events use payload.bbox_xywh + payload.frame_wh.
* frame_provider(t_from, t_to) -> [(ts, BGR ndarray), ...] in the event's time base.
  The capture runs on one background worker thread after EVIDENCE_POST_S of wall
  time has passed (post-roll), from a bounded queue: when full, the capture is
  dropped with a warning — the caller is never blocked.
* Clips: avc1 (H.264 .mp4) -> VP8 (.webm) -> mp4v (.mp4), first that writes a file
  OpenCV can read back. VP8 comes before mp4v because browsers cannot play MPEG-4
  Part 2, and stock opencv-python wheels have no H.264 encoder.
* Retention (EVIDENCE_RETENTION_DAYS) and quota (EVIDENCE_MAX_GB, oldest evicted
  first) are enforced after each capture and by purge_expired() (db maintenance).
"""

from __future__ import annotations

import asyncio
import os
import queue
import re
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from loguru import logger
from sqlalchemy import and_, delete, func, select

from app.config import settings
from app.models.alerts import AlertAck, EvidenceItem
from app.security.encryption import read_encrypted_file, write_encrypted_file

FrameProvider = Callable[[float, float], List[Tuple[float, np.ndarray]]]

CLIP_MAX_WIDTH = 640
CLIP_MAX_FPS = 10.0
THUMB_WIDTH = 320
# (fourcc, container extension). See module docstring for the order.
CLIP_CODECS: Tuple[Tuple[str, str], ...] = (("avc1", ".mp4"), ("VP80", ".webm"), ("mp4v", ".mp4"))
MEDIA_TYPES = {".jpg": "image/jpeg", ".mp4": "video/mp4", ".webm": "video/webm"}

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def evidence_root() -> Path:
    return Path(settings.EVIDENCE_DIR)


def resolve_path(rel: str) -> Path:
    """Absolute path of a stored evidence file; refuses anything outside the root."""
    root = evidence_root().resolve()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        raise ValueError("evidence path escapes the evidence root")
    return p


def media_type_for(path: str) -> str:
    name = path[:-4] if path.endswith(".enc") else path
    return MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def thumb_rel_path(item_path: str) -> str:
    """Every capture stores one thumbnail next to its snapshot/clip: <base>_thumb.jpg.enc."""
    base = item_path.rsplit("_", 1)[0]
    return f"{base}_thumb.jpg.enc"


def read_item_bytes(item_path: str, encrypted: bool = True) -> bytes:
    p = resolve_path(item_path)
    return read_encrypted_file(p) if encrypted else p.read_bytes()


# ─────────────────────────── job building ───────────────────────────


@dataclass
class _Job:
    source: str                 # zone_event | fire_incident
    key: str                    # event_uid (zone_event) or incident_key (fire)
    camera_id: int
    ts: float                   # alert time in the provider's time base
    track_id: Optional[int]
    label: str
    box: Optional[Tuple[float, float, float, float]]   # xyxy in frame_wh pixels
    box_wh: Optional[Tuple[int, int]]
    provider: FrameProvider
    ready_at: float = field(default=0.0)               # time.monotonic()


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _build_job(event: dict, camera_id: Any, provider: FrameProvider) -> Optional[_Job]:
    cam = _int_or_none(camera_id if camera_id is not None else event.get("camera_id"))
    if cam is None:
        return None
    etype = event.get("type") or event.get("event_type")
    payload = event.get("payload") or {}
    action = payload.get("action")
    ts = event.get("ts")
    if ts is None:
        return None
    box = event.get("bbox_xyxy")
    box_wh = event.get("frame_wh")
    if etype == "alert":
        if action not in (None, "open") or not event.get("event_uid"):
            return None
        source, key, label = "zone_event", str(event["event_uid"])[:36], str(payload.get("alert_type") or "alert")
    elif etype == "fire":
        if action not in ("start", "reopen") or not payload.get("incident_key"):
            return None
        source, key, label = "fire_incident", str(payload["incident_key"])[:36], str(payload.get("class_name") or "fire")
        xywh = payload.get("bbox_xywh")
        if xywh and len(xywh) == 4:
            x, y, w, h = (float(v) for v in xywh)
            box = (x, y, x + w, y + h)
            box_wh = payload.get("frame_wh")
    else:
        return None
    return _Job(
        source=source, key=key, camera_id=cam, ts=float(ts), track_id=_int_or_none(event.get("track_id")),
        label=label, box=tuple(float(v) for v in box[:4]) if box and len(box) >= 4 else None,
        box_wh=tuple(int(v) for v in box_wh[:2]) if box_wh and len(box_wh) >= 2 else None,
        provider=provider,
    )


# ─────────────────────────── media encoding ───────────────────────────


def _annotate(frame: np.ndarray, job: _Job) -> np.ndarray:
    import cv2  # noqa: PLC0415

    if job.box is None:
        return frame
    out = frame.copy()
    h, w = out.shape[:2]
    sx = w / job.box_wh[0] if job.box_wh and job.box_wh[0] else 1.0
    sy = h / job.box_wh[1] if job.box_wh and job.box_wh[1] else 1.0
    x1, y1, x2, y2 = job.box
    p1 = (int(max(0, x1 * sx)), int(max(0, y1 * sy)))
    p2 = (int(min(w - 1, x2 * sx)), int(min(h - 1, y2 * sy)))
    color = (0, 0, 255) if job.source == "fire_incident" else (0, 200, 255)
    cv2.rectangle(out, p1, p2, color, 2, cv2.LINE_AA)
    cv2.putText(out, job.label, (p1[0], max(14, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def _resize_max_width(frame: np.ndarray, max_w: int) -> np.ndarray:
    import cv2  # noqa: PLC0415

    h, w = frame.shape[:2]
    if w <= max_w:
        nw, nh = w, h
    else:
        nw, nh = max_w, int(round(h * max_w / w))
    nw, nh = max(2, nw - nw % 2), max(2, nh - nh % 2)   # even dims for H.264 / VP8
    if (nw, nh) == (w, h):
        return frame
    return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)


def _subsample(frames: Sequence[Tuple[float, np.ndarray]], max_fps: float) -> List[Tuple[float, np.ndarray]]:
    out: List[Tuple[float, np.ndarray]] = []
    min_dt = 1.0 / max_fps
    for ts, fr in frames:
        if not out or ts - out[-1][0] >= min_dt * 0.95:
            out.append((ts, fr))
    return out


def _clip_fps(frames: Sequence[Tuple[float, np.ndarray]]) -> float:
    if len(frames) < 2:
        return 1.0
    span = frames[-1][0] - frames[0][0]
    if span <= 0:
        return CLIP_MAX_FPS
    return float(min(CLIP_MAX_FPS, max(1.0, (len(frames) - 1) / span)))


def _video_readable(path: str, min_frames: int = 1) -> bool:
    import cv2  # noqa: PLC0415

    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return False
        n = 0
        while n < min_frames:
            ok, _ = cap.read()
            if not ok:
                break
            n += 1
        return n >= min_frames
    finally:
        cap.release()


def encode_clip(frames: Sequence[Tuple[float, np.ndarray]], tmp_dir: Optional[Path] = None) -> Optional[Tuple[bytes, str, str]]:
    """Encode (ts, BGR) frames -> (bytes, extension, fourcc), or None if no codec works."""
    import cv2  # noqa: PLC0415

    frames = _subsample(sorted(frames, key=lambda f: f[0]), CLIP_MAX_FPS)
    if not frames:
        return None
    fps = _clip_fps(frames)
    first = _resize_max_width(frames[0][1], CLIP_MAX_WIDTH)
    h, w = first.shape[:2]
    tmp_dir = Path(tmp_dir or tempfile.gettempdir())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for fourcc, ext in CLIP_CODECS:
        fd, tmp = tempfile.mkstemp(suffix=ext, dir=str(tmp_dir))
        os.close(fd)
        try:
            writer = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
            if not writer.isOpened():
                writer.release()
                continue
            try:
                for _, fr in frames:
                    fr = _resize_max_width(fr, CLIP_MAX_WIDTH)
                    if fr.shape[:2] != (h, w):
                        fr = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
                    writer.write(fr)
            finally:
                writer.release()
            if os.path.getsize(tmp) > 0 and _video_readable(tmp, min_frames=min(3, len(frames))):
                return Path(tmp).read_bytes(), ext, fourcc
            logger.debug(f"evidence: {fourcc} wrote an unreadable clip, trying the next codec")
        except Exception as exc:
            logger.debug(f"evidence: codec {fourcc} failed: {exc}")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


def _jpeg(frame: np.ndarray, quality: int = 88) -> bytes:
    import cv2  # noqa: PLC0415

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


# ─────────────────────────── database (worker-owned loop) ───────────────────────────


class _Db:
    """A private NullPool engine on the worker thread's own event loop, so the
    worker never touches the API loop or its connection pool."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._maker = None

    def run(self, coro_fn: Callable, *args):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(self._call(coro_fn, *args))

    async def _call(self, coro_fn, *args):
        if self._maker is None:
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: PLC0415
            from sqlalchemy.pool import NullPool  # noqa: PLC0415

            eng = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
            self._maker = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
        async with self._maker() as session:
            out = await coro_fn(session, *args)
            await session.commit()
            return out


async def _resolve_source_id(session, source: str, key: str) -> Optional[int]:
    from app.models.zones import FireIncident, ZoneEvent  # noqa: PLC0415

    if source == "zone_event":
        q = select(ZoneEvent.id).where(ZoneEvent.event_uid == key).order_by(ZoneEvent.id).limit(1)
    elif source == "fire_incident":
        q = select(FireIncident.id).where(FireIncident.incident_key == key).limit(1)
    else:
        return None
    return (await session.execute(q)).scalar_one_or_none()


async def _record(session, job: _Job, files: List[Tuple[str, str, int]]) -> List[int]:
    """Insert one evidence row per (kind, rel_path, bytes); then enforce the quota."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.EVIDENCE_RETENTION_DAYS) if settings.EVIDENCE_RETENTION_DAYS > 0 else None
    source_id = await _resolve_source_id(session, job.source, job.key)
    rows = []
    for kind, rel, nbytes in files:
        row = EvidenceItem(
            source=job.source, source_id=source_id, event_uid=job.key, camera_id=job.camera_id,
            track_id=job.track_id, kind=kind, path=rel, encrypted=True, bytes=int(nbytes),
            created_at=now, expires_at=expires,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    await enforce_quota(session)
    return [r.id for r in rows]


def _unlink_item_files(rel: str) -> int:
    freed = 0
    for r in (rel, thumb_rel_path(rel)):
        try:
            p = resolve_path(r)
        except ValueError:
            continue
        try:
            freed += p.stat().st_size
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(f"evidence: could not delete {p}: {exc}")
    return freed


async def _delete_rows(session, rows: Sequence[EvidenceItem]) -> int:
    if not rows:
        return 0
    ids = [r.id for r in rows]
    await session.execute(delete(EvidenceItem).where(EvidenceItem.id.in_(ids)))
    for r in rows:
        _unlink_item_files(r.path)
    return len(ids)


async def enforce_quota(session) -> int:
    """Evict the oldest evidence until the total is within EVIDENCE_MAX_GB."""
    limit = int(float(settings.EVIDENCE_MAX_GB) * (1024 ** 3))
    if limit <= 0:
        return 0
    total = int((await session.execute(select(func.coalesce(func.sum(EvidenceItem.bytes), 0)))).scalar_one())
    if total <= limit:
        return 0
    removed = 0
    while total > limit:
        batch = (
            await session.execute(select(EvidenceItem).order_by(EvidenceItem.created_at, EvidenceItem.id).limit(50))
        ).scalars().all()
        if not batch:
            break
        doomed = []
        for r in batch:
            if total <= limit:
                break
            doomed.append(r)
            total -= int(r.bytes or 0)
        removed += await _delete_rows(session, doomed)
    if removed:
        logger.info(f"evidence: quota {settings.EVIDENCE_MAX_GB} GB reached, evicted {removed} oldest item(s)")
    return removed


async def purge_expired(session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Retention purge (called from db maintenance): expired evidence rows + files,
    the quota, and acknowledgements whose alert row no longer exists."""
    from app.models.humanless import LossPreventionAlert  # noqa: PLC0415
    from app.models.zones import FireIncident, ZoneEvent  # noqa: PLC0415

    now = now or datetime.now(timezone.utc)
    conds = [and_(EvidenceItem.expires_at.is_not(None), EvidenceItem.expires_at < now)]
    if settings.EVIDENCE_RETENTION_DAYS > 0:
        conds.append(EvidenceItem.created_at < now - timedelta(days=settings.EVIDENCE_RETENTION_DAYS))
    from sqlalchemy import or_  # noqa: PLC0415

    expired_n = 0
    while True:
        rows = (await session.execute(select(EvidenceItem).where(or_(*conds)).limit(500))).scalars().all()
        if not rows:
            break
        expired_n += await _delete_rows(session, rows)
    quota_n = await enforce_quota(session)
    orphan_acks = 0
    for source, model in (("zone_event", ZoneEvent), ("fire_incident", FireIncident), ("humanless", LossPreventionAlert)):
        res = await session.execute(
            delete(AlertAck).where(
                AlertAck.source == source,
                ~select(model.id).where(model.id == AlertAck.source_id).exists(),
            )
        )
        orphan_acks += res.rowcount or 0
    return {"evidence_expired": expired_n, "evidence_quota": quota_n, "alert_acks_orphaned": orphan_acks}


# ─────────────────────────── capture worker ───────────────────────────


class EvidenceRecorder:
    def __init__(self, maxsize: int) -> None:
        self._q: "queue.Queue[_Job]" = queue.Queue(maxsize=max(1, int(maxsize)))
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._recent: "OrderedDict[str, float]" = OrderedDict()
        self._db = _Db()
        self.stats = {"scheduled": 0, "dropped": 0, "captured": 0, "failed": 0, "no_frames": 0}

    # -- producer side (any thread) --
    def submit(self, job: _Job) -> bool:
        dedupe = f"{job.source}:{job.key}"
        with self._start_lock:
            if dedupe in self._recent:
                return False
            self._recent[dedupe] = time.monotonic()
            while len(self._recent) > 512:
                self._recent.popitem(last=False)
            self._ensure_thread()
        job.ready_at = time.monotonic() + max(0.0, float(settings.EVIDENCE_POST_S)) + 0.5
        try:
            self._q.put_nowait(job)
        except queue.Full:
            self.stats["dropped"] += 1
            logger.warning(
                f"evidence: queue full ({self._q.maxsize}); dropped capture for {job.source} {job.key} cam {job.camera_id}"
            )
            return False
        self.stats["scheduled"] += 1
        return True

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="evidence-capture", daemon=True)
            self._thread.start()

    def pending(self) -> int:
        return self._q.qsize()

    # -- worker --
    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                delay = job.ready_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.capture(job)
            except Exception as exc:  # pragma: no cover - logged, worker keeps going
                self.stats["failed"] += 1
                logger.error(f"evidence: capture failed for {job.source} {job.key}: {exc}")
            finally:
                self._q.task_done()

    def capture(self, job: _Job) -> List[int]:
        """Fetch frames, write encrypted snapshot/clip/thumb, record rows. Returns row ids."""
        pre, post = float(settings.EVIDENCE_PRE_S), float(settings.EVIDENCE_POST_S)
        try:
            frames = list(job.provider(job.ts - pre, job.ts + post) or [])
        except Exception as exc:
            logger.warning(f"evidence: frame provider failed for cam {job.camera_id}: {exc}")
            frames = []
        frames = [(float(t), f) for t, f in frames if f is not None and getattr(f, "ndim", 0) == 3]
        if not frames:
            self.stats["no_frames"] += 1
            logger.warning(f"evidence: no buffered frames for {job.source} {job.key} cam {job.camera_id}")
            return []
        frames.sort(key=lambda f: f[0])
        snap_ts, snap = min(frames, key=lambda f: abs(f[0] - job.ts))
        snap = _annotate(snap, job)

        root = evidence_root()
        day = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        rel_dir = Path(str(job.camera_id)) / day.strftime("%Y%m%d")
        base = f"{day.strftime('%H%M%S')}_{job.source}_{_SAFE.sub('-', job.key)[:40]}"
        written: List[Tuple[str, str, int]] = []
        try:
            snap_rel = str(rel_dir / f"{base}_snapshot.jpg.enc")
            jpg = _jpeg(snap)
            write_encrypted_file(root / snap_rel, jpg)
            write_encrypted_file(root / thumb_rel_path(snap_rel), _jpeg(_resize_max_width(snap, THUMB_WIDTH), 80))
            written.append(("snapshot", snap_rel, (root / snap_rel).stat().st_size))

            clip = encode_clip(frames, tmp_dir=root / ".tmp")
            del frames
            if clip is not None:
                data, ext, fourcc = clip
                clip_rel = str(rel_dir / f"{base}_clip{ext}.enc")
                write_encrypted_file(root / clip_rel, data)
                written.append(("clip", clip_rel, (root / clip_rel).stat().st_size))
                logger.info(f"evidence: {job.source} {job.key} cam {job.camera_id}: clip {fourcc} {len(data)} B")
            else:
                logger.error("evidence: no working video codec (avc1/VP80/mp4v); stored the snapshot only")
            ids = self._db.run(_record, job, written)
            self.stats["captured"] += 1
            return ids
        except Exception:
            for _, rel, _n in written:
                _unlink_item_files(rel)
            raise


_recorder: Optional[EvidenceRecorder] = None
_recorder_lock = threading.Lock()


def get_recorder() -> EvidenceRecorder:
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = EvidenceRecorder(settings.EVIDENCE_QUEUE_SIZE)
        return _recorder


def schedule_capture(event: dict, camera_id: Any, frame_provider: FrameProvider) -> None:
    """Queue evidence capture for an alert-open / fire start event. Never blocks or raises."""
    try:
        if not settings.EVIDENCE_ENABLED or not isinstance(event, dict) or frame_provider is None:
            return
        job = _build_job(event, camera_id, frame_provider)
        if job is None:
            return
        get_recorder().submit(job)
    except Exception as exc:  # pragma: no cover - defensive: the frame loop must not see this
        logger.error(f"evidence: schedule_capture failed: {exc}")
