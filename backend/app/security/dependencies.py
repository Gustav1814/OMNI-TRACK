"""
OmniTrack AI — Security Dependencies
FastAPI Depends for auth, roles, rate limiting
"""

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User, UserRole
from app.security.jwt_handler import decode_token
from typing import List, Optional

security_scheme = HTTPBearer(auto_error=False)


def _token_payload(credentials: Optional[HTTPAuthorizationCredentials], request: Optional[Request]):
    """Get JWT payload from Bearer header or query param ?token= (for img src / stream URLs)."""
    token = None
    if credentials:
        token = credentials.credentials
    if not token and request:
        token = request.query_params.get("token")
    if not token:
        return None
    return decode_token(token)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current user from JWT token (header or query param 'token')."""
    payload = _token_payload(credentials, request)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Support both header and query token

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_role(allowed_roles: List[UserRole]):
    """Factory for role-based access control."""

    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' not authorized. Required: {[r.value for r in allowed_roles]}",
            )
        return current_user

    return role_checker
