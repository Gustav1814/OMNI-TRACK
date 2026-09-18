"""
OmniTrack AI — Artifact index (read-only)
═════════════════════════════════════════

Makes the files in `shared/ais1` queryable without touching how they are written.

`ArtifactStore` already encodes every dimension anyone needs to filter on into the
filename itself, so this module is a parser over `os.scandir` rather than a database
table:

    kpis~{activity}~{cam}~{zone}~{class}~{date}~{time}~{frame}~{track}.png
    kpis~{activity}~{cam}~{zone}~{date}~{time}~{start}_{end}.mp4

Keeping it read-only is deliberate. An index table would need a write in
`ArtifactStore.save_snapshot`, which would (a) change the save path we agreed not to
touch and (b) leave the ~24k files already on disk unindexed. Parsing filenames works
retroactively on everything that is already there.

Cost at the current scale is negligible — a full scan of 24k entries is a few tens of
milliseconds — and the result is cached until the directory changes.

KNOWN LOSSY FIELD
-----------------
`ArtifactStore._safe()` rewrites `~`, `/`, `\\` to `-` and spaces to `_` before
building a filename, and that is not reversible. A zone literally named "a~b" is
stored as "a-b" and parses back as "a-b". Filters therefore match the sanitised form,
which is what the UI shows anyway.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

# Must stay in step with ArtifactStore (_DATE_FMT / _TIME_FMT in artifacts.py).
_DATE_FMT = "%Y_%m_%d"
_TIME_FMT = "%H_%M_%S"

_PREFIX = "kpis"
_SNAPSHOT_FIELDS = 9   # kpis~activity~cam~zone~class~date~time~frame~track
_CLIP_FIELDS = 7       # kpis~activity~cam~zone~date~time~start_end

# How long a scan is trusted when the directory mtime has not moved. Windows does
# not always bump directory mtime promptly, so the TTL is the real safety net.
_CACHE_TTL_S = 5.0


@dataclass(frozen=True)
class ArtifactMeta:
    """One file on disk, described by its own name."""

    filename: str
    kind: str                      # "image" | "clip"
    activity: str
    camera_id: int
    zone: str
    captured_at: datetime          # UTC, from the filename not the mtime
    size_bytes: int
    class_name: Optional[str] = None    # images only
    track_id: Optional[int] = None      # images only; -1 means untracked
    frame_number: Optional[int] = None  # image frame, or a clip's start frame
    end_frame: Optional[int] = None     # clips only

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["captured_at"] = self.captured_at.isoformat()
        return d


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_artifact_name(filename: str, size_bytes: int = 0) -> Optional[ArtifactMeta]:
    """
    Decode an artifact filename. Returns None for anything that does not match the
    convention — which is also what makes this safe to use as a path validator:
    a name containing separators or traversal cannot parse into the expected
    field count with a valid timestamp.
    """
    name = str(filename or "")
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in (".png", ".mp4"):
        return None
    if "/" in name or "\\" in name or os.path.sep in name:
        return None

    parts = stem.split("~")
    if not parts or parts[0] != _PREFIX:
        return None

    try:
        if ext == ".png":
            if len(parts) != _SNAPSHOT_FIELDS:
                return None
            _, activity, cam, zone, class_name, date_s, time_s, frame_s, track_s = parts
            end_frame = None
            frame_number = _parse_int(frame_s)
            track_id = _parse_int(track_s)
            kind = "image"
        else:
            if len(parts) != _CLIP_FIELDS:
                return None
            _, activity, cam, zone, date_s, time_s, span = parts
            class_name = None
            track_id = None
            start_s, _, end_s = span.partition("_")
            frame_number = _parse_int(start_s)
            end_frame = _parse_int(end_s)
            kind = "clip"

        camera_id = _parse_int(cam)
        if camera_id is None:
            return None

        captured_at = datetime.strptime(
            f"{date_s} {time_s}", f"{_DATE_FMT} {_TIME_FMT}"
        ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    return ArtifactMeta(
        filename=name,
        kind=kind,
        activity=activity,
        camera_id=camera_id,
        zone=zone,
        class_name=class_name,
        track_id=track_id,
        frame_number=frame_number,
        end_frame=end_frame,
        captured_at=captured_at,
        size_bytes=int(size_bytes or 0),
    )


class _ScanCache:
    """Directory scan memoised on (mtime, wall clock)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, Any] = {}

    def get(self, root: Path) -> List[ArtifactMeta]:
        key = str(root)
        try:
            dir_mtime = root.stat().st_mtime
        except OSError:
            return []

        now = time.time()
        with self._lock:
            cached = self._entries.get(key)
            if cached and cached["mtime"] == dir_mtime and (now - cached["at"]) < _CACHE_TTL_S:
                return cached["items"]

        items = _scan_uncached(root)

        with self._lock:
            self._entries[key] = {"mtime": dir_mtime, "at": now, "items": items}
        return items

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _ScanCache()


def _scan_uncached(root: Path) -> List[ArtifactMeta]:
    items: List[ArtifactMeta] = []
    skipped = 0
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                meta = parse_artifact_name(entry.name, size)
                if meta is None:
                    skipped += 1
                    continue
                items.append(meta)
    except OSError as e:
        logger.debug(f"Artifact scan failed for {root}: {e}")
        return []

    if skipped:
        logger.debug(f"Artifact scan: {skipped} file(s) did not match the naming convention")
    items.sort(key=lambda m: m.captured_at, reverse=True)
    return items


def scan(root: Path) -> List[ArtifactMeta]:
    """All parseable artifacts under `root`, newest first. Cached."""
    return _CACHE.get(Path(root))


def invalidate_cache() -> None:
    _CACHE.invalidate()


def filter_artifacts(
    items: Iterable[ArtifactMeta],
    *,
    kind: Optional[str] = None,
    camera_id: Optional[int] = None,
    zone: Optional[str] = None,
    activity: Optional[str] = None,
    class_name: Optional[str] = None,
    track_id: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[ArtifactMeta]:
    """
    Apply the filter set. String comparisons are case-insensitive because class and
    zone names reach the filename in whatever case the model or the operator used.
    `start` is inclusive, `end` exclusive.
    """
    zone_l = zone.lower() if zone else None
    activity_l = activity.lower() if activity else None
    class_l = class_name.lower() if class_name else None

    out: List[ArtifactMeta] = []
    for m in items:
        if kind and kind != "all" and m.kind != kind:
            continue
        if camera_id is not None and m.camera_id != camera_id:
            continue
        if zone_l and m.zone.lower() != zone_l:
            continue
        if activity_l and m.activity.lower() != activity_l:
            continue
        if class_l and (m.class_name or "").lower() != class_l:
            continue
        if track_id is not None and m.track_id != track_id:
            continue
        if start is not None and m.captured_at < _as_utc(start):
            continue
        if end is not None and m.captured_at >= _as_utc(end):
            continue
        out.append(m)
    return out


def _as_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC — filenames are UTC, so a naive bound is too."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def facets(items: Iterable[ArtifactMeta]) -> Dict[str, Any]:
    """Distinct values present on disk, for populating filter controls."""
    cameras, zones, activities, classes = set(), set(), set(), set()
    kinds: Dict[str, int] = {}
    earliest: Optional[datetime] = None
    latest: Optional[datetime] = None
    total = 0
    total_bytes = 0

    for m in items:
        total += 1
        total_bytes += m.size_bytes
        cameras.add(m.camera_id)
        zones.add(m.zone)
        activities.add(m.activity)
        if m.class_name:
            classes.add(m.class_name)
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
        if earliest is None or m.captured_at < earliest:
            earliest = m.captured_at
        if latest is None or m.captured_at > latest:
            latest = m.captured_at

    return {
        "total": total,
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "kinds": kinds,
        "cameras": sorted(cameras),
        "zones": sorted(zones),
        "activities": sorted(activities),
        "classes": sorted(classes),
        "earliest": earliest.isoformat() if earliest else None,
        "latest": latest.isoformat() if latest else None,
    }
