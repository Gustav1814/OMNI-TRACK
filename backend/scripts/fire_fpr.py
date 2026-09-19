"""
OmniTrack AI — Fire/smoke false-positive sweep
══════════════════════════════════════════════

Covers the NEGATIVE half of proposal criterion 9 (> 90% precision, < 5% FPR).

Why this can be run without annotating anything: the clips passed in must
contain no fire and no smoke, so every detection is a false positive by
construction. Labelling is free; only the choice of clip matters.

What it does NOT do is measure precision or recall. That needs footage
containing real fire, and without it there is no way to know what raising the
threshold costs in sensitivity. Report the two halves separately.

Frames are downscaled to DECODE_IMGSZ first, because that is what the pipeline
actually hands the detector — measuring on full-resolution frames would answer
a question the system never asks.

Usage
-----
    cd backend
    ../.venv/Scripts/python.exe scripts/fire_fpr.py
    ../.venv/Scripts/python.exe scripts/fire_fpr.py --clips a.mp4 b.mp4 --samples 100
    ../.venv/Scripts/python.exe scripts/fire_fpr.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
from ultralytics import YOLO  # noqa: E402

from app.config import settings, resolved_footage_dir  # noqa: E402

THRESHOLDS = (0.25, 0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80)
FIRE_CLASSES = ("fire", "smoke")


def default_clips() -> List[Path]:
    """Whatever fire-free footage this checkout happens to have."""
    found: List[Path] = []
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "emotion test.mp4",):
        if candidate.is_file():
            found.append(candidate)
    footage = resolved_footage_dir()
    if footage.is_dir():
        found.extend(sorted(footage.glob("*.mp4"))[:3])
    return found


def sweep_clip(model: YOLO, names: Dict[int, str], path: Path, samples: int,
               decode: int) -> Dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return {"clip": path.name, "error": "unreadable or empty"}

    scores: List[float] = []
    frames_seen = 0
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * i / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        if decode:
            h, w = frame.shape[:2]
            if max(h, w) > decode:
                s = decode / max(h, w)
                frame = cv2.resize(frame, (int(w * s), int(h * s)),
                                   interpolation=cv2.INTER_AREA)
        frames_seen += 1
        # Sweep from well below any sane operating point, then threshold in
        # arithmetic rather than re-running the model once per candidate.
        for result in model.predict(source=frame, conf=0.10, verbose=False):
            if result.boxes is None:
                continue
            for box in result.boxes:
                if names.get(int(box.cls[0]), "") in FIRE_CLASSES:
                    scores.append(float(box.conf[0]))
    cap.release()

    return {
        "clip": path.name,
        "frames_sampled": frames_seen,
        "false_detections": {
            f"{t:.2f}": sum(1 for c in scores if c >= t) for t in THRESHOLDS
        },
        "frames_pct": {
            f"{t:.2f}": round(100.0 * sum(1 for c in scores if c >= t) / max(frames_seen, 1), 1)
            for t in THRESHOLDS
        },
        "highest_false_score": round(max(scores), 3) if scores else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clips", nargs="*", type=Path,
                    help="Clips containing NO fire and NO smoke.")
    ap.add_argument("--samples", type=int, default=60,
                    help="Frames sampled per clip (default 60).")
    ap.add_argument("--json", type=Path, help="Write results here.")
    args = ap.parse_args()

    clips = args.clips or default_clips()
    if not clips:
        print("No clips given and none found. Pass --clips <file> ...")
        return 1

    model = YOLO(settings.FIRE_MODEL_PATH)
    names = {int(k): str(v).lower() for k, v in (model.names or {}).items()}
    decode = int(getattr(settings, "DECODE_IMGSZ", 0) or 0)
    operating = float(settings.FIRE_CONFIDENCE)

    print(f"model            : {settings.FIRE_MODEL_PATH}")
    print(f"classes          : {names}")
    print(f"decode long side : {decode or 'native'}")
    print(f"FIRE_CONFIDENCE  : {operating}")
    if not any(c in names.values() for c in FIRE_CLASSES):
        print("\nThis model has no fire or smoke class — nothing to measure.")
        return 1

    results = [sweep_clip(model, names, Path(c), args.samples, decode) for c in clips]
    results = [r for r in results if "error" not in r]
    if not results:
        print("\nNo clip could be read.")
        return 1

    width = max(len(r["clip"]) for r in results) + 2
    print("\n" + "=" * (12 + width * len(results)))
    print(f"{'threshold':>10}  " + "".join(f"{r['clip']:>{width}}" for r in results))
    print("-" * (12 + width * len(results)))
    for t in THRESHOLDS:
        key = f"{t:.2f}"
        row = "".join(
            f"{str(r['false_detections'][key]) + ' (' + str(r['frames_pct'][key]) + '%)':>{width}}"
            for r in results
        )
        mark = "  <-- operating point" if abs(t - operating) < 1e-6 else ""
        print(f"{t:>10.2f}  " + row + mark)

    print("\nEvery detection above is a FALSE POSITIVE: none of these clips "
          "contain fire.")
    for r in results:
        print(f"  {r['clip']}: {r['frames_sampled']} frames, "
              f"highest false score {r['highest_false_score']}")

    at_operating = sum(r["false_detections"][f"{operating:.2f}"] for r in results)
    frames = sum(r["frames_sampled"] for r in results)
    print(f"\nAt the operating threshold {operating}: {at_operating} false alerts "
          f"across {frames} fire-free frames "
          f"({100.0 * at_operating / max(frames, 1):.1f}% of frames).")
    print("Precision and recall are NOT measured here — that needs footage "
          "containing real fire.")

    if args.json:
        args.json.write_text(json.dumps({
            "model": str(settings.FIRE_MODEL_PATH),
            "decode_imgsz": decode,
            "operating_threshold": operating,
            "clips": results,
        }, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
