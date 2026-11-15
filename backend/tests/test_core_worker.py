"""Shared inference worker: cross-camera detect batching and no specialist starvation."""

import asyncio
import time

import numpy as np

from app.services.pipeline import InferenceWorker


class _FakeDetector:
    def __init__(self, ms: float = 40.0):
        self.ms = ms
        self.batches = []

    def predict_batch(self, frames):
        self.batches.append(len(frames))
        time.sleep(self.ms / 1000.0)
        return [f"det{i}" for i in range(len(frames))]


def _frame():
    return np.zeros((4, 4, 3), np.uint8)


def test_detect_jobs_are_batched_across_cameras():
    async def main():
        det = _FakeDetector()
        w = InferenceWorker(det, max_batch=8)
        w.start()
        try:
            # first job occupies the worker; the next three queue up and must run as one batch
            first = asyncio.ensure_future(w.detect(_frame()))
            await asyncio.sleep(0.01)
            rest = await asyncio.gather(*(w.detect(_frame()) for _ in range(3)))
            await first
        finally:
            w.stop()
        return det.batches, rest

    batches, rest = asyncio.run(main())
    assert batches[-1] == 3, batches
    assert all(r[0].startswith("det") for r in rest)


def test_specialist_runs_while_detection_saturates_worker():
    async def main():
        # max_batch=1 + two cameras: a detect job is always queued when the worker frees up,
        # which is what out-of-phase cameras do on a saturated CPU.
        det = _FakeDetector(ms=40.0)
        w = InferenceWorker(det, max_batch=1)
        w.start()
        stop = time.perf_counter() + 3.0

        async def camera():
            while time.perf_counter() < stop:
                await w.detect(_frame())

        cams = [asyncio.ensure_future(camera()) for _ in range(2)]
        await asyncio.sleep(0.1)
        t0 = time.perf_counter()
        res, wait_ms, _ = await w.run(lambda: "fire-ok", prio=InferenceWorker.PRIO_SPECIALIST)
        waited = time.perf_counter() - t0
        await asyncio.gather(*cams)
        stats = w.stats()
        w.stop()
        return res, waited, stats

    res, waited, stats = asyncio.run(main())
    assert res == "fire-ok"
    assert waited < 1.5, waited  # strict priority would hold it until the cameras stop (~2.9 s)
    assert stats["starved_served"].get("2") == 1, stats


def test_stop_fails_queued_jobs():
    async def main():
        det = _FakeDetector(ms=200.0)
        w = InferenceWorker(det, max_batch=1)
        w.start()
        a = asyncio.ensure_future(w.detect(_frame()))
        await asyncio.sleep(0.02)
        b = asyncio.ensure_future(w.run(lambda: 1, prio=InferenceWorker.PRIO_SPECIALIST))
        await asyncio.sleep(0.02)
        await asyncio.get_running_loop().run_in_executor(None, w.stop)
        await asyncio.sleep(0.05)
        return await asyncio.gather(a, b, return_exceptions=True)

    a, b = asyncio.run(main())
    assert isinstance(b, RuntimeError), b
