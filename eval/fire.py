"""Fire / smoke detection precision and false-alarm rate.

Proposal criterion: fire detection precision > 90% with false-positive rate < 5%.

Two parts, one model (the shipped fire-smoke.pt via the app's FireDetector
class map, imgsz 640 as in the pipeline):

1. D-Fire test split (frozen 1-in-3 subset, fire + smoke + negative images),
   image level: an image is predicted positive when the model returns any
   fire/smoke box at the operating confidence; it is truly positive when its
   label has any box. Precision, recall and FPR (on the negative images) are
   reported at the shipped confidence (FireDetector default 0.4) - the primary
   number - and at 0.5 / 0.6 for reference. Box-level AP@0.5 per class too.
2. CAVIAR shopping-centre clips (no fire anywhere = pure negatives), scanned at
   the pipeline's fire cadence (every FIRE_ACTIVE_INTERVAL_FRAMES frames):
   false alarms per hour for the instant rule (any box -> alert, what the app
   does today) and for a k-of-n confirmation rule (k positive scans out of the
   last n, then a cool-down) proposed in the remediation plan.

    python eval/fetch_datasets.py dfire
    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/fire.py
"""

from __future__ import annotations

import json
import time
from collections import deque

import numpy as np

import caviar
import fetch_datasets
from common import (
    DATASETS_DIR,
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

MANIFEST = "dfire_test"
DFIRE_DIR = DATASETS_DIR / "dfire" / "test"
DFIRE_NAMES = {0: "smoke", 1: "fire"}
SHIPPED_CONF = 0.4  # app.ai.fire_detector.FireDetector default (pipeline does not override it)
CAVIAR_FPS = 25.0


def build_manifest() -> dict:
    index = json.loads((DFIRE_DIR / "index.json").read_text())
    items = [{"row": int(k), **v} for k, v in sorted(index.items(), key=lambda kv: int(kv[0]))]
    return {
        "dataset": "D-Fire test split (de Venancio et al. 2022), 1-in-3 subset",
        "source": f"https://huggingface.co/datasets/{fetch_datasets.DFIRE['repo']} @ {fetch_datasets.DFIRE['revision']}",
        "license": "D-Fire: CC0 1.0 (github.com/gaiasd/DFireDataset)",
        "split": f"test rows with row_idx % {fetch_datasets.DFIRE['every']} == 0",
        "items": items,
    }


def parse_label(text: str, w: int, h: int) -> list[tuple[str, list[float]]]:
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 5:
            c, xc, yc, bw, bh = int(float(p[0])), *(float(v) for v in p[1:5])
            out.append((DFIRE_NAMES[c], [(xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h]))
    return out


def image_metrics(pred_max: np.ndarray, truth: np.ndarray, thr: float) -> dict:
    pos = pred_max >= thr
    tp, fp = int((pos & truth).sum()), int((pos & ~truth).sum())
    fn, tn = int((~pos & truth).sum()), int((~pos & ~truth).sum())
    return {
        "conf": thr,
        "precision": round(tp / max(tp + fp, 1), 4),
        "recall": round(tp / max(tp + fn, 1), 4),
        "fpr": round(fp / max(fp + tn, 1), 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def k_of_n_events(flags: list[bool], k: int, n: int, cooldown: int) -> int:
    """Count alarms: fire when >= k of the last n scans are positive, then stay silent `cooldown` scans."""
    window: deque[bool] = deque(maxlen=n)
    events, quiet = 0, 0
    for f in flags:
        window.append(f)
        if quiet:
            quiet -= 1
            continue
        if sum(window) >= k:
            events += 1
            quiet = cooldown
            window.clear()
    return events


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--weight", default="fire-smoke.pt")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--clear-s", type=float, default=30.0)
    p.add_argument("--scan-every", type=int, default=12, help="frames between fire scans (FIRE_ACTIVE_INTERVAL_FRAMES)")
    p.add_argument("--skip-caviar", action="store_true")
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    if not (DFIRE_DIR / "index.json").is_file():
        raise SystemExit("D-Fire subset missing: run python eval/fetch_datasets.py dfire")
    have, want = len(json.loads((DFIRE_DIR / "index.json").read_text())), fetch_datasets.dfire_expected()
    if have != want:
        raise SystemExit(f"D-Fire subset incomplete ({have}/{want} images): re-run "
                         "python eval/fetch_datasets.py dfire (it resumes)")
    manifest = load_or_freeze_manifest(MANIFEST, build_manifest, refreeze=args.refreeze)
    weight = WEIGHTS_DIR / args.weight
    if not weight.is_file():
        raise SystemExit(f"{weight} missing - see scripts/download_default_weights.py (custom weight)")

    import cv2  # noqa: PLC0415

    with ml_lock():
        from ultralytics import YOLO  # noqa: PLC0415

        torch_threads(args.threads)
        device = resolve_device(args.device)
        model = YOLO(str(weight))
        names = {i: n.lower() for i, n in model.names.items()}
        log(f"fire model classes: {names}")

        # ---- part 1: D-Fire image level + box AP
        pred_max, truth, per_type = [], [], []
        box_scores = {"fire": [], "smoke": []}
        box_tp = {"fire": [], "smoke": []}
        n_gt = {"fire": 0, "smoke": 0}
        t_inf = 0.0
        for it in manifest["items"]:
            img = cv2.imread(str(DFIRE_DIR / "images" / it["file"]))
            if img is None:
                raise SystemExit(f"unreadable image {it['file']}")
            h, w = img.shape[:2]
            gt = parse_label(it["label"], w, h)
            t = time.perf_counter()
            r = model.predict(img, imgsz=args.imgsz, conf=0.05, device=device, verbose=False)[0]
            t_inf += time.perf_counter() - t
            cls = [names[int(c)] for c in r.boxes.cls.cpu().numpy()]
            conf = r.boxes.conf.cpu().numpy()
            xyxy = r.boxes.xyxy.cpu().numpy()
            pred_max.append(float(conf.max()) if len(conf) else 0.0)
            truth.append(bool(gt))
            types = {c for c, _ in gt}
            per_type.append("fire" if "fire" in types else "smoke" if "smoke" in types else "none")
            for c in ("fire", "smoke"):
                g = np.array([b for cc, b in gt if cc == c]).reshape(-1, 4)
                n_gt[c] += len(g)
                sel = [i for i, cc in enumerate(cls) if cc == c]
                used = np.zeros(len(g), bool)
                ious = box_iou(xyxy[sel], g) if sel and len(g) else np.zeros((len(sel), len(g)))
                for j in np.argsort(-conf[sel]) if sel else []:
                    box_scores[c].append(float(conf[sel][j]))
                    cand = np.where(~used & (ious[j] >= 0.5))[0] if len(g) else []
                    if len(cand):
                        used[cand[np.argmax(ious[j][cand])]] = True
                        box_tp[c].append(True)
                    else:
                        box_tp[c].append(False)
        pm, tr = np.asarray(pred_max), np.asarray(truth)
        pt = np.asarray(per_type)
        ops = [image_metrics(pm, tr, c) for c in (SHIPPED_CONF, 0.5, 0.6)]
        shipped = ops[0]
        dfire = {
            "operating_points": ops,
            "recall_fire_images": round(float((pm[pt == "fire"] >= SHIPPED_CONF).mean()), 4),
            "recall_smoke_only_images": round(float((pm[pt == "smoke"] >= SHIPPED_CONF).mean()), 4),
            "ap50_fire": round(average_precision(box_scores["fire"], box_tp["fire"], n_gt["fire"])["ap"], 4),
            "ap50_smoke": round(average_precision(box_scores["smoke"], box_tp["smoke"], n_gt["smoke"])["ap"], 4),
            "n_images": len(pm),
            "n_positive": int(tr.sum()),
            "n_negative": int((~tr).sum()),
            "ms_per_image": round(1000 * t_inf / max(len(pm), 1), 1),
        }
        log(f"D-Fire: {dfire}")

        # ---- part 2: CAVIAR negatives, event level
        cav = None
        if not args.skip_caviar:
            flags_by_clip, n_scans = {}, 0
            for seq in caviar.sequences():
                for view in caviar.VIEWS:
                    gt = caviar.load_gt(seq, view)
                    wanted = set(range(0, max(gt) + 1 if gt else 0, args.scan_every))
                    fl = []
                    for _, frame in caviar.iter_frames(caviar.video_path(seq, view), wanted):
                        r = model.predict(frame, imgsz=args.imgsz, conf=SHIPPED_CONF, device=device, verbose=False)[0]
                        fl.append(len(r.boxes) > 0)
                    flags_by_clip[f"{seq}/{view}"] = fl
                    n_scans += len(fl)
            hours = n_scans * args.scan_every / CAVIAR_FPS / 3600
            cooldown = int(round(args.clear_s * CAVIAR_FPS / args.scan_every))
            instant = sum(sum(f) for f in flags_by_clip.values())
            kofn = sum(k_of_n_events(f, args.k, args.n, cooldown) for f in flags_by_clip.values())
            cav = {
                "video_hours": round(hours, 3),
                "n_scans": n_scans,
                "positive_scans": instant,
                "scan_fpr": round(instant / max(n_scans, 1), 5),
                "false_alarms_instant": instant,
                "false_alarms_instant_per_hour": round(instant / max(hours, 1e-9), 2),
                "false_alarms_k_of_n": kofn,
                "false_alarms_k_of_n_per_hour": round(kofn / max(hours, 1e-9), 2),
                "rule": f"{args.k} of {args.n} scans, {args.clear_s:g}s cool-down, scan every {args.scan_every} frames",
                "clips_with_alarm": sorted(k for k, f in flags_by_clip.items() if any(f)),
            }
            log(f"CAVIAR: {cav}")

    write_result(
        "fire",
        value=shipped["precision"],
        secondary_value=shipped["fpr"],
        metrics={"dfire": dfire, "caviar_negatives": cav},
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(weight, imgsz=args.imgsz, conf=SHIPPED_CONF, classes=names),
        device=device,
        started=started,
        notes=[
            "Primary = image-level precision on D-Fire at the shipped confidence 0.4; secondary = FPR on "
            "D-Fire negative images at the same threshold.",
            "fire-smoke.pt is a team-trained YOLOv8n (Roboflow fire-smoke data), not trained on D-Fire: "
            "cross-dataset test.",
            "The app currently alerts on every positive scan; the k-of-n numbers show what a confirmation "
            "rule would change on real store video (CAVIAR contains no fire, so every alarm is false).",
        ],
    )


if __name__ == "__main__":
    main()
