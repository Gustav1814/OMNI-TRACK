"""
OmniTrack AI — deny-by-default route policy (installed app-wide in main.py).

Every HTTP route registered on the app — including routers added later by
other modules — passes through ``enforce_route_policy``:

* endpoints marked ``@public_endpoint``            -> no auth (login, register,
  refresh, logout, health, bootstrap status);
* safe methods (GET/HEAD/OPTIONS)                  -> any authenticated user;
* state-changing methods (POST/PUT/PATCH/DELETE)   -> role ``admin`` unless the
  endpoint is marked ``@authenticated_action`` (read-only queries that happen
  to use POST bodies, e.g. similarity search).

Routers still declare explicit ``require_role`` dependencies for clarity; this
policy guarantees a forgotten dependency cannot open a mutating route.
WebSockets authenticate themselves before ``accept`` (see main.py).
"""

from __future__ import annotations

from typing import Callable, Optional, TypeVar

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from app.models.user import User, UserRole
from app.security.dependencies import get_optional_user

ACCESS_ATTR = "__omnitrack_access__"
PUBLIC = "public"
AUTHENTICATED = "authenticated"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

F = TypeVar("F", bound=Callable)


def public_endpoint(fn: F) -> F:
    """Mark an endpoint as reachable without authentication. Keep this list tiny."""
    setattr(fn, ACCESS_ATTR, PUBLIC)
    return fn


def authenticated_action(fn: F) -> F:
    """Mark a POST/PUT endpoint that does not change server state (any logged-in role)."""
    setattr(fn, ACCESS_ATTR, AUTHENTICATED)
    return fn


def endpoint_access(endpoint: Optional[Callable]) -> Optional[str]:
    return getattr(endpoint, ACCESS_ATTR, None) if endpoint is not None else None


def required_role_for(method: str, endpoint: Optional[Callable]) -> Optional[str]:
    """None = public, 'authenticated' = any active user, 'admin' = admin only."""
    access = endpoint_access(endpoint)
    if access == PUBLIC:
        return None
    if method.upper() in SAFE_METHODS or access == AUTHENTICATED:
        return AUTHENTICATED
    return UserRole.ADMIN.value


def _route_endpoint(conn: HTTPConnection) -> Optional[Callable]:
    route = conn.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    return endpoint or conn.scope.get("endpoint")


async def enforce_route_policy(
    conn: HTTPConnection,
    user: Optional[User] = Depends(get_optional_user),
) -> None:
    if conn.scope.get("type") != "http":
        return  # WebSocket handlers authenticate before accept()
    method = conn.scope.get("method", "GET")
    needed = required_role_for(method, _route_endpoint(conn))
    if needed is None:
        return
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated or token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if needed == UserRole.ADMIN.value and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires role: admin")
