"""
OmniTrack AI — Database Admin Panel
═══════════════════════════════════
Browser-based DB admin powered by SQLAdmin. Mounted at /admin.

Most analytics tables (detections, embeddings, foot traffic, …) only receive rows
while the processing pipeline is running with an active feed and successful DB writes.

Auth:
  - Username + password from the existing `users` table (bcrypt).
  - Only users with `role == ADMIN` are allowed in.
  - Session cookie signed with `JWT_SECRET_KEY` (Starlette session middleware
    is registered automatically by SQLAdmin when an auth backend is provided).

Usage:
  Visit http://<host>:8000/admin and log in with an admin account.
"""

from __future__ import annotations

from typing import Optional

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.models.user import User, UserRole
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding
from app.models.audit_log import AuditLog
from app.models.analytics import (
    FootTraffic,
    CustomerJourney,
    DemographicSnapshot,
    StoreVibeScore,
    PeakHoursData,
)
from app.security.jwt_handler import verify_password


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────

class AdminAuth(AuthenticationBackend):
    """Session-cookie auth backed by the `users` table (admin role only)."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not username or not password:
            return False

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.username == username))
            user: Optional[User] = result.scalar_one_or_none()

        if user is None or not user.is_active:
            return False
        if user.role != UserRole.ADMIN:
            return False
        if not verify_password(password, user.hashed_password):
            return False

        request.session["admin_user_id"] = user.id
        request.session["admin_username"] = user.username
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get("admin_user_id")
        if not user_id:
            return False
        async with AsyncSessionLocal() as db:
            user = await db.get(User, int(user_id))
        return bool(user and user.is_active and user.role == UserRole.ADMIN)


# ─────────────────────────────────────────────────────────────
# Model views
# ─────────────────────────────────────────────────────────────

class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"
    category = "Accounts"
    column_list = [
        User.id, User.username, User.email, User.full_name,
        User.role, User.is_active, User.created_at,
    ]
    column_searchable_list = [User.username, User.email, User.full_name]
    column_sortable_list = [User.id, User.username, User.created_at, User.role]
    column_default_sort = [(User.id, True)]
    form_excluded_columns = [User.hashed_password]
    column_details_exclude_list = [User.hashed_password]


class CameraAdmin(ModelView, model=Camera):
    name = "Camera"
    name_plural = "Cameras"
    icon = "fa-solid fa-video"
    category = "Infrastructure"
    column_list = "__all__"
    column_searchable_list = [Camera.name] if hasattr(Camera, "name") else []
    column_default_sort = [(Camera.id, True)]


class DetectionAdmin(ModelView, model=Detection):
    name = "Detection"
    name_plural = "Detections"
    icon = "fa-solid fa-magnifying-glass"
    category = "Pipeline Data"
    column_list = "__all__"
    column_default_sort = [(Detection.id, True)]
    page_size = 50
    page_size_options = [25, 50, 100, 200]


class EmbeddingAdmin(ModelView, model=Embedding):
    name = "Embedding"
    name_plural = "Embeddings"
    icon = "fa-solid fa-fingerprint"
    category = "Pipeline Data"
    # Hide the raw vector column from the list view — it's huge.
    column_exclude_list = [Embedding.vector]
    column_default_sort = [(Embedding.id, True)]
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 50


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-shield-halved"
    category = "Security"
    column_list = "__all__"
    column_default_sort = [(AuditLog.id, True)]
    can_create = False
    can_edit = False
    can_delete = False  # Audit chain is tamper-evident — never mutate from UI.
    page_size = 50


class FootTrafficAdmin(ModelView, model=FootTraffic):
    name = "Foot Traffic"
    name_plural = "Foot Traffic"
    icon = "fa-solid fa-people-arrows"
    category = "Analytics"
    column_list = [
        FootTraffic.id, FootTraffic.camera_id, FootTraffic.zone,
        FootTraffic.person_count, FootTraffic.direction_in,
        FootTraffic.direction_out, FootTraffic.avg_dwell_time,
        FootTraffic.timestamp,
    ]
    column_searchable_list = [FootTraffic.zone]
    column_default_sort = [(FootTraffic.timestamp, True)]
    page_size = 50


class CustomerJourneyAdmin(ModelView, model=CustomerJourney):
    name = "Customer Journey"
    name_plural = "Customer Journeys"
    icon = "fa-solid fa-route"
    category = "Analytics"
    column_list = [
        CustomerJourney.id, CustomerJourney.global_id, CustomerJourney.camera_id,
        CustomerJourney.zone, CustomerJourney.dwell_time,
        CustomerJourney.entry_time, CustomerJourney.exit_time,
        CustomerJourney.total_duration, CustomerJourney.zones_visited,
    ]
    column_searchable_list = [CustomerJourney.global_id, CustomerJourney.zone]
    column_default_sort = [(CustomerJourney.entry_time, True)]
    page_size = 50


class DemographicAdmin(ModelView, model=DemographicSnapshot):
    name = "Demographic Snapshot"
    name_plural = "Demographics"
    icon = "fa-solid fa-users"
    category = "Analytics"
    column_list = "__all__"
    column_default_sort = [(DemographicSnapshot.timestamp, True)]
    page_size = 50


class VibeAdmin(ModelView, model=StoreVibeScore):
    name = "Store Vibe"
    name_plural = "Store Vibe Scores"
    icon = "fa-solid fa-chart-line"
    category = "Analytics"
    column_list = "__all__"
    column_default_sort = [(StoreVibeScore.timestamp, True)]
    page_size = 50


class PeakHoursAdmin(ModelView, model=PeakHoursData):
    name = "Peak Hour"
    name_plural = "Peak Hours"
    icon = "fa-solid fa-clock"
    category = "Analytics"
    column_list = "__all__"
    column_default_sort = [(PeakHoursData.date, True)]


# ─────────────────────────────────────────────────────────────
# Mount
# ─────────────────────────────────────────────────────────────

def setup_admin(app) -> Admin:
    """Attach the /admin panel to a FastAPI app."""
    auth_backend = AdminAuth(secret_key=settings.JWT_SECRET_KEY)
    admin = Admin(
        app=app,
        engine=engine,
        title="OmniTrack DB Admin",
        authentication_backend=auth_backend,
    )
    for view in (
        UserAdmin,
        CameraAdmin,
        DetectionAdmin,
        EmbeddingAdmin,
        AuditLogAdmin,
        FootTrafficAdmin,
        CustomerJourneyAdmin,
        DemographicAdmin,
        VibeAdmin,
        PeakHoursAdmin,
    ):
        admin.add_view(view)
    return admin
