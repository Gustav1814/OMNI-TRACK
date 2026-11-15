"""Checkout queue-length error against hand-annotated clips.

Proposal criterion: queue-length MAE <= 1 person.

Needs annotated clips (none ship with the repo - the CAVIAR/Mall scenes have no
checkout). One JSON per clip in eval/annotations/checkout/ (or --annotations):

    {"video": "backend/storage/demo_videos/reference/checkout_queue.mp4",
     "lane_bbox": [x1, y1, x2, y2],          # lane region in pixels, as configured in the app
     "queue_length": {"0": 2, "5": 3, ...}}  # people in the lane at second t (count by eye)

For each clip the detector + the app's PersonTracker run every --step frames,
confirmed tracks are fed to app.ai.checkout_analytics.CheckoutAnalytics
exactly as the pipeline does, and the lane queue_length is compared with the
annotation at every annotated second. Without annotations the script exits
without writing a result (the Evaluation page then shows "not measured").

    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/checkout.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from common import (
    EVAL_DIR,
    REPO,
    WEIGHTS_DIR,
    base_parser,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    model_info,
    resolve_device,
    sha256_file,
    torch_threads,
    write_result,
)

ANNOTATIONS = EVAL_DIR / "annotations" / "checkout"
MANIFEST = "checkout_annotations"


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


def _video(ann: dict) -> Path:
    v = Path(ann["video"])
    return v if v.is_absolute() else REPO / v


def build_manifest(files: list[Path]) -> dict:
    items = []
    for f in files:
        ann = json.loads(f.read_text())
        items.append({"annotation": f.name, "sha256": sha256_file(f),
                      "video": ann["video"], "video_sha256": sha256_file(_video(ann))})
    return {"dataset": f"{len(files)} hand-annotated checkout clips", "split": "all",
            "source": _rel(files[0].parent), "items": items}


def run_clip(ann: dict, model, device: str, imgsz: int, step: int) -> list[tuple[int, int, int]]:
    import cv2  # noqa: PLC0415

    from app.ai.checkout_analytics import CheckoutAnalytics, CheckoutLane  # noqa: PLC0415
    from app.ai.tracker import PersonTracker  # noqa: PLC0415

    cap = cv2.VideoCapture(str(_video(ann)))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    tracker = PersonTracker(fps=fps / step)
    lanes = CheckoutAnalytics([CheckoutLane("lane", 0, list(ann["lane_bbox"]))])
    wanted = {int(k): int(v) for k, v in ann["queue_length"].items()}
    pairs, i = [], -1
    while True:
        ok = cap.grab()
        i += 1
        if not ok or i / fps > max(wanted) + 1:
            break
        if i % step:
            continue
        _, frame = cap.retrieve()
        r = model.predict(frame, imgsz=imgsz, conf=0.1, iou=0.6, classes=[0], device=device, verbose=False)[0]
        out = tracker.update(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), i / fps)
        tracks = [{"track_id": t.track_id, "bbox": t.bbox_xywh} for t in out.confirmed]
        res = lanes.update(0, tracks)
        sec = int(i / fps)
        if sec in wanted and (i - step) / fps < sec:  # first processed frame of an annotated second
            q = res["total_queue"]
            pairs.append((sec, wanted[sec], q))
    cap.release()
    return pairs


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    p.add_argument("--model", default="yolo11s.pt")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--step", type=int, default=2)
    args = p.parse_args()
    files = sorted(args.annotations.glob("*.json")) if args.annotations.is_dir() else []
    if not files:
        raise SystemExit(f"No checkout annotations in {args.annotations} - nothing measured (see the docstring).")
    manifest = load_or_freeze_manifest(MANIFEST, lambda: build_manifest(files), refreeze=args.refreeze)
    files = []
    for it in manifest["items"]:  # exactly the frozen annotations and clips
        f = args.annotations / it["annotation"]
        if not f.is_file() or sha256_file(f) != it["sha256"]:
            raise SystemExit(f"{f} is missing or changed since manifest {MANIFEST} was frozen (use --refreeze on purpose)")
        if sha256_file(_video(json.loads(f.read_text()))) != it["video_sha256"]:
            raise SystemExit(f"The clip of {f.name} changed since the manifest was frozen (use --refreeze on purpose)")
        files.append(f)
    started = time.time()
    cap_threads(args.threads)
    per_clip, errs = {}, []
    with ml_lock():
        from ultralytics import YOLO  # noqa: PLC0415

        torch_threads(args.threads)
        device = resolve_device(args.device)
        model = YOLO(str(WEIGHTS_DIR / args.model))
        for f in files:
            pairs = run_clip(json.loads(f.read_text()), model, device, args.imgsz, args.step)
            e = [abs(q - gt) for _, gt, q in pairs]
            errs += e
            per_clip[f.stem] = {"n_points": len(e), "mae": round(float(np.mean(e)), 3) if e else None}
            log(f"{f.stem}: {per_clip[f.stem]}")
    if not errs:
        raise SystemExit("Annotations matched no processed frames")
    mae = float(np.mean(errs))
    write_result(
        "checkout",
        value=mae,
        metrics={"queue_mae": round(mae, 3), "per_clip": per_clip, "n_points": len(errs)},
        dataset={**manifest_ref(MANIFEST, manifest), "n_points": len(errs)},
        model=model_info(WEIGHTS_DIR / args.model, imgsz=args.imgsz, tracker="PersonTracker"),
        device=device,
        started=started,
    )


if __name__ == "__main__":
    main()
