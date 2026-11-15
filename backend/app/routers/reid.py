"""
OmniTrack AI — Re-ID Router
Person search, journeys, cross-camera identities, camera topology and calibration.

Live data comes from the pipeline's track coordinator (recent identities in
memory); older identities come from ``reid_identities`` (pgvector HNSW). When
there is nothing to report the endpoints return empty results with
``X-Data-Status: no_data`` (list endpoints) or ``"status": "no_data"`` — they
never invent identities.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.schemas import CustomerJourneyResponse, ReIDMatch
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, require_role
from app.security.route_policy import authenticated_action
from app.services.crud import AnalyticsService, AuditService, ReIDConfigService, ReIDIdentityService
from app.services.track_coordinator import parse_links

router = APIRouter(prefix="/api/reid", tags=["Re-Identification"])

# A global ID counts as "active" if its tracklet was updated this recently.
ACTIVE_WINDOW_SECONDS = 10.0


class ReIDSearchRequest(BaseModel):
    global_id: str = Field(..., min_length=1, max_length=100)
    top_k: int = Field(10, ge=1, le=100)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="min cosine similarity; default REID_SIMILARITY_THRESHOLD")


class TopologyLink(BaseModel):
    from_camera: int = Field(..., ge=1, alias="from")
    to_camera: int = Field(..., ge=1, alias="to")
    min_s: float = Field(0.0, ge=0.0, le=86400.0)
    max_s: float = Field(..., gt=0.0, le=86400.0)
    overlapping: bool = False
    bidirectional: bool = True

    model_config = {"populate_by_name": True}


class TopologyBody(BaseModel):
    links: List[TopologyLink] = Field(default_factory=list, max_length=500)


class CalibrationPoint(BaseModel):
    image: List[float] = Field(..., min_length=2, max_length=2, description="Point in the camera image, 0..1 of width/height")
    floor: List[float] = Field(..., min_length=2, max_length=2, description="Same point on the floor plan, metres")

    @field_validator("image")
    @classmethod
    def _unit(cls, v: List[float]) -> List[float]:
        if not all(0.0 <= c <= 1.0 for c in v):
            raise ValueError("image coordinates must be within 0..1")
        return v

    @field_validator("floor")
    @classmethod
    def _metres(cls, v: List[float]) -> List[float]:
        if not all(-1000.0 <= c <= 1000.0 for c in v):
            raise ValueError("floor coordinates must be within ±1000 m")
        return v


class CalibrationBody(BaseModel):
    """Either a ready 3x3 homography, or >= 4 clicked point pairs plus the frame size they were clicked on."""

    homography: Optional[List[List[float]]] = Field(None, description="3x3 image (pixels) -> floor (metres) homography")
    points: Optional[List[CalibrationPoint]] = Field(None, min_length=4, max_length=32)
    frame_wh: Optional[List[int]] = Field(None, min_length=2, max_length=2, description="Camera frame width, height in pixels")

    @field_validator("homography")
    @classmethod
    def _shape(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        if v is not None and (len(v) != 3 or any(len(row) != 3 for row in v)):
            raise ValueError("homography must be 3x3")
        return v

    @field_validator("frame_wh")
    @classmethod
    def _size(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None and not all(16 <= c <= 16384 for c in v):
            raise ValueError("frame_wh must be a real frame size in pixels")
        return v


# Floor points closer than this (metres) are treated as the same spot; a fit needs a real quadrilateral.
_MIN_FLOOR_SPREAD_M = 0.5


def fit_floor_homography(points: List[CalibrationPoint], frame_wh: List[int]) -> tuple:
    """Least-squares image->floor homography from clicked pairs. Returns (H as 3x3 list, RMSE in metres or None).

    With exactly 4 points the fit is exact, so there is no error to report (None). Rejects
    collinear or bunched-up points, because those give a homography that is wrong away from them.
    """
    import cv2
    import numpy as np

    w, h = frame_wh
    src = np.array([[p.image[0] * w, p.image[1] * h] for p in points], np.float64)
    dst = np.array([p.floor for p in points], np.float64)
    for pts, unit, minimum in ((src, "image", 8.0), (dst, "floor", _MIN_FLOOR_SPREAD_M)):
        d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)
        if (d[np.triu_indices(len(pts), 1)] < minimum).any():
            raise ValueError(f"Two {unit} points are on the same spot; spread the points out")
        centred = pts - pts.mean(axis=0)
        s = np.linalg.svd(centred, compute_uv=False)
        if s[-1] < 1e-3 * max(s[0], 1e-9) or s[-1] / np.sqrt(len(pts)) < minimum / 4:
            raise ValueError(f"The {unit} points lie on a line; use points that span an area of the floor")
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None or not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-12:
        raise ValueError("These points do not define a floor plane; check that each image point matches its floor point")
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    if not np.isfinite(proj).all():
        raise ValueError("The fitted plane sends a clicked point to infinity; check the point order")
    rmse = float(np.sqrt(np.mean(np.sum((proj - dst) ** 2, axis=1)))) if len(points) > 4 else None
    return H.tolist(), rmse


def _pipeline(request: Request):
    return getattr(request.app.state, "pipeline", None)


def _coordinator(request: Request):
    return getattr(_pipeline(request), "coordinator", None)


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch or 0.0), tz=timezone.utc)


def _no_data(response: Response) -> None:
    response.headers["X-Data-Status"] = "no_data"


# ── search / journeys / active ──────────────────────────────────────

@router.post("/search", response_model=List[ReIDMatch])
@authenticated_action
async def search_person(
    body: ReIDSearchRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Identities that look like ``global_id`` (cosine similarity on OSNet-AIN features).

    Searches the live gallery first; identities no longer in memory are searched
    against the persisted centroids (pgvector HNSW)."""
    threshold = settings.REID_SIMILARITY_THRESHOLD if body.threshold is None else body.threshold
    coord = _coordinator(request)
    hits = coord.similar_to(body.global_id, top_k=body.top_k, threshold=threshold) if coord else None
    matches: List[ReIDMatch] = []
    if hits is not None:
        for h in hits:
            if h.get("last_camera_id") is None:
                continue
            matches.append(ReIDMatch(
                global_id=str(h["global_id"]),
                camera_id=int(h["last_camera_id"]),
                confidence=round(float(h["similarity"]), 4),
                timestamp=_ts(h["last_seen"]),
                bbox=h.get("last_bbox"),
            ))
    else:
        row = (await db.execute(
            text("SELECT centroid FROM reid_identities WHERE global_id = :g AND model_version = :mv"),
            {"g": body.global_id, "mv": settings.reid_model_version},
        )).first()
        if row is not None:
            for h in await ReIDIdentityService.search(db, list(row[0]), top_k=body.top_k + 1, threshold=threshold):
                if h["global_id"] == body.global_id or h.get("last_camera_id") is None:
                    continue
                matches.append(ReIDMatch(
                    global_id=h["global_id"],
                    camera_id=int(h["last_camera_id"]),
                    confidence=round(float(h["similarity"]), 4),
                    timestamp=h["last_seen"],
                ))
    if not matches:
        _no_data(response)
    return matches[: body.top_k]


@router.get("/thumb/{global_id}")
async def person_thumbnail(
    global_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Sharpest crop of this identity that passed the Re-ID quality gate (JPEG, kept in memory only).

    404 when no crop is held, e.g. the identity was restored from the database after a restart."""
    pipe = _pipeline(request)
    thumb = pipe.person_thumb(global_id) if pipe is not None and hasattr(pipe, "person_thumb") else None
    if thumb is None:
        raise HTTPException(status_code=404, detail="No snapshot of this person in memory")
    jpeg, cam_id, ts = thumb
    return Response(content=jpeg, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store",
        "X-Camera-Id": str(cam_id),
        "X-Captured-At": _ts(ts).isoformat(),
    })


@router.get("/journey/{global_id}", response_model=CustomerJourneyResponse)
async def get_person_journey(
    global_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A person's journey: persisted legs plus the legs still open in this process
    (a leg is only written once the person has been unseen for 90 s)."""
    legs = await AnalyticsService.get_journey(db, global_id)
    # (camera_id, zone, start, end, in_progress)
    spans = [
        (leg.camera_id, leg.zone, leg.entry_time,
         leg.exit_time or (datetime.fromtimestamp(leg.entry_time.timestamp() + float(leg.dwell_time or 0.0), tz=timezone.utc)
                           if leg.entry_time else None), False)
        for leg in legs
    ]
    persistence = getattr(request.app.state, "persistence", None)
    if persistence is not None and hasattr(persistence, "active_legs"):
        for leg in persistence.active_legs(global_id):
            spans.append((leg["camera_id"], leg["zone"],
                          datetime.fromtimestamp(leg["started"], tz=timezone.utc),
                          datetime.fromtimestamp(leg["last_seen"], tz=timezone.utc), True))
    spans = [s for s in spans if s[2] is not None]
    if not spans:
        raise HTTPException(status_code=404, detail=f"No recorded journey for {global_id}")
    spans.sort(key=lambda s: s[2])

    journey_data = [
        {
            "camera_id": cam_id,
            "zone": zone,
            "timestamp": start.isoformat(),
            "duration": max(0.0, (end - start).total_seconds()) if end else 0.0,
            "in_progress": in_progress,
        }
        for cam_id, zone, start, end, in_progress in spans
    ]
    entry_time = spans[0][2]
    exit_time = None if any(s[4] for s in spans) else max((s[3] for s in spans if s[3]), default=None)
    last_end = max((s[3] for s in spans if s[3]), default=None)
    total = (last_end - entry_time).total_seconds() if last_end else None
    return CustomerJourneyResponse(
        global_id=global_id,
        entry_time=entry_time,
        exit_time=exit_time,
        total_duration=total,
        zones_visited=len({s[1] for s in spans if s[1]}),
        journey_data=journey_data,
    )


@router.get("/active", response_model=List[ReIDMatch])
async def get_active_persons(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Global IDs currently assigned to a live track (updated in the last few seconds)."""
    coord = _coordinator(request)
    rows = coord.active() if coord else []
    if rows:
        cutoff = time.time() - ACTIVE_WINDOW_SECONDS
        rows = [r for r in rows if float(r.get("last_seen") or 0.0) >= cutoff]
    active = [
        ReIDMatch(
            global_id=str(r["global_id"]),
            camera_id=int(r["camera_id"]),
            confidence=round(float(r.get("similarity") or 0.0), 4),
            timestamp=_ts(r["last_seen"]),
            bbox=r.get("bbox"),
        )
        for r in rows
    ]
    active.sort(key=lambda m: m.timestamp, reverse=True)
    if not active:
        _no_data(response)
    return active


# ── gallery / status ────────────────────────────────────────────────

@router.get("/gallery")
async def get_gallery(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Recent identities: live (in memory) plus the persisted total."""
    coord = _coordinator(request)
    live = coord.identities(limit) if coord else []
    try:
        persisted = await ReIDIdentityService.count(db)
    except Exception:
        persisted = None
    return {
        "status": "ok" if live else "no_data",
        "identities": live,
        "in_memory": len(coord.gallery) if coord else 0,
        "persisted_identities": persisted,
        "model_version": settings.reid_model_version,
    }


@router.get("/status")
async def get_reid_status(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Coordinator snapshot: thresholds, gallery size, tracklets, gating, fusion, counters."""
    pipe = _pipeline(request)
    coord = _coordinator(request)
    if coord is None:
        return {"status": "no_data"}
    reid = getattr(pipe, "reid", None)
    return {
        "status": "ok",
        "model": reid.info() if reid is not None and reid.is_loaded else {"status": "not_loaded"},
        "quality_gate": dict(getattr(pipe, "_reid_gate", {}) or {}),
        **coord.snapshot(),
    }


@router.get("/expected")
async def get_expected_arrivals(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Identities that left a camera and are expected on a linked camera (topology handoff)."""
    coord = _coordinator(request)
    rows = coord.expected_arrivals() if coord else []
    return {"status": "ok" if rows else "no_data", "expected": rows}


# ── topology ────────────────────────────────────────────────────────

@router.get("/topology")
async def get_topology(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    coord = _coordinator(request)
    links = await ReIDConfigService.list_links(db)
    return {
        "status": "ok" if links else "no_data",
        "links": links,
        "active_links": coord.topology.as_list() if coord else [],
        "default_max_transit_s": settings.REID_DEFAULT_MAX_TRANSIT_S,
        "reentry_max_s": settings.REID_REENTRY_MAX_S,
    }


@router.put("/topology")
async def put_topology(
    body: TopologyBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN)),
) -> Dict[str, Any]:
    """Replace the camera adjacency graph. Empty list = no gating beyond the default window."""
    raw = [link.model_dump(by_alias=True) for link in body.links]
    try:
        parsed = parse_links(raw)
        n = await ReIDConfigService.replace_links(db, raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Topology rejected (unknown camera?): {type(e).__name__}")
    await AuditService.log_event(
        db, "REID_TOPOLOGY_UPDATE", admin.id, f"Re-ID topology replaced ({n} directed links)",
        {"links": raw}, ip_address=client_ip(request),
    )
    await db.commit()
    coord = _coordinator(request)
    if coord is not None:
        coord.set_topology(parsed)
    return {"status": "ok", "directed_links": n}


# ── calibration ─────────────────────────────────────────────────────

@router.get("/calibration")
async def get_calibrations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    rows = await ReIDConfigService.list_calibrations(db)
    coord = _coordinator(request)
    calibrated = coord.calibrated_cameras() if coord else []
    return {
        "status": "ok" if rows else "no_data",
        "calibrations": rows,
        "fusion": "on" if len(calibrated) >= 2 else "off (needs 2+ calibrated cameras)",
    }


@router.put("/calibration/{camera_id}")
async def put_calibration(
    camera_id: int,
    body: CalibrationBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN)),
) -> Dict[str, Any]:
    coord = _coordinator(request)
    points, rmse = None, None
    try:
        if (body.homography is None) == (body.points is None):
            raise ValueError("Send either homography or points (with frame_wh), not both")
        if body.points is not None:
            if body.frame_wh is None:
                raise ValueError("frame_wh is required with points")
            homography, rmse = fit_floor_homography(body.points, body.frame_wh)
            points = [p.model_dump() for p in body.points]
        else:
            homography = body.homography
        if coord is not None:
            coord.set_calibration(camera_id, homography)  # validates finiteness/invertibility
        await ReIDConfigService.set_calibration(db, camera_id, homography, points=points, rmse_m=rmse)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        await db.rollback()
        if coord is not None:
            coord.set_calibration(camera_id, None)
        raise HTTPException(status_code=409, detail=f"Calibration rejected (unknown camera?): {type(e).__name__}")
    await AuditService.log_event(
        db, "REID_CALIBRATION_SET", admin.id, f"Floor homography set for camera {camera_id}",
        {"camera_id": camera_id, "points": len(points) if points else 0}, ip_address=client_ip(request),
    )
    await db.commit()
    return {"status": "ok", "camera_id": camera_id, "homography": homography, "rmse_m": rmse,
            "points": len(points) if points else None}


@router.delete("/calibration/{camera_id}")
async def delete_calibration(
    camera_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN)),
) -> Dict[str, Any]:
    if not await ReIDConfigService.delete_calibration(db, camera_id):
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} has no calibration")
    await AuditService.log_event(
        db, "REID_CALIBRATION_DELETE", admin.id, f"Floor homography removed for camera {camera_id}",
        {"camera_id": camera_id}, ip_address=client_ip(request),
    )
    await db.commit()
    coord = _coordinator(request)
    if coord is not None:
        coord.set_calibration(camera_id, None)
    return {"status": "ok", "camera_id": camera_id}
