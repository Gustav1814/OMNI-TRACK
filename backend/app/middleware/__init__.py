"""
OmniTrack AI — Request Middleware
Production-grade middleware stack:
  - Request ID tracing (every request gets a unique ID for debugging)
  - Structured logging (method, path, status, duration)
  - Rate limiting per user/IP
  - Security headers (XSS, clickjacking protection)
  - Processing time header (X-Process-Time)
"""

import uuid
import time
from typing import Callable
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

from app.security.client_ip import client_ip as resolve_request_ip


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique request ID to every API call.
    
    This ID appears in:
      - Response header: X-Request-ID
      - All log entries for that request
      - Error reports
    
    WHY: When debugging "why did the shelf analytics crash at 2pm?",
    you can trace the exact request through every log line.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate unique request ID
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # Start timer
        start_time = time.time()

        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(f"[{request_id}] Unhandled error: {e}")
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )

        # Calculate processing time
        process_time = time.time() - start_time

        # Add headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.4f}"

        # Log the request (skip health checks to reduce noise)
        path = request.url.path
        if path not in ("/api/health", "/docs", "/redoc", "/openapi.json"):
            status = response.status_code
            level = "INFO" if status < 400 else "WARNING" if status < 500 else "ERROR"
            logger.log(
                level,
                f"[{request_id}] {request.method} {path} → {status} ({process_time*1000:.1f}ms)"
            )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to every response.
    
    These protect against common web attacks:
      - XSS (Cross-Site Scripting)
      - Clickjacking
      - MIME sniffing
      - Referrer leaks
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Legacy XSS auditors are themselves exploitable; modern guidance is to disable them.
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # API responses are data, never documents: forbid scripts/framing outright.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; sandbox"
            )

        return response


def _is_media_path(path: str, method: str) -> bool:
    """GET/HEAD of evidence files/thumbnails and served footage."""
    if method not in ("GET", "HEAD"):
        return False
    if path.startswith("/api/evidence/") and (path.endswith("/file") or path.endswith("/thumb")):
        return True
    return path.startswith("/api/footage/serve/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware using sliding window algorithm.
    
    Limits (per real client IP — see app/security/client_ip.py):
      - 10/minute on /api/auth/login and /admin/login (brute force; account
        lockout in routers/auth.py is the second layer)
      - 30/minute on the other /api/auth/* endpoints
      - 120/minute per top-level API area for everything else
    """

    _LOCAL_MAX_KEYS = 50_000  # bound the in-memory fallback (IP-spray cannot exhaust RAM)

    def __init__(self, app, cache=None):
        super().__init__(app)
        self._cache = cache
        self._local_counts = {}  # Fallback if no Redis

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health checks and docs
        path = request.url.path
        if path in ("/api/health", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)

        # Real client IP: X-Forwarded-For is honoured only from TRUSTED_PROXIES.
        client_ip = resolve_request_ip(request)
        
        # Stricter limit for auth endpoints
        if path in ("/api/auth/login", "/admin/login"):
            max_requests, window = 10, 60   # 10 login attempts per minute
        elif path.startswith("/api/auth/"):
            max_requests, window = 30, 60   # 30 auth requests per minute
        elif _is_media_path(path, request.method):
            # Media reads (<img>/<video> incl. ranged seeks): browsing-shaped but still bounded,
            # and in their own bucket so a gallery page cannot exhaust the API bucket.
            max_requests, window = 900, 60
        else:
            max_requests, window = 120, 60  # 120 general requests per minute

        segments = path.split("/")
        if max_requests == 10:
            bucket = "login"
        elif max_requests == 900:
            bucket = "media"
        else:
            bucket = segments[2] if len(segments) > 2 else "general"
        identifier = f"{client_ip}:{bucket}"

        # Check rate limit
        if self._cache:
            result = await self._cache.rate_limit(identifier, max_requests, window)
        else:
            # Simple in-memory fallback
            now = time.time()
            key = f"rl:{identifier}"
            if key not in self._local_counts and len(self._local_counts) >= self._LOCAL_MAX_KEYS:
                cutoff = now - window
                self._local_counts = {
                    k: v for k, v in self._local_counts.items() if v and v[-1] > cutoff
                }
            if key not in self._local_counts:
                self._local_counts[key] = []
            self._local_counts[key] = [t for t in self._local_counts[key] if t > now - window]
            self._local_counts[key].append(now)
            count = len(self._local_counts[key])
            result = {"allowed": count <= max_requests, "remaining": max(0, max_requests - count)}

        if not result["allowed"]:
            logger.warning(f"Rate limit exceeded: {identifier}")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please slow down.",
                    "retry_after": result.get("reset_in", 60),
                },
                headers={"Retry-After": str(int(result.get("reset_in", 60)))},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(result.get("remaining", "?"))
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        return response
