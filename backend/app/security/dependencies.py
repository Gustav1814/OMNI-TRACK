"""
OmniTrack AI — authentication / authorisation dependencies.

* Tokens come from ``Authorization: Bearer``. ``?token=`` is accepted only on
  GET/HEAD (``<img>``/``<video>`` tags cannot send headers) — never on a
  state-changing request, so a token leaked in a URL cannot mutate anything.
* Only ``type="access"`` tokens authenticate requests; a refresh token is
  rejected here and is only valid at /api/auth/refresh.
* The token's ``ver`` must equal ``users.token_version`` (bumped on password /
  role change, deactivation and logout-all) so stale tokens die immediately.
"""

from __future__ import annotations

from typing import Iterable, Optional, Union

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.database import get_db
from app.models.user import User, UserRole
from app.security.jwt_handler import ACCESS, decode_token

# Only used so OpenAPI shows the "Authorize" button; parsing is done below.
security_scheme = HTTPBearer(auto_error=False)

_QUERY_TOKEN_METHODS = {"GET", "HEAD"}
_STATE_KEY = "omnitrack_auth_user"


def extract_token(conn: HTTPConnection) -> Optional[str]:
    auth = conn.headers.get("authorization") or ""
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    if conn.scope.get("type") == "websocket" or conn.scope.get("method") in _QUERY_TOKEN_METHODS:
        token = conn.query_params.get("token")
        if token:
            return token
    return None


async def user_from_access_payload(payload: Optional[dict], db: AsyncSession) -> Optional[User]:
    if not payload or payload.get("type") != ACCESS:
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if int(payload.get("ver", -1)) != int(user.token_version or 0):
        return None  # revoked by password/role change, deactivation or logout-all
    return user


async def get_user_from_token(token: Optional[str], db: AsyncSession) -> Optional[User]:
    """Validate a raw access JWT (e.g. WebSocket ?token=) and return the active user, or None."""
    if not token:
        return None
    return await user_from_access_payload(decode_token(token, expected_type=ACCESS), db)


async def get_optional_user(
    conn: HTTPConnection,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """The authenticated user or None. Cached per request; skipped for WebSockets
    (they authenticate themselves before accept, without holding a DB session)."""
    if conn.scope.get("type") != "http":
        return None
    if _STATE_KEY in conn.scope:
        return conn.scope[_STATE_KEY]
    user = await get_user_from_token(extract_token(conn), db)
    conn.scope[_STATE_KEY] = user
    return user


async def get_current_user(
    user: Optional[User] = Depends(get_optional_user),
    _bearer: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated or token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


RoleSpec = Union[str, UserRole, Iterable[Union[str, UserRole]]]


def _normalise_roles(roles: RoleSpec) -> frozenset:
    if isinstance(roles, (str, UserRole)):
        roles = [roles]
    return frozenset(UserRole(r) if not isinstance(r, UserRole) else r for r in roles)


def require_role(*roles: RoleSpec):
    """``Depends(require_role("admin"))`` or ``require_role([UserRole.ADMIN, ...])``."""
    allowed: frozenset = frozenset()
    for spec in roles:
        allowed |= _normalise_roles(spec)
    if not allowed:
        raise ValueError("require_role needs at least one role")

    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(sorted(r.value for r in allowed))}",
            )
        return current_user

    role_checker.allowed_roles = allowed  # introspected by the route-table tests
    role_checker.__name__ = f"require_role_{'_'.join(sorted(r.value for r in allowed))}"
    return role_checker


require_admin = require_role(UserRole.ADMIN)
