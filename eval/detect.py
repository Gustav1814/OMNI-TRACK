"""Person detection AP@0.5 on CAVIAR shopping-centre ground truth.

Proposal criterion: person detection > 85% mAP@0.5 (single class -> mAP == AP).

Method
  * Frozen sample: every --stride-th frame of every CAVIAR view (both cameras of
    all 26 sequences), empty frames included so false positives count.
  * GT boxes shorter than --min-height px (people far down the corridor, often
    only a few pixels tall) are "ignore" regions: a detection on them is neither
    a TP nor an FP, and unmatched detections shorter than --min-height are also
    ignored (Caltech/CityPersons protocol).
  * The detector runs exactly as the pipeline does for the person stream:
    classes=[0], iou=0.6, at the given imgsz, but with conf=0.001 so the full
    precision/recall curve is available. COCO 101-point interpolated AP.
  * Each --variant MODEL@IMGSZ is measured; the first one is the primary result.

Known GT limitation: CAVIAR labels only people relevant to the scenario, so a
few real people (e.g. inside the shop, reflections in the glass) are unlabelled
and show up as false positives. The number is reported as measured.

Usage
  OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/detect.py \
      --variant yolo11m.pt@960 --variant yolo11s.pt@640   # the defaults start with these
  # own-store CVAT export (YOLO txt layout, class 0 = person):
  python eval/detect.py --yolo-dir path/to/images_and_labels
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

import caviar
from common import (
    WEIGHTS_DIR,
    average_precision,
    base_parser,
    box_iou,
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

MANIFEST = "caviar_detect"


def build_manifest(stride: int, min_h: float) -> dict:
    items = []
    for seq in caviar.sequences():
        for view in caviar.VIEWS:
            gt = caviar.load_gt(seq, view)
            last = max(gt) + 1 if gt else 0  # GT XML covers every decoded frame (container frame count is unreliable)
            for f in range(stride // 2, last, stride):
                items.append({"seq": seq, "view": view, "frame": f})
    return {
        "dataset": "CAVIAR shopping centre (both views, 26 sequences)",
        "source": "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/",
        "license": "CAVIAR terms: free for research use with acknowledgement",
        "split": f"every {stride}th frame (offset {stride // 2}); GT h<{min_h:g}px = ignore",
        "params": {"stride": stride, "min_height_px": min_h},
        "items": items,
    }


def match_frame(pred_xyxy, pred_conf, gt_xyxy, min_h: float, iou_thr: float = 0.5):
    """Greedy score-ordered matching. Returns (scores, tp_flags, n_valid_gt) with ignored dets removed."""
    gt_h = gt_xyxy[:, 3] - gt_xyxy[:, 1] if len(gt_xyxy) else np.zeros(0)
    valid = gt_h >= min_h
    order = np.argsort(-pred_conf)
    ious = box_iou(pred_xyxy, gt_xyxy)
    used = np.zeros(len(gt_xyxy), dtype=bool)
    scores, tps = [], []
    for i in order:
        best, best_j = iou_thr, -1
        for j in np.where(valid & ~used)[0]:
            if ious[i, j] >= best:
                best, best_j = ious[i, j], j
        if best_j >= 0:
            used[best_j] = True
            scores.append(pred_conf[i])
            tps.append(True)
            continue
        # matches an ignore region, or too small to judge -> ignored
        if (len(gt_xyxy) and np.any(~valid & (ious[i] >= iou_thr))) or (pred_xyxy[i, 3] - pred_xyxy[i, 1]) < min_h:
            continue
        scores.append(pred_conf[i])
        tps.append(False)
    return scores, tps, int(valid.sum())


def load_yolo_dir(root: Path):
    """Images + YOLO-format labels (class 0 = person) as exported by CVAT."""
    import cv2  # noqa: PLC0415

    for img in sorted(p for p in root.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}):
        lbl = Path(str(img).replace("/images/", "/labels/")).with_suffix(".txt")
        frame = cv2.imread(str(img))
        h, w = frame.shape[:2]
        rows = []
        if lbl.is_file():
            for line in lbl.read_text().split("\n"):
                parts = line.split()
                if len(parts) >= 5 and int(float(parts[0])) == 0:
                    xc, yc, bw, bh = (float(v) for v in parts[1:5])
                    rows.append(((xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h))
        yield str(img.relative_to(root)), frame, np.asarray(rows, dtype=np.float64).reshape(-1, 4)


def evaluate(model, frames, imgsz: int, device: str, min_h: float, batch: int = 8):
    scores, tps, n_gt, n_frames, infer_s = [], [], 0, 0, 0.0
    buf: list[tuple[np.ndarray, np.ndarray]] = []

    def flush():
        nonlocal n_gt, n_frames, infer_s
        if not buf:
            return
        t = time.perf_counter()
        res = model.predict([b[0] for b in buf], imgsz=imgsz, conf=0.001, iou=0.6, classes=[0],
                            device=device, verbose=False, max_det=100)
        infer_s += time.perf_counter() - t
        for (_, gt), r in zip(buf, res):
            boxes = r.boxes
            s, tp, ng = match_frame(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(), gt, min_h)
            scores.extend(s)
            tps.extend(tp)
            n_gt += ng
            n_frames += 1
        buf.clear()

    for frame, gt in frames:
        buf.append((frame, gt))
        if len(buf) >= batch:
            flush()
    flush()
    ap = average_precision(scores, tps, n_gt)
    # operating point used by the pipeline (conf >= 0.5 counted as a person)
    s = np.asarray(scores)
    t = np.asarray(tps, dtype=bool)
    keep = s >= 0.5
    tp50, fp50 = int((t & keep).sum()), int((~t & keep).sum())
    return {
        "ap50": round(ap["ap"], 4),
        "max_recall": round(ap["max_recall"], 4),
        "precision_at_conf0.5": round(tp50 / max(tp50 + fp50, 1), 4),
        "recall_at_conf0.5": round(tp50 / max(n_gt, 1), 4),
        "n_frames": n_frames,
        "n_gt_boxes": n_gt,
        "ms_per_frame": round(1000 * infer_s / max(n_frames, 1), 1),
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--variant", action="append", default=None,
                   help="MODEL@IMGSZ, repeatable; first is primary (default: the shipped GPU config yolo11m.pt@960, then the shipped CPU config yolo11s.pt@640)")
    p.add_argument("--stride", type=int, default=150)
    p.add_argument("--min-height", type=float, default=24.0)
    p.add_argument("--batch", type=int, default=4)
    p.add_argument("--yolo-dir", type=Path, default=None, help="Evaluate an own-store CVAT/YOLO export instead")
    args = p.parse_args()
    # Shipped configs first (app.config PERSON_MODEL_GPU/IMGSZ_GPU, then _CPU), then
    # yolo11s@960 and the pre-remediation yolov8n@640 baseline for reference.
    variants = args.variant or ["yolo11m.pt@960", "yolo11s.pt@640", "yolo11s.pt@960", "yolov8n.pt@640"]
    started = time.time()
    cap_threads(args.threads)

    if args.yolo_dir:
        manifest = {"dataset": f"own labelled frames ({args.yolo_dir.name})", "split": "all", "items": []}
        dataset = {"name": manifest["dataset"], "split": "all", "n_items": None}
    else:
        manifest = load_or_freeze_manifest(MANIFEST, lambda: build_manifest(args.stride, args.min_height),
                                           refreeze=args.refreeze)
        dataset = manifest_ref(MANIFEST, manifest)
        args.min_height = manifest["params"]["min_height_px"]

    def frames():
        if args.yolo_dir:
            for _, frame, gt in load_yolo_dir(args.yolo_dir):
                yield frame, gt
            return
        by_clip: dict[tuple[str, str], set[int]] = {}
        for it in manifest["items"]:
            by_clip.setdefault((it["seq"], it["view"]), set()).add(it["frame"])
        for (seq, view), wanted in sorted(by_clip.items()):
            gt = caviar.load_gt(seq, view)
            for idx, frame in caviar.iter_frames(caviar.video_path(seq, view), wanted):
                rows = gt.get(idx, np.zeros((0, 5)))
                yield frame, rows[:, 1:5]

    with ml_lock():
        import torch  # noqa: F401,PLC0415
        from ultralytics import YOLO  # noqa: PLC0415

        torch_threads(args.threads)
        device = resolve_device(args.device)
        results = []
        for v in variants:
            name, _, size = v.partition("@")
            imgsz = int(size or 640)
            weight = WEIGHTS_DIR / name
            log(f"evaluating {name} @ {imgsz} on {device}")
            model = YOLO(str(weight))
            m = evaluate(model, frames(), imgsz, device, args.min_height, args.batch)
            m.update({"model": name, "imgsz": imgsz})
            log(f"  {m}")
            results.append(m)
            del model

    if not results or results[0]["n_frames"] == 0:
        raise SystemExit("No frames were evaluated; check the dataset paths/manifest.")
    primary = results[0]
    write_result(
        "detection",
        value=primary["ap50"],
        metrics={"primary": primary, "variants": results},
        dataset=dataset,
        model=model_info(WEIGHTS_DIR / primary["model"], imgsz=primary["imgsz"], conf=0.001, iou=0.6, classes=[0]),
        device=device,
        started=started,
        notes=[
            f"GT boxes shorter than {args.min_height:g}px are ignore regions (far-field people a few px tall).",
            "CAVIAR labels only scenario-relevant people; unlabelled real people count as false positives.",
            "MOT17 val and the 300 own-store CVAT frames are not yet included (no labels in repo); "
            "run with --yolo-dir once exported.",
        ],
    )


if __name__ == "__main__":
    main()
