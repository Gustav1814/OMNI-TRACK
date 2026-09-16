"""
OmniTrack AI — On-disk job artifacts
═══════════════════════════════════════════════════════════════

Mirrors VisRax's `backend/shared/ais1` folder. Two artifact kinds, both named
with the same `~`-delimited convention so a filename alone tells you which job,
camera and moment it came from:

  Snapshot crop   kpis~{activity}~{cam}~{zone}~{class}~{date}~{time}~{frame}~{track}.png
  Annotated clip  kpis~{activity}~{cam}~{zone}~{date}~{time}~{start}_{end}.mp4

Snapshots are deduplicated per track (one per second by default) — without that
a 15 fps feed writes fifteen near-identical crops of the same car every second,
which is how VisRax's folder reached 19,000 files.

Clips roll every `segment_frames` frames, so a long-running job produces a set
of short reviewable segments rather than one unbounded file.
"""

import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

# Codecs tried in order when opening a segment writer, as VisRax does.
_SEGMENT_CODECS = ("mp4v", "avc1")
_DATE_FMT = "%Y_%m_%d"
_TIME_FMT = "%H_%M_%S"


def _safe(value: Any, fallback: str = "na") -> str:
    """Filenames use ~ as the delimiter, so it must not appear inside a field."""
    text = str(value if value not in (None, "") else fallback)
    return text.replace("~", "-").replace("/", "-").replace("\\", "-").replace(" ", "_")


class ArtifactStore:
    """Per-process writer for snapshots and annotated clips."""

    def __init__(
        self,
        root: str,
        segment_frames: int = 150,
        snapshot_dedup_seconds: float = 1.0,
        max_total_mb: int = 2048,
    ) -> None:
        self.root = Path(root)
        self.segment_frames = max(1, int(segment_frames))
        self.snapshot_dedup_seconds = snapshot_dedup_seconds
        self.max_total_mb = max_total_mb

        self.root.mkdir(parents=True, exist_ok=True)

        self._recent_snapshots: Dict[str, float] = {}
        # camera_id -> (writer, path, start_frame, size)
        self._segments: Dict[int, Tuple[Any, str, int, Tuple[int, int]]] = {}
        self._lock = threading.Lock()

    # ── snapshots ─────────────────────────────────────────────────
    def _should_snapshot(self, key: str) -> bool:
        now = time.time()
        last = self._recent_snapshots.get(key, 0.0)
        if now - last < self.snapshot_dedup_seconds:
            return False
        self._recent_snapshots[key] = now
        return True

    def save_snapshot(
        self,
        frame: np.ndarray,
        detection: Dict[str, Any],
        *,
        activity: str,
        camera_id: int,
        zone: str,
        frame_number: int,
        snapshot_classes: Optional[List[str]] = None,
    ) -> str:
        """
        Write a cropped detection. Returns the path, or "" when skipped
        (deduplicated, filtered out, or an empty crop).
        """
        try:
            class_name = str(detection.get("class_name", "unknown")).lower()
            if snapshot_classes and "all" not in snapshot_classes:
                if class_name not in {c.lower() for c in snapshot_classes}:
                    return ""

            track_id = detection.get("track_id")
            dedup_key = (
                f"{camera_id}~{activity}~{track_id}"
                if track_id is not None
                else f"{camera_id}~{activity}~{class_name}"
            )
            if not self._should_snapshot(dedup_key):
                return ""

            bbox = detection.get("bbox") or []
            if len(bbox) < 4:
                return ""
            x, y, w, h = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
            x, y = max(0, x), max(0, y)
            crop = frame[y : y + h, x : x + w]
            if crop.size == 0:
                return ""

            dt = datetime.now(timezone.utc)
            filename = (
                f"kpis~{_safe(activity)}~{_safe(camera_id)}~{_safe(zone)}~{_safe(class_name)}~"
                f"{dt:{_DATE_FMT}}~{dt:{_TIME_FMT}}~{frame_number}~{_safe(track_id, '-1')}.png"
            )
            path = self.root / filename
            cv2.imwrite(str(path), crop)
            return str(path)
        except Exception as e:
            logger.debug(f"Snapshot write failed (cam {camera_id}): {e}")
            return ""

    # ── annotated clips ───────────────────────────────────────────
    @staticmethod
    def _open_writer(path: str, fps: float, size: Tuple[int, int]):
        for codec in _SEGMENT_CODECS:
            try:
                writer = cv2.VideoWriter(
                    path, cv2.VideoWriter_fourcc(*codec), max(1.0, fps), size
                )
                if writer.isOpened():
                    return writer, codec
                writer.release()
            except Exception:
                continue
        return None, None

    def write_segment_frame(
        self,
        frame: np.ndarray,
        *,
        activity: str,
        camera_id: int,
        zone: str,
        frame_number: int,
        fps: float,
    ) -> Optional[str]:
        """
        Append an annotated frame to the camera's current clip, rolling to a new
        one every `segment_frames`. Returns the path of a clip that just closed.
        """
        closed_path: Optional[str] = None
        try:
            h, w = frame.shape[:2]
            size = (int(w), int(h))

            with self._lock:
                entry = self._segments.get(camera_id)
                needs_roll = (
                    entry is None
                    or entry[3] != size
                    or (frame_number - entry[2]) >= self.segment_frames
                )

                if needs_roll:
                    if entry is not None:
                        try:
                            entry[0].release()
                            closed_path = entry[1]
                        except Exception:
                            pass
                    dt = datetime.now(timezone.utc)
                    end_frame = frame_number + self.segment_frames
                    filename = (
                        f"kpis~{_safe(activity)}~{_safe(camera_id)}~{_safe(zone)}~"
                        f"{dt:{_DATE_FMT}}~{dt:{_TIME_FMT}}~{frame_number}_{end_frame}.mp4"
                    )
                    path = str(self.root / filename)
                    writer, codec = self._open_writer(path, fps, size)
                    if writer is None:
                        logger.warning(f"No usable codec; skipping clip for camera {camera_id}")
                        self._segments.pop(camera_id, None)
                        return closed_path
                    logger.info(f"Recording clip [{codec}]: {filename}")
                    self._segments[camera_id] = (writer, path, frame_number, size)
                    entry = self._segments[camera_id]

                entry[0].write(frame)
        except Exception as e:
            logger.debug(f"Segment write failed (cam {camera_id}): {e}")
        return closed_path

    def close_segment(self, camera_id: int) -> Optional[str]:
        with self._lock:
            entry = self._segments.pop(camera_id, None)
        if entry is None:
            return None
        try:
            entry[0].release()
            return entry[1]
        except Exception:
            return None

    def close_all(self) -> None:
        for camera_id in list(self._segments.keys()):
            self.close_segment(camera_id)

    # ── housekeeping ──────────────────────────────────────────────
    def usage_mb(self) -> float:
        total = 0
        try:
            for entry in os.scandir(self.root):
                if entry.is_file():
                    total += entry.stat().st_size
        except OSError:
            return 0.0
        return total / (1024 * 1024)

    def enforce_quota(self) -> int:
        """
        Delete oldest artifacts once the folder exceeds its budget. VisRax's
        folder grew to ~19k files unbounded; this keeps it from filling a disk.
        Returns how many files were removed.
        """
        try:
            if self.usage_mb() <= self.max_total_mb:
                return 0
            files = [
                (e.stat().st_mtime, e.path, e.stat().st_size)
                for e in os.scandir(self.root)
                if e.is_file()
            ]
            files.sort()
            budget_bytes = self.max_total_mb * 1024 * 1024
            total = sum(f[2] for f in files)
            removed = 0
            open_paths = {v[1] for v in self._segments.values()}
            for _mtime, path, size in files:
                if total <= budget_bytes:
                    break
                if path in open_paths:
                    continue
                try:
                    os.remove(path)
                    total -= size
                    removed += 1
                except OSError:
                    continue
            if removed:
                logger.info(f"Artifact quota: removed {removed} old files")
            return removed
        except Exception as e:
            logger.debug(f"Artifact quota check failed: {e}")
            return 0

    def stats(self) -> Dict[str, Any]:
        snapshots = clips = 0
        try:
            for entry in os.scandir(self.root):
                if not entry.is_file():
                    continue
                if entry.name.endswith(".png"):
                    snapshots += 1
                elif entry.name.endswith(".mp4"):
                    clips += 1
        except OSError:
            pass
        return {
            "root": str(self.root),
            "snapshots": snapshots,
            "clips": clips,
            "usage_mb": round(self.usage_mb(), 1),
            "quota_mb": self.max_total_mb,
            "free_disk_mb": round(shutil.disk_usage(self.root).free / (1024 * 1024), 1),
        }
