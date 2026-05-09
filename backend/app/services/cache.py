"""
OmniTrack AI — Redis Cache Layer
Fast in-memory cache for:
  - Frequently accessed dashboard data (avoid DB round trips)
  - Rate limiting per user/IP
  - Session storage
  - Real-time pipeline state

WHAT YOU NEED:
  - Redis server running (Docker: docker run -d -p 6379:6379 redis:alpine)
  - Set REDIS_URL=redis://localhost:6379/0 in your .env
  - If Redis is unavailable, the system falls back gracefully (no crash)
"""

import json
import time
from typing import Optional, Any, Dict
from loguru import logger

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis package not installed — cache disabled")


class RedisCache:
    """
    Async Redis wrapper with JSON serialization and graceful fallback.
    
    Usage:
        cache = RedisCache("redis://localhost:6379/0")
        await cache.connect()
        
        # Store data with TTL (seconds)
        await cache.set("dashboard:overview", data, ttl=30)
        
        # Retrieve data
        data = await cache.get("dashboard:overview")
        
        # Rate limiting
        allowed = await cache.rate_limit("user:123", max_requests=60, window=60)
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._url = redis_url
        self._client = None
        self._connected = False
        self._fallback: Dict[str, Any] = {}  # In-memory fallback if Redis unavailable
        self._fallback_ttl: Dict[str, float] = {}

    async def connect(self) -> bool:
        """Connect to Redis. Returns True if successful."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis library not available — using in-memory fallback")
            return False

        try:
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            # Test connection
            await self._client.ping()
            self._connected = True
            logger.info(f"✅ Redis connected: {self._url}")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} — using in-memory fallback")
            self._connected = False
            return False

    async def disconnect(self):
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._connected = False

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Store a value with optional TTL (time-to-live in seconds).
        Default TTL: 5 minutes.
        """
        try:
            serialized = json.dumps(value, default=str)
            if self._connected and self._client:
                await self._client.setex(key, ttl, serialized)
                return True
            else:
                # Fallback: in-memory dict
                self._fallback[key] = serialized
                self._fallback_ttl[key] = time.time() + ttl
                return True
        except Exception as e:
            logger.debug(f"Cache set error ({key}): {e}")
            return False

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key. Returns None if not found or expired."""
        try:
            if self._connected and self._client:
                data = await self._client.get(key)
                if data:
                    return json.loads(data)
                return None
            else:
                # Fallback: check in-memory with TTL
                if key in self._fallback:
                    if time.time() < self._fallback_ttl.get(key, 0):
                        return json.loads(self._fallback[key])
                    else:
                        del self._fallback[key]
                        self._fallback_ttl.pop(key, None)
                return None
        except Exception as e:
            logger.debug(f"Cache get error ({key}): {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        try:
            if self._connected and self._client:
                await self._client.delete(key)
            else:
                self._fallback.pop(key, None)
                self._fallback_ttl.pop(key, None)
            return True
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        try:
            if self._connected and self._client:
                return await self._client.exists(key) > 0
            else:
                if key in self._fallback:
                    return time.time() < self._fallback_ttl.get(key, 0)
                return False
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────
    # RATE LIMITING
    # ─────────────────────────────────────────────────────────

    async def rate_limit(
        self,
        identifier: str,
        max_requests: int = 60,
        window: int = 60,
    ) -> Dict[str, Any]:
        """
        Sliding window rate limiter.
        
        Args:
            identifier: User ID, IP, or API key
            max_requests: Max requests allowed in the window
            window: Window size in seconds
        
        Returns:
            {
                "allowed": True/False,
                "remaining": int,
                "reset_in": float (seconds)
            }
        """
        key = f"ratelimit:{identifier}"
        now = time.time()

        try:
            if self._connected and self._client:
                # Use Redis sorted set for sliding window
                pipe = self._client.pipeline()
                pipe.zremrangebyscore(key, 0, now - window)
                pipe.zadd(key, {str(now): now})
                pipe.zcard(key)
                pipe.expire(key, window)
                results = await pipe.execute()
                current_count = results[2]
            else:
                # In-memory fallback
                if key not in self._fallback:
                    self._fallback[key] = []
                timestamps = [t for t in self._fallback.get(key, []) if t > now - window]
                timestamps.append(now)
                self._fallback[key] = timestamps
                current_count = len(timestamps)

            allowed = current_count <= max_requests
            return {
                "allowed": allowed,
                "remaining": max(0, max_requests - current_count),
                "reset_in": round(window - (now % window), 1),
                "limit": max_requests,
            }
        except Exception as e:
            logger.debug(f"Rate limit error: {e}")
            return {"allowed": True, "remaining": max_requests, "reset_in": window, "limit": max_requests}

    # ─────────────────────────────────────────────────────────
    # PIPELINE STATE (for dashboard real-time data)
    # ─────────────────────────────────────────────────────────

    async def cache_pipeline_state(self, state: Dict[str, Any]) -> bool:
        """Cache the current pipeline status for fast dashboard access."""
        return await self.set("pipeline:state", state, ttl=10)

    async def get_pipeline_state(self) -> Optional[Dict]:
        """Get cached pipeline state."""
        return await self.get("pipeline:state")

    async def cache_dashboard_overview(self, data: Dict[str, Any]) -> bool:
        """Cache dashboard overview data (refreshed every 15s)."""
        return await self.set("dashboard:overview", data, ttl=15)

    async def get_dashboard_overview(self) -> Optional[Dict]:
        """Get cached dashboard overview."""
        return await self.get("dashboard:overview")

    async def cache_vibe_score(self, score: float, label: str) -> bool:
        """Cache the current store vibe score."""
        return await self.set("vibe:current", {"score": score, "label": label}, ttl=30)

    async def get_vibe_score(self) -> Optional[Dict]:
        """Get cached vibe score."""
        return await self.get("vibe:current")

    # ─────────────────────────────────────────────────────────
    # HEALTH CHECK
    # ─────────────────────────────────────────────────────────

    async def health(self) -> Dict[str, Any]:
        """Check Redis health status."""
        if not self._connected:
            return {"status": "disconnected", "mode": "in-memory-fallback"}
        try:
            latency_start = time.time()
            await self._client.ping()
            latency = (time.time() - latency_start) * 1000
            info = await self._client.info(section="memory")
            return {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
