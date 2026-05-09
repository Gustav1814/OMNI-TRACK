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
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # Cache control for API responses
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware using sliding window algorithm.
    
    Limits:
      - 120 requests/minute for authenticated users
      - 30 requests/minute for unauthenticated users
      - Separate limits for login endpoint (10/minute to prevent brute force)
    """

    def __init__(self, app, cache=None):
        super().__init__(app)
        self._cache = cache
        self._local_counts = {}  # Fallback if no Redis

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health checks and docs
        path = request.url.path
        if path in ("/api/health", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)

        # Determine rate limit
        client_ip = request.client.host if request.client else "unknown"
        
        # Stricter limit for auth endpoints
        if "/auth/login" in path:
            max_requests, window = 10, 60   # 10 login attempts per minute
        elif "/auth/" in path:
            max_requests, window = 30, 60   # 30 auth requests per minute
        else:
            max_requests, window = 120, 60  # 120 general requests per minute

        identifier = f"{client_ip}:{path.split('/')[2] if len(path.split('/')) > 2 else 'general'}"

        # Check rate limit
        if self._cache:
            result = await self._cache.rate_limit(identifier, max_requests, window)
        else:
            # Simple in-memory fallback
            now = time.time()
            key = f"rl:{identifier}"
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
