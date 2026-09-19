"""
OmniTrack AI — Artifact serving endpoints
═════════════════════════════════════════

Serves the snapshots and clips in `shared/ais1`, which until now nothing could read:
there was no static mount, no file route and no UI, and the only consumer was a
counter (`GET /jobs/artifacts`).

Backed by `app.services.artifact_index`, which parses the filenames rather than
maintaining a table — so nothing here changes how artifacts are written, and every
file already on disk is queryable immediately.

  GET /api/artifacts             filtered listing
  GET /api/artifacts/facets      distinct values, for building filter controls
  GET /api/artifacts/file/{name} the file itself
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import resolved_artifacts_dir
from app.database import get_db
from app.models.job import JobRun
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services import artifact_index, artifact_media
from app.services.artifact_index import ArtifactMeta

router = APIRouter(prefix="/api/artifacts", tags=["Artifacts"])

MAX_LIMIT = 1000
DEFAULT_LIMIT = 100

# Media types we are willing to serve. The index only ever yields .png and .mp4,
# but being explicit keeps the file route from becoming a general file server if
# the naming convention is ever widened.
_MEDIA_TYPES = {".png": "image/png", ".mp4": "video/mp4"}


# ── job attribution ────────────────────────────────────────────────────

async def _load_job_runs(db: AsyncSession) -> List[JobRun]:
    result = await db.execute(select(JobRun).order_by(JobRun.started_at.desc()))
    return list(result.scalars().all())


def _attribute(meta: ArtifactMeta, jobs: List[JobRun]) -> Dict[str, Any]:
    """
    Match an artifact to the job that produced it.

    Artifacts carry camera, zone and a UTC timestamp in their filename; `job_runs`
    carries the same camera and zone plus a start/end window. That is enough to join
    the two without having written a job_id into the file. A running job has
    ended_at NULL, which is treated as open-ended.

    Overlapping jobs on one camera+zone are genuinely ambiguous, so the most recent
    start wins and `job_ambiguous` is set rather than presenting a guess as fact.
    """
    matches = []
    for job in jobs:
        if job.camera_id != meta.camera_id:
            continue
        if (job.zone or "") != meta.zone:
            continue
        started = job.started_at
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if meta.captured_at < started:
            continue
        ended = job.ended_at
        if ended is not None:
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            if meta.captured_at > ended:
                continue
        matches.append(job)

    if not matches:
        return {"job_id": None, "job_ambiguous": False}
    # _load_job_runs already sorts by started_at desc, so matches[0] is the latest.
    return {"job_id": matches[0].job_id, "job_ambiguous": len(matches) > 1}


def _serialise(meta: ArtifactMeta, jobs: List[JobRun]) -> Dict[str, Any]:
    d = meta.to_dict()
    d["url"] = f"/api/artifacts/file/{meta.filename}"
    # Clips need a poster: the stored encoding is FMP4, which no browser can
    # decode, so a <video> shows an empty black box until it has a thumbnail.
    d["thumb_url"] = (
        f"/api/artifacts/thumb/{meta.filename}" if meta.kind == "clip" else d["url"]
    )
    d.update(_attribute(meta, jobs))
    return d


# ── endpoints ──────────────────────────────────────────────────────────

@router.get("", summary="List artifacts")
@router.get("/", include_in_schema=False)
async def list_artifacts(
    kind: str = Query("all", pattern="^(all|image|clip)$"),
    camera_id: Optional[int] = None,
    zone: Optional[str] = Query(None, description="Location id the job ran under"),
    activity: Optional[str] = Query(None, description="line_passing | roi_region | general_object_detection"),
    class_name: Optional[str] = Query(None, description="Detected class, e.g. fire, product, person"),
    track_id: Optional[int] = Query(None, description="-1 matches untracked detections"),
    job_id: Optional[str] = Query(None, description="Resolved by camera+zone+time window"),
    start: Optional[datetime] = Query(None, description="Inclusive lower bound (UTC)"),
    end: Optional[datetime] = Query(None, description="Exclusive upper bound (UTC)"),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Filtered listing, newest first.

    Filtering happens on parsed filenames, so it is exact for camera, zone, activity,
    class, track and time. `job_id` is resolved after the fact from `job_runs`, since
    the job that produced a file is not recorded in the file's name.
    """
    if limit < 1:
        raise HTTPException(400, "limit must be >= 1")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")
    limit = min(limit, MAX_LIMIT)

    items = artifact_index.scan(resolved_artifacts_dir())
    items = artifact_index.filter_artifacts(
        items,
        kind=kind, camera_id=camera_id, zone=zone, activity=activity,
        class_name=class_name, track_id=track_id, start=start, end=end,
    )

    jobs = await _load_job_runs(db)

    if job_id:
        # Narrow to the requested job's camera/zone/time windows first, so we do not
        # attribute every artifact on disk just to throw most of them away.
        windows = [j for j in jobs if j.job_id == job_id]
        if not windows:
            raise HTTPException(404, f"No job run found with job_id '{job_id}'")
        items = [m for m in items if _attribute(m, windows)["job_id"] == job_id]

    total = len(items)
    page = items[offset : offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_serialise(m, jobs) for m in page],
    }


@router.get("/facets", summary="Distinct filter values")
async def artifact_facets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Everything a filter UI needs to populate its controls, taken from what is
    actually on disk rather than a hardcoded list.
    """
    items = artifact_index.scan(resolved_artifacts_dir())
    data = artifact_index.facets(items)
    jobs = await _load_job_runs(db)
    data["jobs"] = [
        {
            "job_id": j.job_id,
            "camera_id": j.camera_id,
            "zone": j.zone,
            "activity_type": j.activity_type,
            "model": j.model,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "ended_at": j.ended_at.isoformat() if j.ended_at else None,
            "is_active": bool(j.is_active),
        }
        for j in jobs
    ]
    data["root"] = str(resolved_artifacts_dir())
    return data


@router.get("/file/{filename}", summary="Fetch one artifact")
async def get_artifact_file(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """
    Serve a single artifact.

    `filename` is user input, so it is validated by *parsing* — anything that is not
    a well-formed artifact name is rejected before touching the filesystem, which
    also excludes separators and traversal sequences. The resolved path is then
    checked to be a direct child of the artifacts root, so a symlink cannot be used
    to escape either.
    """
    meta = artifact_index.parse_artifact_name(filename)
    if meta is None:
        raise HTTPException(400, "Not a valid artifact filename")

    root = resolved_artifacts_dir()
    path = (root / meta.filename).resolve()
    if path.parent != root.resolve():
        raise HTTPException(400, "Resolved path is outside the artifacts directory")
    if not path.is_file():
        raise HTTPException(404, "Artifact not found")

    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise HTTPException(400, "Unsupported artifact type")

    headers = {"Content-Disposition": f'inline; filename="{meta.filename}"'}
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/thumb/{filename}", summary="Poster frame for a clip")
async def get_artifact_thumb(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """
    A JPEG poster frame, extracted from the clip with OpenCV.

    OpenCV decodes FMP4 happily even though browsers do not, so this works for
    every clip already on disk without transcoding anything. For an image
    artifact the original is returned, so callers can use one URL for both.
    """
    meta = artifact_index.parse_artifact_name(filename)
    if meta is None:
        raise HTTPException(400, "Not a valid artifact filename")

    root = resolved_artifacts_dir()
    if meta.kind == "image":
        path = (root / meta.filename).resolve()
        if path.parent != root.resolve() or not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, media_type="image/png")

    thumb = artifact_media.thumbnail(root, meta.filename)
    if thumb is None:
        # Two of the stored clips are truncated (no moov atom) because the
        # process was killed while they were still open.
        raise HTTPException(404, "No poster frame could be decoded from this clip")
    return FileResponse(
        thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"},
    )
