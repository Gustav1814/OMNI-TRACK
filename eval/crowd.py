"""Crowd level accuracy and count error on the Mall dataset (Chen et al., BMVC 2012).

Proposal criterion: crowd density level classification accuracy > 85%.

Method
  * Frozen sample: every --stride-th of the 2,000 annotated Mall frames
    (640x480, 13-53 people per frame, every head annotated inside the ROI).
  * The person detector runs as the pipeline does (classes=[0], iou=0.6); a
    detection counts when conf >= --count-conf (the pipeline's 0.5 track/count
    threshold) and its head point (box centre-x, top + 8% of the height) lies
    inside the dataset's ROI mask - the same region the GT heads are in.
  * Levels come from the app's own classifier
    (app.ai.crowd_density.CrowdDensityEstimator.configure_zone + _classify),
    thresholds 10/30/60/90% of --capacity. GT level = the same classifier on the
    GT count, so level accuracy measures only counting error near thresholds.
  * Also reported: count MAE / RMSE, signed bias, and per-level support.

    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/crowd.py
"""

from __future__ import annotations

import time

import numpy as np

from common import (
    BACKEND,
    WEIGHTS_DIR,
    base_parser,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    model_info,
    resolve_device,
    torch_threads,
    write_result,
)

MALL = BACKEND / "storage" / "demo_videos" / "mall" / "mall_dataset"
MANIFEST = "mall_crowd"
LEVELS = ["empty", "low", "medium", "high", "critical"]


def build_manifest(stride: int) -> dict:
    import scipy.io as sio  # noqa: PLC0415

    gt = sio.loadmat(MALL / "mall_gt.mat")
    counts = gt["count"].ravel()
    items = [{"frame": f"seq_{i + 1:06d}.jpg", "count": int(counts[i])} for i in range(0, len(counts), stride)]
    return {
        "dataset": "Mall dataset (Chen et al. 2012), 2,000 annotated frames",
        "source": "https://personal.ie.cuhk.edu.hk/~ccloy/downloads_mall_dataset.html",
        "license": "Mall dataset: research use with citation",
        "split": f"every {stride}th frame",
        "params": {"stride": stride},
        "items": items,
    }


def head_points(xyxy: np.ndarray) -> np.ndarray:
    return np.stack([(xyxy[:, 0] + xyxy[:, 2]) / 2, xyxy[:, 1] + 0.08 * (xyxy[:, 3] - xyxy[:, 1])], axis=1)


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--variant", action="append", default=None, help="MODEL@IMGSZ, first is primary")
    p.add_argument("--stride", type=int, default=4)
    p.add_argument("--capacity", type=int, default=50, help="zone max_capacity (level thresholds 10/30/60/90%%)")
    p.add_argument("--count-conf", type=float, default=0.5)
    p.add_argument("--batch", type=int, default=4)
    args = p.parse_args()
    variants = args.variant or ["yolo11s.pt@960", "yolo11s.pt@640"]
    started = time.time()
    cap_threads(args.threads)
    manifest = load_or_freeze_manifest(MANIFEST, lambda: build_manifest(args.stride), refreeze=args.refreeze)

    import cv2  # noqa: PLC0415
    import scipy.io as sio  # noqa: PLC0415

    from app.ai.crowd_density import CrowdDensityEstimator  # noqa: PLC0415

    mask = sio.loadmat(MALL / "perspective_roi.mat")["roi"][0, 0]["mask"] > 0
    est = CrowdDensityEstimator()
    zone = est.configure_zone("mall-eval", camera_id=0, max_capacity=args.capacity)
    level = lambda n: est._classify(int(n), zone)  # noqa: E731 - the app's classifier
    gt = np.array([it["count"] for it in manifest["items"]])
    gt_lv = [level(n) for n in gt]

    results = []
    with ml_lock():
        from ultralytics import YOLO  # noqa: PLC0415

        torch_threads(args.threads)
        device = resolve_device(args.device)
        for v in variants:
            name, _, size = v.partition("@")
            imgsz = int(size or 640)
            log(f"evaluating {name} @ {imgsz} on {device}")
            model = YOLO(str(WEIGHTS_DIR / name))
            pred, t_inf = [], 0.0
            items = manifest["items"]
            for s in range(0, len(items), args.batch):
                imgs = [cv2.imread(str(MALL / "frames" / it["frame"])) for it in items[s:s + args.batch]]
                t = time.perf_counter()
                res = model.predict(imgs, imgsz=imgsz, conf=args.count_conf, iou=0.6, classes=[0],
                                    device=device, verbose=False, max_det=300)
                t_inf += time.perf_counter() - t
                for r in res:
                    hp = head_points(r.boxes.xyxy.cpu().numpy())
                    xs = np.clip(hp[:, 0].round().astype(int), 0, mask.shape[1] - 1)
                    ys = np.clip(hp[:, 1].round().astype(int), 0, mask.shape[0] - 1)
                    pred.append(int(mask[ys, xs].sum()))
            pred_a = np.asarray(pred)
            pr_lv = [level(n) for n in pred_a]
            err = pred_a - gt
            m = {
                "model": name,
                "imgsz": imgsz,
                "level_accuracy": round(float(np.mean([a == b for a, b in zip(gt_lv, pr_lv)])), 4),
                "count_mae": round(float(np.abs(err).mean()), 3),
                "count_rmse": round(float(np.sqrt((err ** 2).mean())), 3),
                "count_bias": round(float(err.mean()), 3),
                "gt_mean_count": round(float(gt.mean()), 2),
                "n_frames": len(pred_a),
                "ms_per_frame": round(1000 * t_inf / max(len(pred_a), 1), 1),
            }
            log(f"  {m}")
            results.append(m)
            del model

    primary = results[0]
    write_result(
        "crowd",
        value=primary["level_accuracy"],
        metrics={
            "primary": primary,
            "variants": results,
            "capacity": args.capacity,
            "thresholds": {"low": zone.threshold_low, "medium": zone.threshold_medium,
                           "high": zone.threshold_high, "critical": zone.threshold_critical},
            "gt_level_support": {lv: gt_lv.count(lv) for lv in LEVELS},
            "count_conf": args.count_conf,
        },
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(WEIGHTS_DIR / primary["model"], imgsz=primary["imgsz"], conf=args.count_conf, classes=[0]),
        device=device,
        started=started,
        notes=[
            "Level = app.ai.crowd_density classifier on the count (thresholds 10/30/60/90% of capacity); "
            "the GT level uses the same classifier on the GT head count.",
            "Mall is a dense overhead-ish mall view with heavy occlusion; detector-based counting "
            "undercounts occluded people (see count_bias).",
        ],
    )


if __name__ == "__main__":
    main()
