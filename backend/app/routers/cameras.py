"""
OmniTrack AI — Camera CRUD Router.

Reads: any authenticated user (stream credentials are redacted).
Writes: admin only. ``stream_url`` is validated by ``validate_camera_source``
(rtsp, allow-listed http(s), or a file inside VIDEO_SOURCE_DIRS) so the server
cannot be pointed at internal HTTP services or arbitrary local files.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.camera import Camera
from app.models.user import User, UserRole
from app.schemas.schemas import CameraCreate, CameraResponse, CameraUpdate
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, require_role
from app.security.input_safety import UnsafeInputError, redact_source, validate_camera_source
from app.services.crud import AuditService

router = APIRouter(prefix="/api/cameras", tags=["Cameras"])
admin_only = require_role(UserRole.ADMIN)


def _view(camera: Camera) -> CameraResponse:
    out = CameraResponse.model_validate(camera, from_attributes=True)
    out.stream_url = redact_source(out.stream_url) or ""
    return out


def _safe_source(url: str) -> str:
    try:
        return validate_camera_source(url)
    except UnsafeInputError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def _get(db: AsyncSession, camera_id: int) -> Camera:
    camera = (await db.execute(select(Camera).where(Camera.id == camera_id))).scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.get("/", response_model=List[CameraResponse])
async def list_cameras(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    result = await db.execute(select(Camera).order_by(Camera.id))
    return [_view(c) for c in result.scalars().all()]


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return _view(await _get(db, camera_id))


@router.post("/", response_model=CameraResponse, status_code=201)
async def create_camera(
    data: CameraCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    payload = data.model_dump()
    payload["stream_url"] = _safe_source(payload["stream_url"])
    camera = Camera(**payload)
    db.add(camera)
    await db.flush()
    await db.refresh(camera)
    await AuditService.log_event(
        db, "CAMERA_CREATE", admin.id, f"Camera created: {camera.name}",
        {"camera_id": camera.id, "source": redact_source(camera.stream_url)},
        ip_address=client_ip(request),
    )
    return _view(camera)


@router.put("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: int,
    data: CameraUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    camera = await _get(db, camera_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("stream_url") is not None:
        changes["stream_url"] = _safe_source(changes["stream_url"])
    for key, val in changes.items():
        setattr(camera, key, val)
    await db.flush()
    await db.refresh(camera)
    await AuditService.log_event(
        db, "CAMERA_UPDATE", admin.id, f"Camera updated: {camera.name}",
        {"camera_id": camera.id, "fields": sorted(changes)},
        ip_address=client_ip(request),
    )
    return _view(camera)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(
    camera_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    camera = await _get(db, camera_id)
    name = camera.name
    await db.delete(camera)
    await AuditService.log_event(
        db, "CAMERA_DELETE", admin.id, f"Camera deleted: {name}", {"camera_id": camera_id},
        ip_address=client_ip(request),
    )
