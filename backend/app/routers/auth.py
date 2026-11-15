"""
OmniTrack AI — Auth Router.

* login            : bcrypt check, lockout after LOGIN_MAX_FAILURES consecutive
                     failures for LOGIN_LOCKOUT_MINUTES (plus per-IP rate limit
                     in middleware); timing-equalised for unknown usernames.
* refresh          : one-time refresh tokens. Each use rotates the token; the
                     old jti is revoked server-side. Re-using a rotated token
                     (theft signal) revokes the whole token family and every
                     access token of that user (token_version bump).
* logout           : revokes the presented refresh token (or all sessions).
* register         : the very first account bootstraps as admin; afterwards
                     public sign-up is closed unless ALLOW_PUBLIC_REGISTRATION.
* /users           : admin-only account management.

Every security-relevant action is written to the HMAC audit chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import RefreshToken, User, UserRole
from app.schemas.schemas import UserCreate, UserLogin, UserResponse
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, get_optional_user, require_role
from app.security.jwt_handler import (
    REFRESH,
    burn_password_check,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    refresh_expiry,
    verify_password,
)
from app.security.route_policy import authenticated_action, public_endpoint
from app.services.crud import AuditService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

ROTATION_GRACE_SECONDS = 15  # concurrent refresh from two tabs is not treated as theft
_BOOTSTRAP_LOCK = 873246
PASSWORD_MIN, PASSWORD_MAX = 10, 128


# ─────────────────────────── schemas ───────────────────────────

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=4096)


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(None, max_length=4096)
    all_sessions: bool = False


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=PASSWORD_MAX)
    new_password: str = Field(..., min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class AdminUserCreate(UserCreate):
    role: UserRole = UserRole.VIEWER


class AdminUserUpdate(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = Field(None, max_length=100)
    unlock: bool = False
    new_password: Optional[str] = Field(None, min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class AdminUserView(UserResponse):
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class BootstrapStatus(BaseModel):
    needs_bootstrap: bool
    public_registration: bool
    password_min_length: int = PASSWORD_MIN


# ─────────────────────────── helpers ───────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ua(request: Request) -> Optional[str]:
    ua = request.headers.get("user-agent")
    return ua[:300] if ua else None


async def _audit(db: AsyncSession, request: Request, event: str, user_id: Optional[int], desc: str, meta=None):
    await AuditService.log_event(
        db, event_type=event, user_id=user_id, description=desc,
        metadata=meta, ip_address=client_ip(request), user_agent=_ua(request),
    )


async def _issue_tokens(
    db: AsyncSession, user: User, request: Request, family_id: Optional[str] = None
) -> tuple[TokenPair, str]:
    jti = uuid.uuid4().hex
    family = family_id or uuid.uuid4().hex
    expires_at = refresh_expiry()
    db.add(RefreshToken(
        jti=jti, family_id=family, user_id=user.id, expires_at=expires_at,
        ip_address=client_ip(request)[:45], user_agent=_ua(request),
    ))
    pair = TokenPair(
        access_token=create_access_token(user.id, user.role.value, user.token_version or 0),
        refresh_token=create_refresh_token(user.id, jti, family, expires_at),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )
    return pair, jti


async def _revoke_all_sessions(db: AsyncSession, user: User, reason: str) -> None:
    """Kill every refresh token and every outstanding access token of ``user``."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )
    user.token_version = (user.token_version or 0) + 1


def _validate_password(password: str, username: str) -> None:
    if not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise HTTPException(422, f"Password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters")
    if password.lower() == username.lower() or username.lower() in password.lower():
        raise HTTPException(422, "Password must not contain the username")
    if len(set(password)) < 5:
        raise HTTPException(422, "Password is too repetitive")


async def _active_admin_count(db: AsyncSession) -> int:
    return int((await db.execute(
        select(func.count(User.id)).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )).scalar_one())


async def _create_user(db: AsyncSession, data: UserCreate, role: UserRole) -> User:
    _validate_password(data.password, data.username)
    dup = await db.execute(
        select(User.id).where((User.username == data.username) | (User.email == data.email))
    )
    if dup.first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already registered")
    user = User(
        username=data.username, email=data.email, full_name=data.full_name,
        hashed_password=hash_password(data.password), role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


# ─────────────────────────── public endpoints ───────────────────────────

@router.get("/bootstrap", response_model=BootstrapStatus)
@public_endpoint
async def bootstrap_status(db: AsyncSession = Depends(get_db)):
    """Whether the first (admin) account still has to be created."""
    has_user = (await db.execute(select(User.id).limit(1))).first() is not None
    return BootstrapStatus(
        needs_bootstrap=not has_user and settings.FIRST_USER_ADMIN,
        public_registration=settings.ALLOW_PUBLIC_REGISTRATION,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@public_endpoint
async def register(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: Optional[User] = Depends(get_optional_user),
):
    """First account becomes admin. Afterwards: admins only, unless public sign-up is enabled."""
    if settings.DATABASE_URL.startswith("postgresql"):
        await db.execute(text(f"SELECT pg_advisory_xact_lock({_BOOTSTRAP_LOCK})"))
    is_first = (await db.execute(select(User.id).limit(1))).first() is None
    if is_first and settings.FIRST_USER_ADMIN:
        role = UserRole.ADMIN
    elif actor is not None and actor.role == UserRole.ADMIN:
        role = UserRole.VIEWER
    elif settings.ALLOW_PUBLIC_REGISTRATION:
        role = UserRole.VIEWER
    else:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Registration is closed. Ask an administrator to create your account.",
        )
    user = await _create_user(db, user_data, role)
    await _audit(
        db, request, "USER_CREATE", actor.id if actor else user.id,
        f"User registered: {user.username}",
        {"user_id": user.id, "role": role.value, "bootstrap": is_first},
    )
    return user


@router.post("/login", response_model=TokenPair)
@public_endpoint
async def login(creds: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate. Locks the account after LOGIN_MAX_FAILURES consecutive failures."""
    user = (await db.execute(
        select(User).where(User.username == creds.username).with_for_update()
    )).scalar_one_or_none()
    now = _now()

    if user is None:
        burn_password_check(creds.password)  # same latency as a real check
        await _audit(db, request, "LOGIN_FAILED", None, "Login failed: unknown user",
                     {"username": creds.username[:50]})
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.locked_until and user.locked_until > now:
        retry = int((user.locked_until - now).total_seconds()) + 1
        await _audit(db, request, "LOGIN_BLOCKED", user.id, f"Login while locked: {user.username}")
        await db.commit()
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Account locked after repeated failures. Try again in {max(1, retry // 60)} min.",
            headers={"Retry-After": str(retry)},
        )

    if not verify_password(creds.password, user.hashed_password):
        if user.locked_until and user.locked_until <= now:
            user.failed_login_attempts = 0  # previous lock expired: start a fresh window
            user.locked_until = None
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        remaining = settings.LOGIN_MAX_FAILURES - user.failed_login_attempts
        if remaining <= 0:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            await _audit(db, request, "ACCOUNT_LOCKED", user.id,
                         f"Account locked: {user.username}",
                         {"failures": user.failed_login_attempts,
                          "minutes": settings.LOGIN_LOCKOUT_MINUTES})
            await db.commit()
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Too many failed attempts. Account locked for {settings.LOGIN_LOCKOUT_MINUTES} min.",
                headers={"Retry-After": str(settings.LOGIN_LOCKOUT_MINUTES * 60)},
            )
        await _audit(db, request, "LOGIN_FAILED", user.id, f"Login failed: {user.username}",
                     {"failures": user.failed_login_attempts})
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    pair, _ = await _issue_tokens(db, user, request)
    await _audit(db, request, "LOGIN", user.id, f"User logged in: {user.username}")
    return pair


@router.post("/refresh", response_model=TokenPair)
@public_endpoint
async def refresh_tokens(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Rotate a refresh token. The presented token becomes invalid immediately."""
    payload = decode_token(body.refresh_token, expected_type=REFRESH)
    if not payload or not payload.get("jti"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    row = (await db.execute(
        select(RefreshToken).where(RefreshToken.jti == payload["jti"]).with_for_update()
    )).scalar_one_or_none()
    if row is None or str(row.user_id) != str(payload.get("sub")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    user = (await db.execute(select(User).where(User.id == row.user_id).with_for_update())).scalar_one_or_none()
    now = _now()

    if row.revoked_at is not None:
        recent_rotation = (
            row.revoked_reason == "rotated"
            and (now - row.revoked_at).total_seconds() <= ROTATION_GRACE_SECONDS
        )
        if row.revoked_reason == "rotated" and not recent_rotation and user is not None:
            # A rotated token came back: someone else holds a copy. Kill the family + all sessions.
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason="reuse")
            )
            user.token_version = (user.token_version or 0) + 1
            await _audit(db, request, "REFRESH_REUSE", user.id,
                         f"Refresh token reuse detected; sessions revoked: {user.username}",
                         {"family": row.family_id})
            await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token revoked")

    if row.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token expired")
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    pair, new_jti = await _issue_tokens(db, user, request, family_id=row.family_id)
    row.revoked_at = now
    row.revoked_reason = "rotated"
    row.replaced_by_jti = new_jti
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@public_endpoint
async def logout(
    body: LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: Optional[User] = Depends(get_optional_user),
):
    """Revoke the given refresh token; with ``all_sessions`` (needs access token) revoke everything."""
    user_id = actor.id if actor else None
    if body.refresh_token:
        payload = decode_token(body.refresh_token, expected_type=REFRESH)
        if payload and payload.get("jti"):
            row = (await db.execute(
                select(RefreshToken).where(RefreshToken.jti == payload["jti"])
            )).scalar_one_or_none()
            if row is not None and row.revoked_at is None:
                row.revoked_at = _now()
                row.revoked_reason = "logout"
                user_id = user_id or row.user_id
    if body.all_sessions:
        if actor is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Logging out all sessions needs a valid access token")
        await _revoke_all_sessions(db, actor, "logout_all")
    if user_id is not None:
        await _audit(db, request, "LOGOUT", user_id, "User logged out",
                     {"all_sessions": body.all_sessions})
    return None


# ─────────────────────────── authenticated ───────────────────────────

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password", response_model=TokenPair)
@authenticated_action
async def change_password(
    body: PasswordChange,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change own password. Revokes every other session and returns a fresh token pair."""
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    _validate_password(body.new_password, current_user.username)
    current_user.hashed_password = hash_password(body.new_password)
    await _revoke_all_sessions(db, current_user, "password_change")
    await db.flush()
    pair, _ = await _issue_tokens(db, current_user, request)
    await _audit(db, request, "PASSWORD_CHANGE", current_user.id, f"Password changed: {current_user.username}")
    return pair


# ─────────────────────────── admin: user management ───────────────────────────

admin_only = require_role(UserRole.ADMIN)


@router.get("/users", response_model=List[AdminUserView])
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(admin_only)):
    return (await db.execute(select(User).order_by(User.id))).scalars().all()


@router.post("/users", response_model=AdminUserView, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    user = await _create_user(db, body, body.role)
    await _audit(db, request, "USER_CREATE", admin.id, f"Admin created user: {user.username}",
                 {"user_id": user.id, "role": user.role.value})
    return user


@router.patch("/users/{user_id}", response_model=AdminUserView)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    user = (await db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")
    changes = {}
    demoting = body.role is not None and body.role != UserRole.ADMIN and user.role == UserRole.ADMIN
    deactivating = body.is_active is False and user.is_active and user.role == UserRole.ADMIN
    if (demoting or deactivating) and await _active_admin_count(db) <= 1:
        raise HTTPException(409, "Cannot demote or deactivate the last active admin")
    if body.role is not None and body.role != user.role:
        changes["role"] = [user.role.value, body.role.value]
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = [user.is_active, body.is_active]
        user.is_active = body.is_active
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.new_password:
        _validate_password(body.new_password, user.username)
        user.hashed_password = hash_password(body.new_password)
        changes["password_reset"] = True
    if body.unlock:
        user.failed_login_attempts = 0
        user.locked_until = None
        changes["unlocked"] = True
    if {"role", "is_active", "password_reset"} & changes.keys():
        await _revoke_all_sessions(db, user, "admin_update")
    await db.flush()
    await db.refresh(user)
    if changes:
        await _audit(db, request, "USER_UPDATE", admin.id, f"Admin updated user: {user.username}",
                     {"user_id": user.id, "changes": changes})
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    if user_id == admin.id:
        raise HTTPException(409, "Admins cannot delete their own account")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")
    username = user.username
    await db.delete(user)
    await _audit(db, request, "USER_DELETE", admin.id, f"Admin deleted user: {username}",
                 {"user_id": user_id})
    return None
