"""
OmniTrack AI — CCTV Footage Storage
List, upload, and serve stored camera clips for playback in the dashboard.
"""

import os
import re
import time
from pathlib import Path
from typing import List, Optional

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
