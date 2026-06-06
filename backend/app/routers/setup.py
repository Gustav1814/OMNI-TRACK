"""
OmniTrack AI - Store Setup Hub API.

Centralizes deployment templates, enabled modules, and default zones for the
enterprise Setup Hub.
"""

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.store_profile import StoreProfile
from app.models.user import User, UserRole
from app.schemas.schemas import StoreProfileResponse, StoreProfileUpdate, StoreTemplate
from app.security.dependencies import get_current_user, require_role


router = APIRouter(prefix="/api/setup", tags=["Setup"])


STORE_TEMPLATES: Dict[str, StoreTemplate] = {
    "convenience": StoreTemplate(
        id="convenience",
        label="Convenience / Small format",
        modules=["crowd", "checkout", "fire", "vibe"],
        default_zones=["entrance", "aisle", "checkout"],
        description="Compact stores with a few key cameras and fast operational checks.",
    ),
    "supermarket": StoreTemplate(
        id="supermarket",
        label="Supermarket / Grocery",
        modules=["crowd", "checkout", "fire", "vibe", "reid", "shelf", "peak-hours", "demographics", "emotion"],
        default_zones=["entrance", "aisles", "shelf", "checkout", "storage"],
        description="Full retail analytics for grocery floors, shelves, queues, and safety.",
    ),
    "humanless": StoreTemplate(
        id="humanless",
        label="Cashierless / Smart store",
        modules=["humanless", "reid", "shelf", "checkout", "loss-prevention"],
        default_zones=["entrance", "shelf", "checkout"],
        description="Smart cart sessions, shelf decisions, and checkout handoff workflows.",
    ),
    "big_box": StoreTemplate(
        id="big_box",
        label="Big box / Warehouse club",
        modules=["crowd", "peak-hours", "demographics", "shelf"],
        default_zones=["entrance", "main floor", "checkout"],
        description="High traffic monitoring across large floor areas and entrances.",
    ),
    "fuel_forecourt": StoreTemplate(
        id="fuel_forecourt",
        label="Fuel & forecourt",
        modules=["fire", "safety", "crowd"],
        default_zones=["forecourt", "entrance"],
        description="Safety-first template for forecourt and entrance monitoring.",
    ),
    "custom": StoreTemplate(
        id="custom",
        label="Custom enterprise",
        modules=[],
        default_zones=[],
        description="Manual module and zone configuration.",
    ),
}


async def _get_or_create_profile(db: AsyncSession) -> StoreProfile:
    result = await db.execute(select(StoreProfile).order_by(StoreProfile.id).limit(1))
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile

    template = STORE_TEMPLATES["custom"]
    profile = StoreProfile(
        id=1,
        store_name="OmniTrack Store",
        deployment_type=template.id,
        enabled_modules=template.modules,
        zone_templates=template.default_zones,
    )
    db.add(profile)
    await db.flush()
    return profile


@router.get("/templates", response_model=List[StoreTemplate])
async def list_templates(current_user: User = Depends(get_current_user)):
    return list(STORE_TEMPLATES.values())


@router.get("/profile", response_model=StoreProfileResponse)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _get_or_create_profile(db)


@router.put("/profile", response_model=StoreProfileResponse)
async def update_profile(
    data: StoreProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    profile = await _get_or_create_profile(db)
    updates = data.model_dump(exclude_unset=True)

    deployment_type = updates.get("deployment_type")
    if deployment_type in STORE_TEMPLATES:
        template = STORE_TEMPLATES[deployment_type]
        profile.deployment_type = template.id
        if "enabled_modules" not in updates:
            profile.enabled_modules = template.modules
        if "zone_templates" not in updates:
            profile.zone_templates = template.default_zones

    if "store_name" in updates and updates["store_name"]:
        profile.store_name = updates["store_name"]
    if "enabled_modules" in updates:
        profile.enabled_modules = updates["enabled_modules"] or []
    if "zone_templates" in updates:
        profile.zone_templates = updates["zone_templates"] or []
    if "setup_completed" in updates:
        profile.setup_completed_at = (
            datetime.now(timezone.utc) if updates["setup_completed"] else None
        )

    profile.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(profile)
    return profile
