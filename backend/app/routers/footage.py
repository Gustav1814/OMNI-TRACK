"""
OmniTrack AI — CCTV Footage Storage
List, upload, and serve stored camera clips for playback in the dashboard.
"""

import os
import re
import json
import time
from pathlib import Path
from typing import Any, List, Optional, Set

import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings, resolved_footage_dir, resolved_logs_dir, _backend_dir
from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/footage", tags=["Footage"])

FOOTAGE_DIR = resolved_footage_dir()
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm", ".mov"}
SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _det_track_id_equals(stored: Any, want: int) -> bool:
    """JSON logs may store track_id as int or string; query param is always int."""
    if stored is None:
        return False
    try:
        return int(stored) == int(want)
    except (TypeError, ValueError):
        return False


def _global_id_matches(stored: Any, query: str) -> bool:
    """Match global_id from logs to user input; tolerate case and PERSON- zero-padding."""
    q = (query or "").strip()
    if not q or stored is None:
        return False
    s = str(stored).strip()
    if s == q or s.lower() == q.lower():
        return True
    mq = re.match(r"^PERSON-0*(\d+)$", q, re.IGNORECASE)
    ms = re.match(r"^PERSON-0*(\d+)$", s, re.IGNORECASE)
    if mq and ms and mq.group(1) == ms.group(1):
        return True
    return False


def _ensure_footage_dir():
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)


class FootageItem(BaseModel):
    filename: str
    camera_id: Optional[int] = None
    size_bytes: int = 0
    created_ts: float = 0


@router.get("/list", response_model=List[FootageItem])
async def list_footage(
    camera_id: Optional[int] = Query(None, description="Filter by camera ID"),
    current_user: User = Depends(get_current_user),
):
    """List stored CCTV clips. Optionally filter by camera_id (from filename camera_N_*)."""
    _ensure_footage_dir()
    items: List[FootageItem] = []
    for f in FOOTAGE_DIR.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        name = f.name
        # Parse camera_N_* or N_*
        cam = None
        parts = name.replace("_", " ").split()
        for i, p in enumerate(parts):
            if p.isdigit() and i < 2:
                cam = int(p)
                break
        if camera_id is not None and cam != camera_id:
            continue
        try:
            stat = f.stat()
            items.append(
                FootageItem(
                    filename=name,
                    camera_id=cam,
                    size_bytes=stat.st_size,
                    created_ts=stat.st_mtime,
                )
            )
        except OSError:
            continue
    items.sort(key=lambda x: x.created_ts, reverse=True)
    return items


@router.post("/upload")
async def upload_footage(
    file: UploadFile = File(...),
    camera_id: int = Query(1, description="Camera ID for this clip"),
    current_user: User = Depends(get_current_user),
):
    """Upload a CCTV clip (e.g. from store export). Stored as camera_{id}_{timestamp}.ext."""
    _ensure_footage_dir()
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Allowed extensions: {', '.join(ALLOWED_EXTENSIONS)}")
    safe = re.sub(r"[^\w\-.]", "_", (file.filename or "video")[:80])
    name = f"camera_{camera_id}_{int(time.time())}_{safe}"
    if not name.endswith(ext):
        name += ext
    path = FOOTAGE_DIR / name
    try:
        content = await file.read()
        path.write_bytes(content)
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")
    return {"filename": name, "camera_id": camera_id, "size": len(content)}


@router.get("/serve/{filename}")
async def serve_footage(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """Stream a stored clip by filename (for video playback in dashboard)."""
    filename = os.path.basename(filename)
    if ".." in filename or not SAFE_NAME_RE.match(filename):
        raise HTTPException(400, "Invalid filename")
    path = FOOTAGE_DIR / filename
    if not path.is_file():
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="video/mp4")


# ────────────────────────────────────────────────────────────────
# Detection Logs
# ────────────────────────────────────────────────────────────────

LOGS_DIR = resolved_logs_dir()


@router.get("/logs/list")
async def list_logs(
    current_user: User = Depends(get_current_user),
):
    """List all detection log files."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logs = []
    for f in LOGS_DIR.iterdir():
        if f.is_file() and f.suffix == ".json":
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                logs.append({
                    "filename": f.name,
                    "video_file": data.get("video_file"),
                    "camera_id": data.get("camera_id"),
                    "model": data.get("model"),
                    "total_frames": data.get("total_frames", 0),
                    "start_time": data.get("start_time"),
                    "end_time": data.get("end_time"),
                })
            except Exception:
                logs.append({"filename": f.name, "error": "Could not parse"})
    logs.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    return logs


@router.get("/logs/{log_filename}")
async def get_log(
    log_filename: str,
    current_user: User = Depends(get_current_user),
):
    """Get full detection log contents."""
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/logs/{log_filename}/tracks")
async def get_log_tracks(
    log_filename: str,
    current_user: User = Depends(get_current_user),
):
    """Get summary of all unique track_ids in a log with their frame ranges and class names."""
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tracks = {}
    for frame in data.get("frames", []):
        fn = frame.get("frame_number", 0)
        for det in frame.get("detections", []):
            tid = det.get("track_id")
            if tid is None:
                continue
            if tid not in tracks:
                tracks[tid] = {
                    "track_id": tid,
                    "class_name": det.get("class_name", "unknown"),
                    "global_id": det.get("global_id"),
                    "first_frame": fn,
                    "last_frame": fn,
                    "frame_count": 0,
                    "frames": [],
                }
            tracks[tid]["last_frame"] = max(tracks[tid]["last_frame"], fn)
            tracks[tid]["first_frame"] = min(tracks[tid]["first_frame"], fn)
            tracks[tid]["frame_count"] += 1
            tracks[tid]["frames"].append(fn)
            if det.get("global_id") and not tracks[tid]["global_id"]:
                tracks[tid]["global_id"] = det["global_id"]
    # Remove raw frames list to keep response compact, keep the range info
    for t in tracks.values():
        t.pop("frames", None)
    return {"video_file": data.get("video_file"), "tracks": list(tracks.values())}


def _resolve_stored_video_path(video_file: Optional[str]) -> Optional[Path]:
    """
    Map a log's video_file string to an absolute path. Logs store paths relative to
    backend (e.g. storage/footage/...); OpenCV needs a real path regardless of cwd.
    """
    if not video_file or not str(video_file).strip():
        return None
    raw = str(video_file).strip()
    p = Path(raw)
    try:
        if p.is_file():
            return p.resolve()
    except OSError:
        pass
    backend = _backend_dir()
    candidates = [
        FOOTAGE_DIR / p.name,
        backend / p,
    ]
    seen: Set[Path] = set()
    for c in candidates:
        try:
            r = c.resolve()
            if r in seen:
                continue
            seen.add(r)
            if r.is_file():
                return r
        except OSError:
            continue
    return None


def _segments_from_frames(frames: List[int], padding_frames: int) -> List[tuple]:
    """Merge sorted frame numbers into continuous (start,end) segments with padding."""
    if not frames:
        return []
    sorted_frames = sorted(set(frames))
    segments: List[tuple] = []
    seg_start = max(1, sorted_frames[0] - padding_frames)
    seg_end = sorted_frames[0] + padding_frames
    for fn in sorted_frames[1:]:
        if fn <= seg_end + padding_frames + 1:
            seg_end = fn + padding_frames
        else:
            segments.append((seg_start, seg_end))
            seg_start = max(1, fn - padding_frames)
            seg_end = fn + padding_frames
    segments.append((seg_start, seg_end))
    return segments


def _write_segments_to_clip(video_file: Path, segments: List[tuple], out_path: Path) -> int:
    src = str(video_file.resolve())
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise HTTPException(500, f"Cannot open video: {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    # OpenCV may report total=0 for valid MP4s on some builds/codecs.
    # In that case, keep requested segment bounds and read until EOF.
    if total > 0:
        clamped = [(max(1, s), min(e, total)) for s, e in segments]
    else:
        clamped = [(max(1, s), max(1, e)) for s, e in segments]
    writer = None
    for codec in ("avc1", "H264", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
        if writer.isOpened():
            break
        writer.release()
    if writer is None or not writer.isOpened():
        cap.release()
        raise HTTPException(500, "Failed to create output video")
    written = 0
    for seg_start, seg_end in clamped:
        if seg_end < seg_start:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, seg_start - 1)
        frames_to_read = max(0, int(seg_end - seg_start + 1))
        for _ in range(frames_to_read):
            ret, frame = cap.read()
            if not ret:
                break
            writer.write(frame)
            written += 1
    writer.release()
    cap.release()
    return written


@router.get("/logs/{log_filename}/global-ids")
async def get_log_global_ids(
    log_filename: str,
    current_user: User = Depends(get_current_user),
):
    """Unique global_ids in a single log with their frame ranges and local track ids."""
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    gids: dict = {}
    for frame in data.get("frames", []):
        fn = int(frame.get("frame_number", 0))
        for det in frame.get("detections", []):
            gid = det.get("global_id")
            if not gid:
                continue
            entry = gids.setdefault(gid, {
                "global_id": gid,
                "class_name": det.get("class_name", "unknown"),
                "first_frame": fn,
                "last_frame": fn,
                "frame_count": 0,
                "track_ids": set(),
            })
            entry["first_frame"] = min(entry["first_frame"], fn)
            entry["last_frame"] = max(entry["last_frame"], fn)
            entry["frame_count"] += 1
            tid = det.get("track_id")
            if tid is not None:
                entry["track_ids"].add(tid)
    out = []
    for v in gids.values():
        v["track_ids"] = sorted(v["track_ids"])
        out.append(v)
    out.sort(key=lambda x: x["frame_count"], reverse=True)
    return {"video_file": data.get("video_file"), "global_ids": out}


@router.post("/trim/by-global-id")
async def trim_by_global_id(
    global_id: str = Query(..., description="Global Re-ID identity, e.g. PERSON-00042"),
    padding_frames: int = Query(5),
    current_user: User = Depends(get_current_user),
):
    """
    Trim every recorded video that contains this global_id (across cameras) into one
    clip per source video. Useful for following a single person across feeds via Re-ID.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_footage_dir()
    results = []
    for log_file in LOGS_DIR.iterdir():
        if not log_file.is_file() or log_file.suffix != ".json":
            continue
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        video_file = data.get("video_file")
        video_path = _resolve_stored_video_path(video_file)
        if not video_path:
            continue
        target_frames = []
        for frame in data.get("frames", []):
            for det in frame.get("detections", []):
                if _global_id_matches(det.get("global_id"), global_id):
                    target_frames.append(int(frame.get("frame_number", 0)))
                    break
        if not target_frames:
            continue
        segments = _segments_from_frames(target_frames, padding_frames)
        stem = video_path.stem
        out_name = f"{stem}_{re.sub(r'[^a-zA-Z0-9_-]', '_', global_id)}.mp4"
        out_path = FOOTAGE_DIR / out_name
        try:
            written = _write_segments_to_clip(video_path, segments, out_path)
        except HTTPException:
            continue
        results.append({
            "log_file": log_file.name,
            "trimmed_video": out_name,
            "camera_id": data.get("camera_id"),
            "segments": segments,
            "frames_written": written,
            "original_video": str(video_path),
        })
    if not results:
        raise HTTPException(404, f"global_id {global_id} not found in any recorded log")
    return {"global_id": global_id, "clips": results}


@router.post("/trim/by-track")
async def trim_by_track(
    log_filename: str = Query(..., description="Detection log filename (e.g., camera_1_1778278505.json)"),
    track_id: int = Query(..., description="Track ID to extract"),
    padding_frames: int = Query(5, description="Extra frames before/after each appearance"),
    current_user: User = Depends(get_current_user),
):
    """
    Trim the original recorded video to only frames where a specific track_id is visible.
    Uses the detection log to find frame numbers, then extracts those segments from the video.
    Output is saved as a new clip in storage/footage.
    """
    log_filename = os.path.basename(log_filename)
    log_path = LOGS_DIR / log_filename
    if not log_path.is_file():
        raise HTTPException(404, "Detection log not found")

    with open(log_path, "r", encoding="utf-8") as f:
        log_data = json.load(f)

    video_file = log_data.get("video_file")
    video_path = _resolve_stored_video_path(video_file)
    if not video_path:
        raise HTTPException(404, f"Original video not found: {video_file}")

    target_frames: List[int] = []
    for frame in log_data.get("frames", []):
        for det in frame.get("detections", []):
            if _det_track_id_equals(det.get("track_id"), track_id):
                target_frames.append(int(frame.get("frame_number", 0)))
                break
    if not target_frames:
        raise HTTPException(404, f"Track ID {track_id} not found in log")

    segments = _segments_from_frames(target_frames, padding_frames)
    _ensure_footage_dir()
    stem = video_path.stem
    out_name = f"{stem}_track{track_id}.mp4"
    out_path = FOOTAGE_DIR / out_name
    frames_written = _write_segments_to_clip(video_path, segments, out_path)

    return {
        "trimmed_video": out_name,
        "path": str(out_path),
        "track_id": track_id,
        "segments": segments,
        "frames_written": frames_written,
        "original_video": str(video_path),
    }
