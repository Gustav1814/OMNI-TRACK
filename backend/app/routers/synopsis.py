"""
OmniTrack AI — Video synopsis API (streaming engine in app/ai/synopsis.py).

* Sources are restricted to video files under VIDEO_SOURCE_DIRS (realpath
  checked: no ``../`` or symlink escapes) — the old endpoint accepted any path.
* One synopsis runs at a time (CPU-heavy); others queue.
* Every finished synopsis writes a ``<name>.json`` sidecar with the measured
  metrics, so the library shows honest numbers after a restart. Files without a
  sidecar are listed with ``metrics: null`` rather than invented values.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.models.user import User, UserRole
from app.security.dependencies import get_current_user, require_role
from app.security.input_safety import UnsafeInputError, resolve_video_file

router = APIRouter(prefix="/api/synopsis", tags=["Video Synopsis"])

VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".webm", ".mov", ".mpg", ".mpeg"}
OUTPUT_TYPES = {".webm": "video/webm", ".mp4": "video/mp4", ".avi": "video/x-msvideo"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-.]+$")
_JOBS: Dict[int, Dict[str, Any]] = {}
_JOB_IDS = itertools.count(1)
_RUN_LOCK = asyncio.Lock()
_MAX_JOBS_KEPT = 50
_TASKS: set = set()  # strong refs so running jobs are not garbage-collected


def _out_dir() -> Path:
    d = Path(settings.EXPORT_DIR) / "synopsis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel_source(path: Path) -> str:
    """Path relative to its video root (what the UI shows / sends back)."""
    for root in settings.VIDEO_SOURCE_DIRS:
        try:
            return str(path.relative_to(os.path.realpath(root)))
        except ValueError:
            continue
    return path.name


def _iter_sources():
    for root in settings.VIDEO_SOURCE_DIRS:
        base = Path(os.path.realpath(root))
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):  # os.walk does not follow symlinked dirs
            for f in files:
                p = Path(dirpath) / f
                if p.suffix.lower() in VIDEO_EXTS and not p.is_symlink():
                    yield base, p


def _pick_source(camera_id: int, source: Optional[str]) -> Path:
    if source:
        try:
            return resolve_video_file(source)
        except UnsafeInputError as e:
            raise HTTPException(404, str(e))
    footage = Path(os.path.realpath(settings.FOOTAGE_DIR))
    cands = [p for p in footage.glob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS] if footage.is_dir() else []
    mine = [p for p in cands if p.name.startswith(f"camera_{camera_id}_")]
    pool = mine or cands
    if not pool:
        raise HTTPException(404, "No recorded footage for this camera — pick a source clip")
    return max(pool, key=lambda p: p.stat().st_mtime)


def _public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    hidden = {"output_path", "tubes_path", "input_path"}
    return {k: v for k, v in job.items() if k not in hidden}


def _summary(meta: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "original_duration", "synopsis_duration", "compression_ratio", "compression_target",
        "tubes_detected", "tubes_kept", "tubes_preserved", "tubes_dropped", "tubes_preserved_pct",
        "original_frames", "synopsis_frames", "fps", "resolution", "codec", "processing_seconds",
        "background_samples", "status",
    )
    return {k: meta.get(k) for k in keys}


async def _run_job(job_id: int, src: Path, out: Path, compression: float) -> None:
    from app.ai.synopsis import VideoSynopsis

    job = _JOBS[job_id]

    def progress(stage: str, frac: float) -> None:
        job["stage"], job["progress"] = stage, round(float(frac), 3)

    async with _RUN_LOCK:
        job["status"], job["started_at"] = "running", time.time()
        try:
            engine = VideoSynopsis(compression_target=compression, progress=progress)
            result = await asyncio.to_thread(engine.process_video, str(src), str(out))
            meta = {**_summary(result), "camera_id": job["camera_id"], "source": job["source"],
                    "filename": out.name, "created_at": time.time()}
            out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
            job.update(_summary(result))
            job["status"] = "completed" if result.get("status") == "completed" else result.get("status", "completed")
        except Exception as e:  # surfaced to the UI; engine errors are user-facing messages
            job["status"], job["error"] = "failed", str(e)[:300]
            out.unlink(missing_ok=True)
        finally:
            job["finished_at"] = time.time()


@router.get("/sources")
async def list_sources(_: User = Depends(get_current_user)):
    """Video files a synopsis may be generated from (footage + demo clips)."""
    items = []
    for _base, p in _iter_sources():
        try:
            items.append({"source": _rel_source(p), "name": p.name, "size_bytes": p.stat().st_size})
        except OSError:
            continue
    items.sort(key=lambda x: x["source"])
    return items[:500]


@router.get("/")
async def list_synopses(_: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    out = []
    for f in _out_dir().iterdir():
        if not f.is_file() or f.suffix.lower() not in OUTPUT_TYPES:
            continue
        meta = None
        side = f.with_suffix(".json")
        if side.is_file():
            try:
                meta = json.loads(side.read_text())
            except (OSError, ValueError):
                meta = None
        st = f.stat()
        out.append({
            "id": f.stem, "filename": f.name, "size_bytes": st.st_size, "created_ts": st.st_mtime,
            "camera_id": (meta or {}).get("camera_id"), "source": (meta or {}).get("source"),
            "metrics": _summary(meta) if meta else None,
            # flat fields kept for older UI code; null when unknown, never invented
            "original_duration": (meta or {}).get("original_duration"),
            "synopsis_duration": (meta or {}).get("synopsis_duration"),
            "compression_ratio": (meta or {}).get("compression_ratio"),
            "status": "completed",
        })
    out.sort(key=lambda x: x["created_ts"], reverse=True)
    return out


@router.get("/serve/{filename}")
async def serve_synopsis(filename: str, _: User = Depends(get_current_user)):
    name = os.path.basename(filename)
    if not _SAFE_NAME.match(name) or Path(name).suffix.lower() not in OUTPUT_TYPES:
        raise HTTPException(400, "Invalid filename")
    base = _out_dir().resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "Synopsis file not found")
    return FileResponse(path, media_type=OUTPUT_TYPES[path.suffix.lower()])


@router.post("/generate", status_code=202)
async def generate_synopsis(
    camera_id: int = Query(1, ge=0),
    source: Optional[str] = Query(None, max_length=400, description="Clip path relative to a video directory"),
    compression: float = Query(10.0, ge=1.0, le=100.0),
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    src = _pick_source(camera_id, source)
    if sum(1 for j in _JOBS.values() if j["status"] in ("queued", "running")) >= 3:
        raise HTTPException(429, "Too many synopsis jobs queued; wait for one to finish")
    job_id = next(_JOB_IDS)
    out = _out_dir() / f"synopsis_cam{camera_id}_{int(time.time())}_{job_id}.webm"
    _JOBS[job_id] = {
        "id": job_id, "job_id": job_id, "camera_id": camera_id, "status": "queued",
        "stage": "queued", "progress": 0.0, "source": _rel_source(src), "filename": out.name,
        "requested_by": admin.username, "queued_at": time.time(),
    }
    for old in sorted(_JOBS)[:-_MAX_JOBS_KEPT]:
        if _JOBS[old]["status"] not in ("queued", "running"):
            _JOBS.pop(old, None)
    task = asyncio.get_running_loop().create_task(_run_job(job_id, src, out, compression))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return _public_job(_JOBS[job_id])


@router.get("/jobs/{job_id}")
async def synopsis_job_status(job_id: int, _: User = Depends(get_current_user)):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"Synopsis job {job_id} not found")
    return _public_job(job)
