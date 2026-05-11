"""
OmniTrack AI — CCTV Footage Storage
List, upload, and serve stored camera clips for playback in the dashboard.
"""

import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

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
    """Unique track_ids in a log. first_frame / last_frame are 1-based indices in log order (same as the saved video)."""
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tracks = {}
    for i, frame in enumerate(data.get("frames") or []):
        file_idx = i + 1
        for det in frame.get("detections", []):
            tid = det.get("track_id")
            if tid is None:
                continue
            if tid not in tracks:
                tracks[tid] = {
                    "track_id": tid,
                    "class_name": det.get("class_name", "unknown"),
                    "global_id": det.get("global_id"),
                    "first_frame": file_idx,
                    "last_frame": file_idx,
                    "frame_count": 0,
                }
            tracks[tid]["last_frame"] = max(tracks[tid]["last_frame"], file_idx)
            tracks[tid]["first_frame"] = min(tracks[tid]["first_frame"], file_idx)
            tracks[tid]["frame_count"] += 1
            if det.get("global_id") and not tracks[tid]["global_id"]:
                tracks[tid]["global_id"] = det["global_id"]
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


def _merge_hit_clusters(sorted_unique_hits: List[int], padding_frames: int) -> List[Tuple[int, int]]:
    """
    Group 1-based log hit indices into clusters. Two hits belong to the same cluster when
    the gap between them is <= 2*padding+1 (same bridge as the legacy trim merge, expressed on hits).
    """
    if not sorted_unique_hits:
        return []
    p = max(0, int(padding_frames))
    gap_merge = 2 * p + 1
    a = b = sorted_unique_hits[0]
    clusters: List[Tuple[int, int]] = []
    for x in sorted_unique_hits[1:]:
        if x - b <= gap_merge:
            b = x
        else:
            clusters.append((a, b))
            a = b = x
    clusters.append((a, b))
    return clusters


def _merge_adjacent_intervals(segments: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge sorted inclusive [s,e] intervals that touch or overlap."""
    if not segments:
        return []
    segs = sorted((int(s), int(e)) for s, e in segments if int(e) >= int(s))
    out: List[Tuple[int, int]] = [segs[0]]
    for s, e in segs[1:]:
        ps, pe = out[-1]
        if s <= pe + 1:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _segments_from_frames(
    frames: List[int],
    padding_frames: int,
    max_log_frame: Optional[int] = None,
) -> List[tuple]:
    """
    Build inclusive (start,end) segments in log / file 1-based frame space:
      1) merge hit frames that are within (2*padding+1) of each other into clusters
      2) expand each cluster by padding_frames before the first hit and after the last hit
      3) clamp ends to max_log_frame (len(log frames)) when given so padding never exceeds the log
      4) merge overlapping or touching segments after expansion
    """
    if not frames:
        return []
    hits = sorted({int(x) for x in frames if int(x) >= 1})
    if not hits:
        return []
    p = max(0, int(padding_frames))
    clusters = _merge_hit_clusters(hits, p)
    hi = int(max_log_frame) if (max_log_frame is not None and int(max_log_frame) > 0) else None

    raw: List[Tuple[int, int]] = []
    for a, b in clusters:
        s = max(1, a - p)
        e = b + p
        if hi is not None:
            e = min(e, hi)
        if e >= s:
            raw.append((s, e))
    return _merge_adjacent_intervals(raw)


def _file_frame_indices_for_track(log_frames: List[dict], track_id: int) -> List[int]:
    """
    1-based indices into the stored video, in log order.

    Logs store pipeline `frame_number` (monotonic per camera across the whole session).
    The recorded file only contains frames from recording start, so `frame_number` often
    does not match OpenCV's frame index. Ordinal position in `frames[]` matches the file.
    """
    out: List[int] = []
    for i, frame in enumerate(log_frames):
        for det in frame.get("detections", []):
            if _det_track_id_equals(det.get("track_id"), track_id):
                out.append(i + 1)
                break
    return out


def _file_frame_indices_for_global_id(log_frames: List[dict], global_id: str) -> List[int]:
    out: List[int] = []
    for i, frame in enumerate(log_frames):
        for det in frame.get("detections", []):
            if _global_id_matches(det.get("global_id"), global_id):
                out.append(i + 1)
                break
    return out


def _get_ffmpeg_exe() -> Optional[str]:
    """System ffmpeg preferred; else imageio-ffmpeg wheel (no separate install)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _clamp_segments_to_video(segments: List[tuple], total_frames: int) -> List[Tuple[int, int]]:
    """Clamp 1-based inclusive (start,end) segments to valid video frame numbers."""
    out: List[Tuple[int, int]] = []
    for s, e in segments:
        s2 = max(1, int(s))
        e2 = int(e)
        if total_frames > 0:
            e2 = min(e2, total_frames)
        if e2 >= s2:
            out.append((s2, e2))
    return out


def _ffmpeg_trim_h264(ffmpeg_exe: str, video_file: Path, clamped: List[Tuple[int, int]], out_path: Path) -> int:
    """
    Build a browser-safe MP4 (H.264 yuv420p + faststart) from disjoint frame ranges.
    Frame indices are 1-based positions in the log / saved video (same convention as /tracks).
    """
    select_parts: List[str] = []
    expected = 0
    for s, e in clamped:
        if e < s:
            continue
        a = s - 1
        b = e - 1
        if b < a:
            continue
        select_parts.append(f"between(n\\,{a}\\,{b})")
        expected += e - s + 1
    if not select_parts or expected <= 0:
        raise HTTPException(500, "No valid frame ranges to export after clamping")

    inner = "+".join(select_parts)
    vf = f"select='{inner}',setpts=PTS-STARTPTS"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_file.resolve()),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23",
        "-preset",
        "fast",
        "-movflags",
        "+faststart",
        str(out_path.resolve()),
    ]
    kwargs: dict = {}
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Trim operation timed out")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise HTTPException(500, f"ffmpeg could not build the clip: {msg[:1200]}")
    if not out_path.is_file() or out_path.stat().st_size < 64:
        raise HTTPException(500, "Trim produced an empty or unreadable file")
    return expected


def _write_segments_via_opencv_fallback(
    video_file: Path, clamped: List[Tuple[int, int]], out_path: Path, fps: float, w: int, h: int
) -> int:
    """
    Last resort when ffmpeg is unavailable: write with OpenCV (often mp4v / partial MP4).
    May not play in all browsers; prefer installing ffmpeg or imageio-ffmpeg.
    """
    src = str(video_file.resolve())
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise HTTPException(500, f"Cannot open video: {src}")
    writer: Optional[cv2.VideoWriter] = None
    for codec in ("avc1", "H264", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*codec)
        wrt = cv2.VideoWriter(str(out_path), fourcc, float(fps or 15), (w, h))
        if wrt.isOpened():
            writer = wrt
            break
        wrt.release()
    if writer is None or not writer.isOpened():
        cap.release()
        raise HTTPException(500, "Failed to create output video (no ffmpeg and OpenCV writer failed)")
    written = 0
    try:
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
    finally:
        writer.release()
        cap.release()
    return written


def _write_segments_to_clip(
    video_file: Path,
    segments: List[tuple],
    out_path: Path,
    *,
    log_frame_count: Optional[int] = None,
) -> int:
    src = str(video_file.resolve())
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise HTTPException(500, f"Cannot open video: {src}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 15)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    cap_total = total
    if log_frame_count is not None and int(log_frame_count) > 0:
        lf = int(log_frame_count)
        if cap_total > 0:
            cap_total = min(cap_total, lf)
        else:
            cap_total = lf

    if cap_total > 0:
        clamped = _clamp_segments_to_video(segments, cap_total)
    else:
        clamped = [(max(1, int(s)), max(1, int(e))) for s, e in segments if int(e) >= max(1, int(s))]
    # OpenCV often under-reports MP4 frame counts; if clamp wiped everything, widen bound.
    if not clamped and segments:
        max_need = max(max(int(s), int(e)) for s, e in segments)
        if cap_total > 0 and max_need > cap_total:
            clamped = _clamp_segments_to_video(segments, max_need)
        if not clamped:
            clamped = [(max(1, int(s)), max(1, int(e))) for s, e in segments if int(e) >= max(1, int(s))]
    if not clamped:
        raise HTTPException(
            500,
            "No frames fall within the video duration (log vs file length mismatch).",
        )

    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe:
        return _ffmpeg_trim_h264(ffmpeg_exe, video_file, clamped, out_path)
    return _write_segments_via_opencv_fallback(video_file, clamped, out_path, fps, w, h)


@router.get("/logs/{log_filename}/global-ids")
async def get_log_global_ids(
    log_filename: str,
    current_user: User = Depends(get_current_user),
):
    """Unique global_ids in a single log. first_frame / last_frame are 1-based indices in log order (same as the saved video)."""
    log_filename = os.path.basename(log_filename)
    path = LOGS_DIR / log_filename
    if not path.is_file():
        raise HTTPException(404, "Log file not found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    gids: dict = {}
    for i, frame in enumerate(data.get("frames") or []):
        file_idx = i + 1
        for det in frame.get("detections", []):
            gid = det.get("global_id")
            if not gid:
                continue
            entry = gids.setdefault(gid, {
                "global_id": gid,
                "class_name": det.get("class_name", "unknown"),
                "first_frame": file_idx,
                "last_frame": file_idx,
                "frame_count": 0,
                "track_ids": set(),
            })
            entry["first_frame"] = min(entry["first_frame"], file_idx)
            entry["last_frame"] = max(entry["last_frame"], file_idx)
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
        frames_list = data.get("frames") or []
        target_frames = _file_frame_indices_for_global_id(frames_list, global_id)
        if not target_frames:
            continue
        segments = _segments_from_frames(target_frames, padding_frames, len(frames_list))
        stem = video_path.stem
        out_name = f"{stem}_{re.sub(r'[^a-zA-Z0-9_-]', '_', global_id)}.mp4"
        out_path = FOOTAGE_DIR / out_name
        try:
            written = _write_segments_to_clip(
                video_path, segments, out_path, log_frame_count=len(frames_list)
            )
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

    log_frames = log_data.get("frames") or []
    target_frames = _file_frame_indices_for_track(log_frames, track_id)
    if not target_frames:
        raise HTTPException(404, f"Track ID {track_id} not found in log")

    segments = _segments_from_frames(target_frames, padding_frames, len(log_frames))
    _ensure_footage_dir()
    stem = video_path.stem
    out_name = f"{stem}_track{track_id}.mp4"
    out_path = FOOTAGE_DIR / out_name
    frames_written = _write_segments_to_clip(
        video_path, segments, out_path, log_frame_count=len(log_frames)
    )

    return {
        "trimmed_video": out_name,
        "path": str(out_path),
        "track_id": track_id,
        "segments": segments,
        "frames_written": frames_written,
        "original_video": str(video_path),
    }
