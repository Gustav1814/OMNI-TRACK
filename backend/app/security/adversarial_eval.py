"""
OmniTrack AI — adversarial robustness of the REAL person detector (ART).

What it measures
----------------
* Model: an Ultralytics YOLO weight from ``model_weight/`` (default: the shipped person
  detector for the device, ``settings.person_model_for``),
  wrapped by ART's ``PyTorchYolo(is_ultralytics=True)`` so gradients come from
  the detector's own training loss (box + cls + DFL) — no surrogate network.
* Data: COCO val2017 images that contain at least one non-crowd person, with
  the official COCO annotations (HF mirror ``LibreYOLO/coco-val2017-mini500``,
  a deterministic slice of the real val2017 release). Cached under
  ``storage/datasets/coco_val2017_person/``.
* Attacks: ART FastGradientMethod (FGSM) and ProjectedGradientDescent (PGD),
  untargeted, L-inf, eps default 8/255, against the person ground truth.
  Adversarial images are quantised to uint8 before evaluation (what a real
  frame would carry).
* Metric: person AP@0.5 (COCO 101-point interpolation, crowd boxes ignored)
  on clean / FGSM / PGD images, plus the absolute and relative drop.
* Defences (ART preprocessors): JPEG compression (q=75) and 3x3 spatial
  (median) smoothing, applied at inference to clean and adversarial images.

BatchNorm layers are held in eval mode during the attack so the gradient is
that of the deployed network (ART switches the model to train mode to obtain
losses, which would otherwise use per-batch statistics and corrupt running
stats).

CLI (writes JSON, referenced by eval/adversarial.py):
    python -m app.security.adversarial_eval --images 10 --out storage/security/adversarial_report.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = BACKEND_ROOT / "storage" / "security" / "adversarial_report.json"
DATASET_DIR = BACKEND_ROOT / "storage" / "datasets" / "coco_val2017_person"
HF_BASE = "https://huggingface.co/datasets/LibreYOLO/coco-val2017-mini500/resolve/main"
ANN_REL = "annotations/instances_val2017_mini500.json"
IMGSZ = 640
PERSON = 0  # COCO category id 1 -> YOLO class 0


def art_available() -> bool:
    return importlib.util.find_spec("art") is not None


def get_robustness_status() -> Dict[str, Any]:
    """Cheap status for the API: whether ART is installed and the last real report."""
    report = None
    if REPORT_PATH.is_file():
        try:
            report = json.loads(REPORT_PATH.read_text())
        except (OSError, ValueError):
            report = None
    return {
        "art_available": art_available(),
        "status": (report or {}).get("status", "no_data"),
        "report": report,
        "report_path": str(REPORT_PATH.relative_to(BACKEND_ROOT)),
        "method": "ART PyTorchYolo (ultralytics loss) + FGSM/PGD, person AP@0.5 on COCO val2017",
    }


# ───────────────────────────── dataset ─────────────────────────────

def _download(url: str, dest: Path, max_bytes: int = 20 << 20) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "omnitrack-eval/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"Download larger than expected: {url}")
            f.write(chunk)
    os.replace(tmp, dest)


def ensure_person_subset(n_images: int) -> List[Dict[str, Any]]:
    """Return [{file, path, boxes:[[x1,y1,x2,y2]...], crowd:[...]}] for the first
    ``n_images`` val2017 images (by id) with >=1 non-crowd person."""
    ann_path = DATASET_DIR / "instances_val2017_mini500.json"
    if not ann_path.is_file():
        _download(f"{HF_BASE}/{ANN_REL}", ann_path)
    coco = json.loads(ann_path.read_text())
    person_cat = next(c["id"] for c in coco["categories"] if c["name"] == "person")
    per_img: Dict[int, Dict[str, list]] = {}
    for a in coco["annotations"]:
        if a["category_id"] != person_cat:
            continue
        x, y, w, h = a["bbox"]
        slot = per_img.setdefault(a["image_id"], {"boxes": [], "crowd": []})
        (slot["crowd"] if a.get("iscrowd") else slot["boxes"]).append([x, y, x + w, y + h])
    images = {im["id"]: im for im in coco["images"]}
    chosen = [i for i in sorted(per_img) if per_img[i]["boxes"]][:n_images]
    out = []
    for img_id in chosen:
        fname = images[img_id]["file_name"]
        dest = DATASET_DIR / "images" / fname
        if not dest.is_file():
            _download(f"{HF_BASE}/images/val2017/{fname}", dest)
        out.append({"image_id": img_id, "file": fname, "path": str(dest), **per_img[img_id]})
    return out


def _letterbox(img_rgb, boxes, crowd):
    import cv2
    import numpy as np

    h, w = img_rgb.shape[:2]
    r = IMGSZ / max(h, w)
    nw, nh = int(round(w * r)), int(round(h * r))
    canvas = np.full((IMGSZ, IMGSZ, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = cv2.resize(img_rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    scale = lambda bs: (np.asarray(bs, dtype=np.float32).reshape(-1, 4) * r)  # noqa: E731
    return canvas, scale(boxes), scale(crowd)


# ───────────────────────────── metric ─────────────────────────────

def _iou_matrix(a, b):
    import numpy as np

    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def person_ap50(preds: List[Tuple[Any, Any]], gts: List[Tuple[Any, Any]]) -> Dict[str, float]:
    """COCO-style AP@0.5 (101-pt) for one class. preds[i]=(boxes, scores); gts[i]=(boxes, crowd)."""
    import numpy as np

    records, n_gt = [], 0
    for (pb, ps), (gb, gc) in zip(preds, gts):
        n_gt += len(gb)
        order = np.argsort(-ps)
        pb, ps = pb[order], ps[order]
        matched = np.zeros(len(gb), dtype=bool)
        ious = _iou_matrix(pb, gb)
        ious_c = _iou_matrix(pb, gc)
        for k in range(len(pb)):
            best, j = 0.5, -1
            for g in range(len(gb)):
                if not matched[g] and ious[k, g] >= best:
                    best, j = ious[k, g], g
            if j >= 0:
                matched[j] = True
                records.append((ps[k], 1))
            elif len(gc) and ious_c[k].max() >= 0.5:
                continue  # matched a crowd region: ignored, as in COCO
            else:
                records.append((ps[k], 0))
    if n_gt == 0:
        return {"ap50": float("nan"), "recall": float("nan"), "n_gt": 0, "n_det": 0}
    records.sort(key=lambda t: -t[0])
    tp = np.cumsum([r[1] for r in records]) if records else np.zeros(0)
    fp = np.cumsum([1 - r[1] for r in records]) if records else np.zeros(0)
    recall = tp / n_gt
    precision = tp / np.maximum(tp + fp, 1e-9)
    prec_env = np.maximum.accumulate(precision[::-1])[::-1] if len(precision) else precision
    ap = 0.0
    for t in np.linspace(0, 1, 101):
        idx = np.searchsorted(recall, t, side="left")
        ap += prec_env[idx] if idx < len(prec_env) else 0.0
    return {
        "ap50": round(float(ap / 101), 4),
        "recall": round(float(recall[-1]) if len(recall) else 0.0, 4),
        "n_gt": int(n_gt),
        "n_det": int(len(records)),
    }


# ───────────────────────────── evaluation ─────────────────────────────

def _pick_device(requested: str) -> str:
    import torch

    if requested in ("cpu", "cuda"):
        return requested if requested == "cpu" or torch.cuda.is_available() else "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def run_detector_robustness_eval(
    model_name: Optional[str] = None,
    n_images: int = 10,
    eps: float = 8 / 255,
    pgd_steps: int = 10,
    device: str = "auto",
    jpeg_quality: int = 75,
) -> Dict[str, Any]:
    if not art_available():
        return {"status": "no_data", "reason": "adversarial-robustness-toolbox not installed"}

    import numpy as np
    import torch
    import cv2
    import art
    from art.attacks.evasion import FastGradientMethod, ProjectedGradientDescent
    from art.defences.preprocessor import JpegCompression, SpatialSmoothing
    from art.estimators.object_detection import PyTorchYolo
    from ultralytics import YOLO

    from app.security.input_safety import validate_model_name

    t0 = time.monotonic()
    dev = _pick_device(device)
    if not model_name:  # evaluate the detector the pipeline actually runs on this device
        from app.config import settings

        model_name = Path(settings.person_model_for(dev)[0]).name
    weights = validate_model_name(model_name)
    samples = ensure_person_subset(n_images)
    if not samples:
        return {"status": "no_data", "reason": "no COCO person images available"}

    xs, gts = [], []
    for s in samples:
        bgr = cv2.imread(s["path"])
        if bgr is None:
            continue
        lb, boxes, crowd = _letterbox(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), s["boxes"], s["crowd"])
        xs.append(lb)
        gts.append((boxes, crowd))
    x_clean = np.stack(xs).astype(np.float32).transpose(0, 3, 1, 2) / 255.0  # NCHW [0,1]

    # ── ART estimator over the real detector ──
    net = YOLO(str(weights)).model
    net.to(dev).float()
    for p in net.parameters():
        p.requires_grad_(False)
    estimator = PyTorchYolo(
        model=net, input_shape=(3, IMGSZ, IMGSZ), clip_values=(0.0, 1.0), channels_first=True,
        attack_losses=("loss_total",), device_type="gpu" if dev == "cuda" else "cpu",
        is_ultralytics=True, model_name=Path(model_name).stem,
    )
    wrapper = estimator.model

    # ultralytics >= 8.4 returns loss components as a dict; ART's YOLO wrapper indexes them 0/1/2.
    _orig_loss = net.loss

    def _loss_tuple(batch, preds=None):
        loss, items = _orig_loss(batch, preds)
        if isinstance(items, dict):
            items = tuple(items.values())
        return loss, items

    net.loss = _loss_tuple
    _orig_train = wrapper.train

    def _train_bn_frozen(mode: bool = True):
        _orig_train(mode)
        for m in wrapper.modules():
            if isinstance(m, torch.nn.modules.batchnorm._BatchNorm):
                m.eval()
        return wrapper

    wrapper.train = _train_bn_frozen  # gradient of the deployed (eval-BN) network

    labels = [
        {"boxes": torch.as_tensor(b, dtype=torch.float32), "labels": torch.zeros(len(b), dtype=torch.int64)}
        for b, _ in gts
    ]

    def attack(kind: str) -> np.ndarray:
        outs = []
        for i in range(len(x_clean)):  # batch 1 bounds memory
            xi, yi = x_clean[i:i + 1], [labels[i]]
            if kind == "fgsm":
                atk = FastGradientMethod(estimator, norm=np.inf, eps=eps, batch_size=1)
            else:
                atk = ProjectedGradientDescent(
                    estimator, norm=np.inf, eps=eps, eps_step=max(eps / 4, 1 / 255),
                    max_iter=pgd_steps, batch_size=1, verbose=False,
                )
            outs.append(atk.generate(x=xi, y=yi))
        adv = np.concatenate(outs, axis=0)
        return np.round(np.clip(adv, 0, 1) * 255.0) / 255.0  # uint8 quantisation

    t_attack = time.monotonic()
    x_fgsm = attack("fgsm")
    x_pgd = attack("pgd")
    attack_seconds = time.monotonic() - t_attack

    # ── Inference with the stock Ultralytics predictor (letterboxed 640 input) ──
    yolo = YOLO(str(weights))

    def detect(batch: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
        res = []
        for img in batch:
            bgr = cv2.cvtColor((img.transpose(1, 2, 0) * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
            r = yolo.predict(bgr, imgsz=IMGSZ, conf=0.001, iou=0.7, classes=[PERSON],
                             device=dev, verbose=False, max_det=300)[0]
            b = r.boxes
            res.append((b.xyxy.cpu().numpy().astype(np.float32), b.conf.cpu().numpy().astype(np.float32)))
        return res

    jpeg = JpegCompression(clip_values=(0.0, 1.0), quality=jpeg_quality, channels_first=True, apply_predict=True)
    smooth = SpatialSmoothing(window_size=3, channels_first=True, clip_values=(0.0, 1.0), apply_predict=True)

    def evaluate(batch: np.ndarray) -> Dict[str, float]:
        return person_ap50(detect(batch), gts)

    sets = {"clean": x_clean, "fgsm": x_fgsm, "pgd": x_pgd}
    results: Dict[str, Dict[str, Any]] = {k: evaluate(v) for k, v in sets.items()}
    defences: Dict[str, Dict[str, Any]] = {}
    for name, d in ((f"jpeg_q{jpeg_quality}", jpeg), ("median_3x3", smooth)):
        defences[name] = {k: evaluate(d(v)[0].astype(np.float32)) for k, v in sets.items()}

    linf = lambda a: float(np.abs(a - x_clean).max() * 255)  # noqa: E731
    clean_ap = results["clean"]["ap50"]

    def drop(ap: float) -> Dict[str, float]:
        return {
            "abs": round(clean_ap - ap, 4),
            "rel_pct": round(100.0 * (clean_ap - ap) / clean_ap, 2) if clean_ap > 0 else None,
        }

    report = {
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": weights.name,
        "device": dev,
        "imgsz": IMGSZ,
        "art_version": art.__version__,
        "estimator": "art.estimators.object_detection.PyTorchYolo(is_ultralytics=True), BN frozen",
        "dataset": {
            "name": "COCO val2017 person subset",
            "source": "huggingface.co/datasets/LibreYOLO/coco-val2017-mini500 (official val2017 images + annotations)",
            "images": len(x_clean),
            "image_ids": [s["image_id"] for s in samples][: len(x_clean)],
            "person_boxes": results["clean"]["n_gt"],
        },
        "metric": "person AP@0.5 (COCO 101-point, crowd ignored)",
        "params": {"eps": round(eps, 6), "eps_255": round(eps * 255, 2), "pgd_steps": pgd_steps,
                   "pgd_eps_step": round(max(eps / 4, 1 / 255), 6), "norm": "Linf", "targeted": False},
        "clean": results["clean"],
        "fgsm": {**results["fgsm"], "drop": drop(results["fgsm"]["ap50"]), "linf_255": round(linf(x_fgsm), 2)},
        "pgd": {**results["pgd"], "drop": drop(results["pgd"]["ap50"]), "linf_255": round(linf(x_pgd), 2)},
        "defences": {
            name: {k: {**v, "drop_vs_clean": drop(v["ap50"])} for k, v in res.items()}
            for name, res in defences.items()
        },
        "timing": {"attack_seconds": round(attack_seconds, 1), "total_seconds": round(time.monotonic() - t0, 1)},
        "notes": [
            "Smoke-scale sample; AP over few images has wide confidence intervals.",
            "Defences are evaluated against non-adaptive attacks (attacker unaware of the defence).",
        ],
    }
    return report


def write_report(report: Dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2))
    os.replace(tmp, out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ART FGSM/PGD robustness of the OmniTrack person detector")
    ap.add_argument("--model", default=None,
                    help="weight file name inside model_weight/ (default: the shipped person model for the device)")
    ap.add_argument("--images", type=int, default=10)
    ap.add_argument("--eps", type=float, default=8 / 255)
    ap.add_argument("--pgd-steps", type=int, default=10)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--jpeg-quality", type=int, default=75)
    ap.add_argument("--out", default=str(REPORT_PATH))
    args = ap.parse_args(argv)
    report = run_detector_robustness_eval(
        model_name=args.model, n_images=args.images, eps=args.eps,
        pgd_steps=args.pgd_steps, device=args.device, jpeg_quality=args.jpeg_quality,
    )
    write_report(report, Path(args.out))
    print(json.dumps({k: report.get(k) for k in ("status", "clean", "fgsm", "pgd")}, indent=1, default=str))
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND_ROOT))
    sys.exit(main())
