"""
OmniTrack AI — Auth Router
Login, Register, Token Refresh, Get Current User.
Writes LOGIN / USER_CREATE to tamper-evident audit trail (proposal: Decoupled Security Pipeline).
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.schemas import UserCreate, UserLogin, UserResponse, Token, TokenRefresh
from app.security.jwt_handler import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.security.dependencies import get_current_user
from app.services.crud import AuditService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _client_ip(request: Request) -> str | None:
    """Forwarded IP when behind proxy."""
    return (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user. Logs USER_CREATE to audit chain."""
    result = await db.execute(
        select(User).where((User.username == user_data.username) | (User.email == user_data.email))
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        role=UserRole.VIEWER,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    await AuditService.log_event(
        db,
        event_type="USER_CREATE",
        user_id=user.id,
        description=f"User registered: {user.username}",
        metadata={"email": user.email, "role": user.role.value},
        ip_address=_client_ip(request),
    )
    return user


@router.post("/login", response_model=Token)
async def login(
    creds: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and get JWT tokens. Logs LOGIN to audit chain."""
    result = await db.execute(select(User).where(User.username == creds.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(creds.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")

    await AuditService.log_event(
        db,
        event_type="LOGIN",
        user_id=user.id,
        description=f"User logged in: {user.username}",
        metadata={"user_id": user.id},
        ip_address=_client_ip(request),
    )

    access = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh = create_refresh_token({"sub": str(user.id)})
    return Token(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=Token)
async def refresh_token(body: TokenRefresh, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh token."""
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    access = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh = create_refresh_token({"sub": str(user.id)})
    return Token(access_token=access, refresh_token=refresh)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user profile."""
    return current_user
