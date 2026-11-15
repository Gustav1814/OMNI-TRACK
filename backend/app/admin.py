"""
OmniTrack AI — Database Admin Panel
═══════════════════════════════════
Browser-based DB admin powered by SQLAdmin. Mounted at /admin.

Most analytics tables (detections, embeddings, foot traffic, …) only receive rows
while the processing pipeline is running with an active feed and successful DB writes.

Auth:
  - Username + password from the existing `users` table (bcrypt).
  - Only users with `role == ADMIN` are allowed in.
  - Session cookie signed with ADMIN_SESSION_SECRET (or an HMAC derivation of the
    JWT secret — never the JWT key itself). Shares the API lockout counters,
    audits every attempt with the real client IP, and is bound to token_version.
  - Users, cameras and audit logs are read-only here: account/camera changes go
    through the audited API so they cannot bypass validation or the audit chain.

Usage:
  Visit http://<host>:8000/admin and log in with an admin account.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
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
from app.security.client_ip import client_ip
from app.security.jwt_handler import burn_password_check, verify_password
from app.services.crud import AuditService

try:
    from sqladmin import Admin, ModelView
    from sqladmin.authentication import AuthenticationBackend

    SQLADMIN_AVAILABLE = True
except ModuleNotFoundError:
    Admin = None
    SQLADMIN_AVAILABLE = False

    class ModelView:
        """Fallback base so the app can boot when the optional admin package is absent."""

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    class AuthenticationBackend:
        def __init__(self, *args, **kwargs):
            pass


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────

def admin_session_secret() -> str:
    """Dedicated cookie key: ADMIN_SESSION_SECRET, else an HMAC derivation of the
    JWT secret (so a leaked admin cookie key never equals the token-signing key)."""
    if settings.ADMIN_SESSION_SECRET:
        return settings.ADMIN_SESSION_SECRET
    return hmac.new(settings.JWT_SECRET_KEY.encode(), b"omnitrack-admin-session-v1", hashlib.sha256).hexdigest()


class AdminAuth(AuthenticationBackend):
    """Session-cookie auth backed by the `users` table (admin role only).

    Shares the API's lockout counters (LOGIN_MAX_FAILURES / LOGIN_LOCKOUT_MINUTES),
    audits every attempt with the real client IP, and binds the session to the
    user's ``token_version`` so a password change / "log out everywhere" / role
    change also ends admin-panel sessions.
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username") or "").strip()[:50]
        password = str(form.get("password") or "")[:128]
        if not username or not password:
            return False
        ip = client_ip(request)
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            user: Optional[User] = (await db.execute(
                select(User).where(User.username == username).with_for_update()
            )).scalar_one_or_none()
            ok = False
            if user is None:
                burn_password_check(password)
                event, desc = "ADMIN_LOGIN_FAILED", "Admin panel login failed: unknown user"
            elif user.locked_until and user.locked_until > now:
                event, desc = "ADMIN_LOGIN_BLOCKED", f"Admin panel login while locked: {user.username}"
            elif not verify_password(password, user.hashed_password):
                if user.locked_until and user.locked_until <= now:
                    user.failed_login_attempts, user.locked_until = 0, None
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= settings.LOGIN_MAX_FAILURES:
                    user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                    event, desc = "ACCOUNT_LOCKED", f"Account locked (admin panel): {user.username}"
                else:
                    event, desc = "ADMIN_LOGIN_FAILED", f"Admin panel login failed: {user.username}"
            elif not user.is_active or user.role != UserRole.ADMIN:
                event, desc = "ADMIN_LOGIN_DENIED", f"Admin panel denied (role/inactive): {user.username}"
            else:
                user.failed_login_attempts, user.locked_until = 0, None
                user.last_login_at = now
                ok = True
                event, desc = "ADMIN_LOGIN", f"Admin panel login: {user.username}"
            await AuditService.log_event(
                db, event, user.id if user else None, desc,
                {"username": username} if user is None else None, ip_address=ip,
            )
            await db.commit()

        if not ok:
            return False
        request.session.clear()
        request.session["admin_user_id"] = user.id
        request.session["admin_username"] = user.username
        request.session["admin_token_version"] = user.token_version or 0
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
        valid = bool(
            user and user.is_active and user.role == UserRole.ADMIN
            and (user.token_version or 0) == request.session.get("admin_token_version")
        )
        if not valid:
            request.session.clear()
        return valid


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
    column_details_exclude_list = [User.hashed_password]
    # Account changes go through /api/auth/users (password policy, last-admin
    # protection, session revocation, audit chain) — never raw row edits.
    can_create = False
    can_edit = False
    can_delete = False


class CameraAdmin(ModelView, model=Camera):
    name = "Camera"
    name_plural = "Cameras"
    icon = "fa-solid fa-video"
    category = "Infrastructure"
    column_searchable_list = [Camera.name] if hasattr(Camera, "name") else []
    column_default_sort = [(Camera.id, True)]
    column_exclude_list = [Camera.stream_url]          # may embed RTSP credentials
    column_details_exclude_list = [Camera.stream_url]
    # Sources must pass validate_camera_source (SSRF / path checks) -> use the API.
    can_create = False
    can_edit = False
    can_delete = False


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

def setup_admin(app) -> Optional[Any]:
    """Attach the /admin panel to a FastAPI app."""
    if not SQLADMIN_AVAILABLE:
        logger.warning(
            "SQLAdmin is not installed; /admin is disabled. "
            "Install backend requirements to enable the database admin panel."
        )
        return None

    auth_backend = AdminAuth(secret_key=admin_session_secret())
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
