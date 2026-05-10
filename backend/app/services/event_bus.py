"""
Pluggable event bus abstraction.

Default backend is Redis Streams for lightweight laptop operation.
Kafka backend is optional and loaded only when configured.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover
    aioredis = None


EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass
class EventMessage:
    stream: str
    key: str
    payload: Dict[str, Any]
    timestamp: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stream": self.stream,
            "key": self.key,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


class EventBus:
    async def publish(self, stream: str, key: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def subscribe(self, stream: str, group: str, consumer: str, handler: EventHandler) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class NullBus(EventBus):
    async def publish(self, stream: str, key: str, payload: Dict[str, Any]) -> None:
        return None

    async def subscribe(self, stream: str, group: str, consumer: str, handler: EventHandler) -> None:
        return None


class RedisStreamBus(EventBus):
    def __init__(self, redis_url: str):
        self._url = redis_url
        self._client = aioredis.from_url(redis_url, decode_responses=True) if aioredis else None
        self._tasks: list[asyncio.Task] = []

    async def publish(self, stream: str, key: str, payload: Dict[str, Any]) -> None:
        if not self._client:
            return
        msg = EventMessage(
            stream=stream,
            key=key,
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        try:
            await self._client.xadd(stream, {"k": key, "v": json.dumps(msg.as_dict(), default=str)}, maxlen=10_000)
        except Exception as e:
            logger.debug(f"RedisStream publish failed ({stream}): {e}")

    async def subscribe(self, stream: str, group: str, consumer: str, handler: EventHandler) -> None:
        if not self._client:
            return
        try:
            await self._client.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception:
            pass

        async def _loop() -> None:
            while True:
                try:
                    rows = await self._client.xreadgroup(group, consumer, {stream: ">"}, count=25, block=2000)
                    for _s, entries in rows:
                        for msg_id, fields in entries:
                            raw = fields.get("v")
                            if raw:
                                await handler(json.loads(raw))
                            await self._client.xack(stream, group, msg_id)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"RedisStream subscribe loop error ({stream}): {e}")
                    await asyncio.sleep(0.5)

        self._tasks.append(asyncio.create_task(_loop()))

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client:
            await self._client.close()


class KafkaBus(EventBus):
    """
    Optional backend using aiokafka.
    Loaded lazily so laptop profile does not need kafka deps.
    """

    def __init__(self, bootstrap_servers: str):
        self._bootstrap = bootstrap_servers
        self._producer = None
        self._consumers = []

    async def _ensure_producer(self):
        if self._producer is not None:
            return
        try:
            from aiokafka import AIOKafkaProducer
        except Exception as e:  # pragma: no cover
            logger.warning(f"Kafka backend requested but aiokafka missing: {e}")
            return
        self._producer = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
        await self._producer.start()

    async def publish(self, stream: str, key: str, payload: Dict[str, Any]) -> None:
        await self._ensure_producer()
        if self._producer is None:
            return
        msg = EventMessage(
            stream=stream,
            key=key,
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        try:
            await self._producer.send_and_wait(stream, json.dumps(msg.as_dict(), default=str).encode("utf-8"), key=key.encode("utf-8"))
        except Exception as e:
            logger.debug(f"Kafka publish failed ({stream}): {e}")

    async def subscribe(self, stream: str, group: str, consumer: str, handler: EventHandler) -> None:
        try:
            from aiokafka import AIOKafkaConsumer
        except Exception as e:  # pragma: no cover
            logger.warning(f"Kafka backend requested but aiokafka missing: {e}")
            return
        c = AIOKafkaConsumer(
            stream,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        await c.start()
        self._consumers.append(c)

        async def _loop() -> None:
            try:
                async for msg in c:
                    await handler(json.loads(msg.value.decode("utf-8")))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Kafka subscribe loop error ({stream}): {e}")

        self._consumers.append(asyncio.create_task(_loop()))

    async def close(self) -> None:
        for c in self._consumers:
            if isinstance(c, asyncio.Task):
                c.cancel()
            else:
                try:
                    await c.stop()
                except Exception:
                    pass
        if self._producer is not None:
            await self._producer.stop()
