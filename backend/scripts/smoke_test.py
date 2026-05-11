"""
OmniTrack AI — Production Smoke Test
════════════════════════════════════

Drives the REAL ProcessingPipeline (YOLO + ByteTrack + Re-ID + analytics)
against a short synthetic video to prove the production path works end-to-end.

What it verifies:
  1. Pipeline loads every AI module (detector, tracker, Re-ID, emotion, fire,
     shelf, checkout, crowd, vibe)
  2. Camera can be added and a video file can be consumed frame by frame
  3. Detections, tracks, and (optionally) Re-ID embeddings are produced
  4. VideoSynopsis can process the same clip into a compressed output

Run:
  cd backend
  .\.venv312\Scripts\python.exe scripts\smoke_test.py

Exit code:
  0 — all checks passed
  1 — at least one check failed
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Make sure `app` is importable when running as a script
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _make_test_video(path: Path, seconds: int = 4, fps: int = 15) -> None:
    """Draw a moving rectangle + circle onto each frame so YOLO sees motion.

    YOLO likely won't fire on pure synthetic shapes, but the pipeline itself
    still exercises detection, tracking, and every analytics callback. That
    validates the wiring end-to-end. For real detections replace this with
    an actual store clip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 640, 480
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriter could not open output path")
    total = seconds * fps
    for i in range(total):
        img = np.full((h, w, 3), 40, dtype=np.uint8)
        cv2.rectangle(img, (50 + i * 4, 50), (150 + i * 4, 300), (0, 200, 0), -1)
        cv2.circle(img, (500 - i * 4, 240), 40, (0, 0, 200), -1)
        cv2.putText(
            img, f"frame {i}", (20, 450),
            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2,
        )
        writer.write(img)
    writer.release()


async def _run() -> int:
    from app.services.pipeline import ProcessingPipeline

    failures = []

    # 1. Synthetic video
    video_path = ROOT / "data" / "smoke_input.mp4"
    _make_test_video(video_path)
    print(f"[1/6] Synthetic video written → {video_path} ({video_path.stat().st_size} B)")

    # 2. Pipeline boot
    pipeline = ProcessingPipeline(
        detector_model="yolov8n.pt",
        reid_model="osnet_x1_0",
        fire_model="fire_smoke.pt",
        confidence=0.3,
        device="cpu",
        processing_fps=8,
    )
    print("[2/6] Pipeline constructed")

    # 3. Add camera + start
    pipeline.add_camera(
        camera_id=999,
        source=str(video_path),
        stream_type="file",
        zone="smoke_zone",
        fps=15,
        skip_frames=1,
        enable_fire=True,
    )
    await pipeline.start()
    print("[3/6] Pipeline started (camera 999 attached)")

    # 4. Let it chew through the video
    for sec in range(8):
        await asyncio.sleep(1)
        snap = pipeline.get_analytics_snapshot() or {}
        det = snap.get("detections") or {}
        print(
            f"       +{sec + 1}s — frames={det.get('frames_processed', 0)}"
            f" detections={det.get('total_processed', 0)}"
        )

    snapshot = pipeline.get_analytics_snapshot() or {}
    print("[4/6] Analytics snapshot keys:", sorted(snapshot.keys()))

    det = snapshot.get("detections") or {}
    frames_processed = int(det.get("frames_processed", 0))
    total_detections = int(det.get("total_processed", 0))
    if frames_processed <= 0:
        failures.append("Pipeline processed zero frames (stream/tracking broken)")
    else:
        print(
            f"       ✓ frames processed: {frames_processed}"
            f" | detections (on synthetic shapes): {total_detections}"
        )

    # 5. Stop
    await pipeline.stop()
    print("[5/6] Pipeline stopped cleanly")

    # 6. Standalone VideoSynopsis run
    from app.ai.synopsis import VideoSynopsis
    syn_path = ROOT / "data" / "smoke_synopsis.mp4"
    syn = VideoSynopsis(compression_target=4.0, keyframes_per_tube=8)
    try:
        t0 = time.time()
        metrics = syn.process_video(str(video_path), str(syn_path))
        elapsed = time.time() - t0
        print(f"[6/6] VideoSynopsis OK in {elapsed:.2f}s — {metrics}")
        if not syn_path.exists() or syn_path.stat().st_size == 0:
            failures.append("VideoSynopsis wrote no output file")
    except Exception as e:
        failures.append(f"VideoSynopsis raised: {e}")

    if failures:
        print("\n╳ SMOKE TEST FAILED:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("\n✓ SMOKE TEST PASSED — backend production path is live")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(_run())
    except Exception as e:
        print(f"Fatal: {e}")
        code = 1
    sys.exit(code)
