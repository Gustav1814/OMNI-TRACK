"""Core CV benchmarks on CAVIAR ground truth (run on the demo PC; device comes from settings.DEVICE).

    python scripts/eval_core.py detector --model yolo11m.pt --imgsz 960 --export none
    python scripts/eval_core.py detector --model yolo11m.pt --imgsz 960 --export engine
    python scripts/eval_core.py reid                      # REID_MODEL / REID_WEIGHTS_PATH from settings
    python scripts/eval_core.py reid --arch osnet_x1_0 --weights osnet_x1_0_msmt17.pth

detector: AP50 (all-point PR, IoU 0.5, conf 0.05) and median latency per frame on every 10th annotated
          frame of every 3rd CAVIAR view. Compare backends at equal AP before switching DETECTOR_EXPORT.
reid:     GT person crops (h >= 64 px, every 5th frame) -> embeddings -> 5-crop tracklets (what the
          coordinator matches). Prints pairwise TAR/FAR and the open-set decision table
          (returning person: correct / wrong id / split; new person: false merge) per threshold,
          which is what REID_T_MATCH / REID_T_UPDATE are chosen from.

Reference (laptop CPU, 2026-11-15): yolo11s@640 AP50 .665 at 192 ms (pytorch) / 91 ms (onnx);
osnet_ain_x1_0_msmt17 on 8 clips / 52 people: T=0.75 -> 88% correct, 5% split, 18% new-person merge.
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.config import settings  # noqa: E402

CAVIAR = BACKEND / "storage" / "demo_videos" / "caviar"


def _views():
    for xml in sorted(glob.glob(str(CAVIAR / "*" / "gt_*.xml"))):
        view = xml.split("gt_")[1][:-4]
        vid = glob.glob(f"{xml.rsplit('/', 1)[0]}/*_{view}.mpg")
        if vid:
            yield xml, vid[0]


def _iou(a, b):
    x1 = np.maximum(a[:, None, 0], b[None, :, 0]); y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2]); y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = lambda z: (z[:, 2] - z[:, 0]) * (z[:, 3] - z[:, 1])  # noqa: E731
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-9)


def detector(args) -> None:
    from app.ai.detector import PersonDetector

    m = PersonDetector(str(settings.weight_path(args.model)), conf=0.05, iou=settings.PERSON_IOU, imgsz=args.imgsz,
                       device=settings.DEVICE, export=args.export)
    print("backend:", m.info(), flush=True)
    scores, tps, n_gt, ts = [], [], 0, []
    for xml, vid in list(_views())[::3]:
        gt = {}
        for fr in ET.parse(xml).getroot().iter("frame"):
            bs = [[float(b.get("xc")) - float(b.get("w")) / 2, float(b.get("yc")) - float(b.get("h")) / 2,
                   float(b.get("xc")) + float(b.get("w")) / 2, float(b.get("yc")) + float(b.get("h")) / 2]
                  for b in fr.iter("box")]
            gt[int(fr.get("number"))] = np.array(bs).reshape(-1, 4)
        cap, i = cv2.VideoCapture(vid), -1
        while True:
            ok, f = cap.read(); i += 1
            if not ok:
                break
            if i % 10 or i not in gt:
                continue
            t = time.perf_counter()
            r = m.predict_batch([f])[0]
            ts.append(time.perf_counter() - t)
            o = np.argsort(-r.conf); d, s = r.xyxy[o], r.conf[o]
            g = gt[i]; n_gt += len(g)
            used = np.zeros(len(g), bool)
            ov = _iou(d, g) if len(d) and len(g) else np.zeros((len(d), len(g)))
            for k in range(len(d)):
                j = int(ov[k].argmax()) if len(g) else -1
                hit = j >= 0 and ov[k, j] >= 0.5 and not used[j]
                if hit:
                    used[j] = True
                scores.append(s[k]); tps.append(hit)
        cap.release()
    scores, tps = np.array(scores), np.array(tps, bool)
    o = np.argsort(-scores); tps = tps[o]
    ctp = np.cumsum(tps); rec = ctp / max(1, n_gt); prec = ctp / np.arange(1, len(tps) + 1)
    ap = float(np.sum(np.diff(np.concatenate([[0], rec])) * np.maximum.accumulate(prec[::-1])[::-1]))
    print(f"{args.model} {args.export} @{args.imgsz}: AP50={ap:.3f} latency_p50={np.median(ts[3:]) * 1000:.1f} ms "
          f"frames={len(ts)} gt={n_gt}", flush=True)


def _l2n(x):
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def reid(args) -> None:
    from app.ai.reid import PersonReID

    crops, clip, pid, fr = [], [], [], []
    for xml, vid in _views():
        gt = {}
        for f_ in ET.parse(xml).getroot().iter("frame"):
            n = int(f_.get("number"))
            if n % 5 == 0:
                gt[n] = [(int(o.get("id")), *(float(o.find("box").get(k)) for k in ("xc", "yc", "w", "h")))
                         for o in f_.iter("object")]
        cap, i = cv2.VideoCapture(vid), -1
        while True:
            ok, f = cap.read(); i += 1
            if not ok:
                break
            for p_, xc, yc, w, h in gt.get(i, []):
                if h < 64:
                    continue
                c = f[int(max(0, yc - h / 2)):int(yc + h / 2), int(max(0, xc - w / 2)):int(xc + w / 2)]
                if c.size:
                    crops.append(c.copy()); clip.append(xml); pid.append(p_); fr.append(i)
        cap.release()
    clip, pid, fr = np.array(clip), np.array(pid), np.array(fr)
    w = settings.weight_path(args.weights) if args.weights else settings.REID_WEIGHTS_PATH
    m = PersonReID(args.arch or settings.REID_MODEL, weights=str(w), device=settings.DEVICE)
    if not m.is_loaded:
        sys.exit(f"Re-ID model not loaded: {m.error}")
    t0 = time.time()
    E = np.concatenate([m.extract_batch(crops[i:i + 64], batch_size=64) for i in range(0, len(crops), 64)])
    print(f"{m.model_version}: {len(E)} crops, {len(set(zip(clip, pid)))} people, "
          f"{(time.time() - t0) / len(E) * 1000:.1f} ms/crop on {m.device}", flush=True)
    del crops

    # 5-crop tracklets (~1 s at every 5th frame), as matched by the coordinator
    T, tc, tp, tt = [], [], [], []
    for c in np.unique(clip):
        for p_ in np.unique(pid[clip == c]):
            idx = np.where((clip == c) & (pid == p_))[0]; idx = idx[np.argsort(fr[idx])]
            for k in range(0, len(idx) - 4, 5):
                run = idx[k:k + 5]
                if fr[run[-1]] - fr[run[0]] <= 50:
                    T.append(_l2n(E[run].mean(0))); tc.append(c); tp.append(p_); tt.append(fr[run[0]])
    T, tc, tp, tt = np.array(T), np.array(tc), np.array(tp), np.array(tt)

    pos, neg, ret, new = [], [], [], []
    for c in np.unique(tc):
        idx = np.where(tc == c)[0]
        S = T[idx] @ T[idx].T
        far = np.abs(tt[idx][:, None] - tt[idx][None, :]) >= 75  # >= 3 s apart
        same = tp[idx][:, None] == tp[idx][None, :]
        iu = np.triu(np.ones_like(S, bool), 1)
        pos.append(S[iu & far & same]); neg.append(S[iu & ~same])
        for qi in range(len(idx)):
            per = {}
            for p_ in np.unique(tp[idx]):
                mask = (tp[idx] == p_) & (far[qi] if p_ == tp[idx][qi] else True)
                if mask.any():
                    per[p_] = np.sort(S[qi, mask])[::-1][:5].mean()  # top-5 mean over the identity's views
            other = max([s for p_, s in per.items() if p_ != tp[idx][qi]], default=-1.0)
            if tp[idx][qi] in per:
                ret.append((per[tp[idx][qi]], other))
            new.append(other)
    pos, neg, ret, new = np.concatenate(pos), np.concatenate(neg), np.array(ret), np.array(new)
    print(f"tracklets={len(T)} pairwise: th@1%FAR={np.quantile(neg, 0.99):.3f} th@0.1%FAR={np.quantile(neg, 0.999):.3f}")
    print(" T     pairTAR pairFAR | returning: correct wrong-id split | new person: false-merge")
    for th in np.arange(0.55, 0.91, 0.05):
        ok = ((ret[:, 0] >= ret[:, 1]) & (ret[:, 0] >= th)).mean()
        bad = ((ret[:, 1] > ret[:, 0]) & (ret[:, 1] >= th)).mean()
        print(f" {th:.2f}  {(pos >= th).mean():.3f}   {(neg >= th).mean():.4f} | {ok:.3f}   {bad:.3f}   {1 - ok - bad:.3f} "
              f"| {(new >= th).mean():.3f}")
    print("Pick REID_T_MATCH where splits stay small and new-person merges drop; REID_T_UPDATE where wrong-id ~<4%.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detector")
    d.add_argument("--model", default="yolo11m.pt")
    d.add_argument("--imgsz", type=int, default=960)
    d.add_argument("--export", default="none", help="none | onnx | openvino | engine")
    r = sub.add_parser("reid")
    r.add_argument("--arch", default=None)
    r.add_argument("--weights", default=None)
    a = ap.parse_args()
    {"detector": detector, "reid": reid}[a.cmd](a)
