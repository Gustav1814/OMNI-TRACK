"""
Disk-space safeguards for footage writes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

from loguru import logger


class StorageGuard:
    def __init__(self, footage_dir: str, min_free_mb: int = 1024, retention_gb: int = 20):
        self.footage_dir = Path(footage_dir)
        self.min_free_mb = max(128, int(min_free_mb))
        self.retention_gb = max(1, int(retention_gb))
        self._task: asyncio.Task | None = None

    def _disk_free_mb(self) -> float:
        self.footage_dir.mkdir(parents=True, exist_ok=True)
        st = self.footage_dir.stat()
        # On Windows statvfs is unavailable; use shutil.
        import shutil

        usage = shutil.disk_usage(self.footage_dir)
        return usage.free / (1024 * 1024)

    def check_can_record(self) -> Dict[str, float | bool]:
        free_mb = self._disk_free_mb()
        return {"ok": free_mb >= self.min_free_mb, "free_mb": free_mb, "min_required_mb": float(self.min_free_mb)}

    async def start_retention_worker(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop_retention_worker(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._enforce_retention()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"StorageGuard retention loop failed: {e}")
            await asyncio.sleep(60)

    async def _enforce_retention(self) -> None:
        self.footage_dir.mkdir(parents=True, exist_ok=True)
        files = [p for p in self.footage_dir.iterdir() if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime)  # oldest first
        total = sum(p.stat().st_size for p in files)
        cap = self.retention_gb * 1024 * 1024 * 1024
        idx = 0
        while total > cap and idx < len(files):
            victim = files[idx]
            size = victim.stat().st_size
            victim.unlink(missing_ok=True)
            total -= size
            idx += 1
