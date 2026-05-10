"""
Runtime memory pressure guard.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


@dataclass
class MemoryState:
    status: str = "unknown"   # ok | soft | hard | unknown
    rss_mb: float = 0.0
    available_mb: float = 0.0
    target_mb: float = 0.0


class MemoryGuard:
    def __init__(self, pipeline: Any, target_mb: int = 0):
        self.pipeline = pipeline
        self._target_mb = float(target_mb or 0)
        self.state = MemoryState()
        self._task: Optional[asyncio.Task] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "status": self.state.status,
            "rss_mb": round(self.state.rss_mb, 1),
            "available_mb": round(self.state.available_mb, 1),
            "target_mb": round(self.state.target_mb, 1),
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        if psutil is None:
            self.state.status = "unknown"
            return
        proc = psutil.Process()
        while True:
            try:
                vm = psutil.virtual_memory()
                rss_mb = proc.memory_info().rss / (1024 * 1024)
                avail_mb = vm.available / (1024 * 1024)
                target = self._target_mb or ((vm.total / (1024 * 1024)) * 0.85)

                self.state.rss_mb = rss_mb
                self.state.available_mb = avail_mb
                self.state.target_mb = target

                ratio = rss_mb / max(target, 1)
                if ratio >= 0.9:
                    self.state.status = "hard"
                    # Hard pressure: reject growth and drop cached detectors.
                    self.pipeline._detectors = dict(list(self.pipeline._detectors.items())[:1])
                elif ratio >= 0.7:
                    self.state.status = "soft"
                    # Soft pressure: trim detector cache and reduce processing fps gently.
                    self.pipeline._detectors = dict(list(self.pipeline._detectors.items())[:2])
                    self.pipeline.processing_fps = max(3, int(self.pipeline.processing_fps * 0.9))
                else:
                    self.state.status = "ok"
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"MemoryGuard loop failed: {e}")
            await asyncio.sleep(3.0)
