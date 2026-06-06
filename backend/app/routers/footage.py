"""
OmniTrack AI — CCTV Footage Storage
List, upload, and serve stored camera clips for playback in the dashboard.
"""

import os
import re
import json
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/footage", tags=["Footage"])

FOOTAGE_DIR = Path(settings.FOOTAGE_DIR)
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm", ".mov"}
SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _ensure_footage_dir():
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)


def _within_dir(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


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
    max_bytes = max(1, settings.MAX_UPLOAD_MB) * 1024 * 1024
    written = 0
    try:
        with path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(413, f"Upload exceeds {settings.MAX_UPLOAD_MB} MB limit")
                await asyncio.to_thread(out.write, chunk)
    except Exception as e:
        path.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(500, f"Upload failed: {e}")
    return {"filename": name, "camera_id": camera_id, "size": written}


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
    if not _within_dir(path, FOOTAGE_DIR) or not path.is_file():
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="video/mp4")


# ────────────────────────────────────────────────────────────────
# Detection Logs
# ────────────────────────────────────────────────────────────────

LOGS_DIR = Path("storage/logs")


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


@router.get("/logs/{log_filename}/analytics")
async def get_log_analytics(
    log_filename: str,
    bins: int = Query(4, ge=2, le=16, description="Heatmap grid size per axis"),
    min_track_frames: int = Query(30, ge=1, le=10000, description="Minimum frames for timeline inclusion"),
    current_user: User = Depends(get_current_user),
):
    """
    Generate retail analytics from a detection log:
    heatmap cells, per-track timelines, frame counts, travel distance, and fall candidates.
    Inspired by the retail-vision-analytics notebook, but served as an API response.
    """
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    frames = data.get("frames", []) or []
    track_points: Dict[str, List[Dict[str, Any]]] = {}
    counts_by_frame: List[Dict[str, Any]] = []
    max_x = 1.0
    max_y = 1.0
    fall_candidates: List[Dict[str, Any]] = []

    for frame in frames:
        frame_number = int(frame.get("frame_number") or 0)
        detections = frame.get("detections", []) or []
        counts_by_frame.append({
            "frame": frame_number,
            "detections": len(detections),
            "tracks": len({d.get("track_id") for d in detections if d.get("track_id") is not None}),
        })
        for det in detections:
            bbox = det.get("bbox") or []
            if len(bbox) < 4:
                continue
            x, y, w, h = [float(v) for v in bbox[:4]]
            cx = x + (w * 0.5)
            cy = y + (h * 0.5)
            max_x = max(max_x, x + w, cx)
            max_y = max(max_y, y + h, cy)
            tid = det.get("global_id") or det.get("track_id")
            if tid is None:
                continue
            key = str(tid)
            track_points.setdefault(key, []).append({
                "frame": frame_number,
                "x": cx,
                "y": cy,
                "width": w,
                "height": h,
                "class_name": det.get("class_name", "unknown"),
                "confidence": det.get("confidence", 0.0),
                "region": det.get("region"),
            })
            if w > h:
                fall_candidates.append({
                    "frame": frame_number,
                    "track_id": det.get("track_id"),
                    "global_id": det.get("global_id"),
                    "class_name": det.get("class_name", "unknown"),
                    "bbox": bbox,
                    "reason": "bbox_width_greater_than_height",
                })

    heatmap_counts = [[0 for _ in range(bins)] for _ in range(bins)]
    total_points = 0
    timelines: List[Dict[str, Any]] = []
    distances: List[Dict[str, Any]] = []

    for track_id, points in track_points.items():
        points.sort(key=lambda p: p["frame"])
        total_points += len(points)
        for point in points:
            col = min(bins - 1, max(0, int((point["x"] / max_x) * bins)))
            row = min(bins - 1, max(0, int((point["y"] / max_y) * bins)))
            heatmap_counts[row][col] += 1

        if len(points) >= min_track_frames:
            first = points[0]["frame"]
            last = points[-1]["frame"]
            timelines.append({
                "track_id": track_id,
                "class_name": points[0].get("class_name", "unknown"),
                "first_frame": first,
                "last_frame": last,
                "duration_frames": max(0, last - first),
                "sample_count": len(points),
                "region": points[0].get("region"),
            })

        distance = 0.0
        for prev, cur in zip(points, points[1:]):
            dx = cur["x"] - prev["x"]
            dy = cur["y"] - prev["y"]
            distance += (dx * dx + dy * dy) ** 0.5
        distances.append({
            "track_id": track_id,
            "class_name": points[0].get("class_name", "unknown"),
            "frames_observed": len(points),
            "distance_px": round(distance, 2),
        })

    heatmap = []
    for row_idx, row in enumerate(heatmap_counts):
        for col_idx, count in enumerate(row):
            heatmap.append({
                "row": row_idx,
                "col": col_idx,
                "count": count,
                "share": round(count / max(total_points, 1), 4),
            })

    timelines.sort(key=lambda t: (t["first_frame"], t["track_id"]))
    distances.sort(key=lambda d: d["distance_px"], reverse=True)

    return {
        "video_file": data.get("video_file"),
        "log_filename": log_filename,
        "frame_count": len(frames),
        "track_count": len(track_points),
        "heatmap": {
            "bins": bins,
            "points": total_points,
            "cells": heatmap,
        },
        "counts_by_frame": counts_by_frame,
        "timelines": timelines,
        "distances": distances[:100],
        "fall_candidates": fall_candidates[:100],
    }


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
    video_path = Path(video_file) if video_file else None
    if (
        not video_path
        or not video_path.is_file()
        or not _within_dir(video_path, FOOTAGE_DIR)
    ):
        raise HTTPException(404, f"Original video not found: {video_file}")

    # Collect frame numbers where this track_id appears
    target_frames = set()
    for frame in log_data.get("frames", []):
        for det in frame.get("detections", []):
            if det.get("track_id") == track_id:
                target_frames.add(frame.get("frame_number", 0))
                break

    if not target_frames:
        raise HTTPException(404, f"Track ID {track_id} not found in log")

    # Build continuous segments with padding
    sorted_frames = sorted(target_frames)
    segments = []
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

    # Open source video
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise HTTPException(500, f"Cannot open video: {video_file}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_vid_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Clamp segments to actual video length
    segments = [(max(1, s), min(e, total_vid_frames)) for s, e in segments]

    # Create output clip — try H.264 (avc1) first for browser playback,
    # fall back to mp4v if codec unavailable.
    _ensure_footage_dir()
    stem = video_path.stem
    out_name = f"{stem}_track{track_id}.mp4"
    out_path = FOOTAGE_DIR / out_name
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

    frames_written = 0
    for seg_start, seg_end in segments:
        # frame_number in logs is 1-indexed; OpenCV frame index is 0-indexed
        cap.set(cv2.CAP_PROP_POS_FRAMES, seg_start - 1)
        for frame_idx in range(seg_start, seg_end + 1):
            ret, frame = cap.read()
            if not ret:
                break
            writer.write(frame)
            frames_written += 1

    writer.release()
    cap.release()

    return {
        "trimmed_video": out_name,
        "path": str(out_path),
        "track_id": track_id,
        "segments": segments,
        "frames_written": frames_written,
        "original_video": video_file,
    }
