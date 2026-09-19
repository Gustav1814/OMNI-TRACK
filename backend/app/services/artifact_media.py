"""
OmniTrack AI — Artifact thumbnails
══════════════════════════════════

Clips need a poster frame: a <video> in a grid renders nothing until it has one,
and sixty of them would each open ranged requests just to draw a card.

Frames are extracted with OpenCV and cached in `.cache/thumbs/`, which sits in a
subdirectory so `artifact_index.scan` never sees it — that scan only looks at
files directly inside the root.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import cv2
from loguru import logger

_THUMB_MAX_EDGE = 320     # enough for a grid card, small enough to send 60 of
_THUMB_QUALITY = 78

# A single lock is fine at this scale, and keeps two simultaneous requests from
# extracting the same poster frame twice.
_lock = threading.Lock()


def _cache_dir(root: Path, kind: str) -> Path:
    d = root / ".cache" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbnail(root: Path, filename: str) -> Optional[Path]:
    """
    JPEG poster frame for a clip, cached. Returns None when the clip cannot be
    decoded — two of the existing clips are truncated (`moov atom not found`)
    because the process was killed while they were still open.
    """
    src = root / filename
    if not src.is_file():
        return None

    out = _cache_dir(root, "thumbs") / f"{filename}.jpg"
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out

    with _lock:
        if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
            return out
        try:
            cap = cv2.VideoCapture(str(src))
            if not cap.isOpened():
                return None
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            # A middle frame beats frame 0: the first frame of a segment is often
            # the moment the writer opened, before anything has entered the scene.
            if total > 2:
                cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return None

            h, w = frame.shape[:2]
            scale = min(_THUMB_MAX_EDGE / max(w, h), 1.0)
            if scale < 1.0:
                frame = cv2.resize(
                    frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )

            # imencode, not imwrite: imwrite picks its format from the file
            # EXTENSION, so writing to a ".tmp" path fails with "could not find
            # a writer for the specified extension". Encoding to bytes lets the
            # temp file be named whatever we like.
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _THUMB_QUALITY]
            )
            if not ok:
                return None
            tmp = out.with_name(out.name + ".part")
            tmp.write_bytes(buf.tobytes())
            os.replace(tmp, out)      # atomic, so a reader never sees a part-file
            return out
        except Exception as e:
            logger.debug(f"Thumbnail failed for {filename}: {e}")
            return None
