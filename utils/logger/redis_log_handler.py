import json
import logging
from typing import Any

from dtos.common.constants.api_config import API_NAME, MAX_LOG_ENTRIES, REDIS_LOG_PREFIX
from utils.services.logger.ilog_handler import ILogHandler
from utils.services.logger.log_levels import LogLevel

# Levels that get persisted to Redis (extend this set as needed).
_PERSISTED_LEVELS: frozenset[LogLevel] = frozenset({LogLevel.ERROR, LogLevel.CRITICAL})

# Redis cluster-safe key — hash tag keeps all log keys in the same slot.
_LOG_KEY = f"{{{API_NAME}}}:{REDIS_LOG_PREFIX}"


class RedisLogHandler(ILogHandler):
    def __init__(self, redis_client: Any, limit: int = MAX_LOG_ENTRIES) -> None:
        self._redis = redis_client
        self._limit = limit
        self._fallback_logger = logging.getLogger(f"{API_NAME}_Logger")

    async def emit(self, level: LogLevel, entry: dict) -> None:
        """Only persists levels that belong to ``_PERSISTED_LEVELS``."""
        if level not in _PERSISTED_LEVELS:
            return

        try:
            payload = {k: v for k, v in entry.items() if k != "formatted_message"}
            await self._redis.lpush(_LOG_KEY, json.dumps(payload, default=str))
            # Trim in the same round-trip — no separate while-loop needed.
            await self._redis.ltrim(_LOG_KEY, 0, self._limit - 1)
        except Exception as exc:
            self._fallback_logger.warning("Redis logging failed: %s", exc)

    async def get_recent_logs(self, count: int | None = None) -> list[dict]:
        """
        Return up to *count* most-recent log entries in chronological order.

        Uses LRANGE instead of rpop+lpush, which is both non-destructive
        and O(N) in a single network round-trip.
        """
        limit = min(count, self._limit) if count else self._limit
        try:
            raw_entries = await self._redis.lrange(_LOG_KEY, 0, limit - 1)
            # Redis returns newest-first (lpush); reverse for chronological order.
            return [json.loads(entry) for entry in reversed(raw_entries)]
        except Exception as exc:
            self._fallback_logger.error("Failed to retrieve logs from Redis: %s", exc)
            return []
