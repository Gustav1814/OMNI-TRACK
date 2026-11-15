"""Live route-table auth audit: walks every HTTP route actually registered on the
app (including routers owned by other modules) and fails loudly if any
state-changing route is reachable without the admin role.

Allow-list = the genuinely public endpoints (login/register/refresh/logout/health
and the bootstrap status probe). Read-only POSTs must be explicitly marked
``@authenticated_action``; they are listed in the failure output for review.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute, iter_route_contexts

from app.models.user import UserRole
from app.security.route_policy import (
    AUTHENTICATED,
    MUTATING_METHODS,
    endpoint_access,
    enforce_route_policy,
    required_role_for,
)

PUBLIC_ALLOWLIST = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/bootstrap"),
    ("GET", "/api/health"),
}


@pytest.fixture(scope="module")
def app():
    from app.main import app as live_app
    return live_app


def _http_routes(app):
    out = []
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(ctx.methods or ()):
            out.append((method, ctx.path, ctx.endpoint))
    return out


def test_policy_installed_app_wide(app):
    deps = [d.dependency for d in app.router.dependencies]
    assert enforce_route_policy in deps, "enforce_route_policy must be an app-level dependency"


def _calls(dependant):
    stack, seen = [dependant], set()
    while stack:
        d = stack.pop()
        if d is None or id(d) in seen:
            continue
        seen.add(id(d))
        yield d.call
        stack.extend(d.dependencies)


def test_policy_reaches_every_effective_route(app):
    """The app-level dependency must be in the dependency tree FastAPI actually
    executes for each route (catches routers mounted in a way that skips it)."""
    missing = []
    for ctx in iter_route_contexts(app.routes):
        if not isinstance(ctx.original_route, APIRoute):
            continue
        if enforce_route_policy not in set(_calls(ctx.dependant)):
            missing.append(f"{sorted(ctx.methods or ())} {ctx.path}")
    assert not missing, "Routes executing WITHOUT enforce_route_policy:\n  " + "\n  ".join(missing)


def test_route_walk_finds_the_api(app):
    routes = _http_routes(app)
    paths = {p for _, p, _ in routes}
    assert len(routes) > 80, f"route walk looks broken: only {len(routes)} routes"
    for must in ("/api/auth/login", "/api/cameras/", "/api/audit/verify"):
        assert any(p.rstrip("/") == must.rstrip("/") for p in paths), f"{must} missing from route walk"


def test_every_mutating_route_requires_admin(app):
    offenders, public_unlisted = [], []
    for method, path, endpoint in _http_routes(app):
        need = required_role_for(method, endpoint)
        if need is None:
            if (method, path) not in PUBLIC_ALLOWLIST:
                public_unlisted.append(f"{method} {path} ({endpoint.__module__}.{endpoint.__name__})")
            continue
        if method in MUTATING_METHODS and need != UserRole.ADMIN.value:
            if endpoint_access(endpoint) != AUTHENTICATED:
                offenders.append(f"{method} {path}")
    assert not public_unlisted, "Public routes outside the allow-list:\n  " + "\n  ".join(public_unlisted)
    assert not offenders, "Mutating routes NOT requiring admin:\n  " + "\n  ".join(offenders)


def test_authenticated_actions_are_reported(app):
    """Read-only POSTs open to every logged-in role — printed so reviewers see them."""
    marked = sorted(f"{m} {p}" for m, p, e in _http_routes(app)
                    if m in MUTATING_METHODS and endpoint_access(e) == AUTHENTICATED)
    print("\n@authenticated_action routes:\n  " + "\n  ".join(marked or ["(none)"]))
    for entry in marked:  # none of these may be destructive verbs
        assert not entry.startswith(("DELETE", "PUT", "PATCH")), entry


def test_allowlist_is_still_public(app):
    live = {(m, p): e for m, p, e in _http_routes(app)}
    for key in PUBLIC_ALLOWLIST:
        assert key in live, f"allow-listed route vanished: {key}"
        assert required_role_for(*key[:1], live[key]) is None, f"{key} lost @public_endpoint"


# ───────── the dependency itself: anonymous -> 401, viewer -> 403, admin -> ok ─────────

def _conn(method, endpoint):
    return SimpleNamespace(scope={"type": "http", "method": method, "route": SimpleNamespace(endpoint=endpoint)})


def _run(method, endpoint, user):
    return asyncio.run(enforce_route_policy(_conn(method, endpoint), user))


def test_policy_decisions_on_every_route(app):
    viewer = SimpleNamespace(role=UserRole.VIEWER)
    admin = SimpleNamespace(role=UserRole.ADMIN)
    checked = 0
    for method, path, endpoint in _http_routes(app):
        need = required_role_for(method, endpoint)
        if need is None:
            assert _run(method, endpoint, None) is None
            continue
        with pytest.raises(HTTPException) as anon:
            _run(method, endpoint, None)
        assert anon.value.status_code == 401, f"{method} {path}"
        if need == UserRole.ADMIN.value:
            with pytest.raises(HTTPException) as v:
                _run(method, endpoint, viewer)
            assert v.value.status_code == 403, f"{method} {path}"
        else:
            assert _run(method, endpoint, viewer) is None
        assert _run(method, endpoint, admin) is None
        checked += 1
    assert checked > 50
