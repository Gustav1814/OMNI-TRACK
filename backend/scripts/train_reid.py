"""
Fine-tune the Re-ID model (OSNet-AIN, MSMT17 init) for OmniTrack's store cameras.

Training data
  * Market-1501 bounding_box_train (751 identities, 6 cameras), and
  * CAVIAR shopping-centre GT crops from the TRAIN sequences only. An identity is
    (sequence, view, person_id), because CAVIAR ids are not shared across the two views.
CAVIAR sequences used in the cross-camera demo are held out (HOLDOUT) and used only
for evaluation, so demo behaviour is never trained on.

Loss: label-smoothed cross-entropy + batch-hard triplet (P identities x K images per batch).
Evaluation: Market-1501 query/gallery Rank-1/mAP (standard protocol) and, on held-out
CAVIAR, same-person vs different-person similarity (AUC, TAR at 1% FAR, threshold).

Device is chosen at run time (CUDA if present). Examples:
  .venv/bin/python scripts/train_reid.py --epochs 30                 # GPU (RTX 3060 Ti: ~1 min/epoch)
  .venv/bin/python scripts/train_reid.py --epochs 6 --p 8 --k 4      # CPU, memory-light
  .venv/bin/python scripts/train_reid.py --eval-only model_weight/osnet_ain_x1_0_omnitrack.pth

Output: model_weight/osnet_ain_x1_0_omnitrack.pth (backbone only, loads with
torchreid.utils.load_pretrained_weights) + eval/results/reid_finetune.json.
Point REID_WEIGHTS_PATH at it to use it in the live pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
MARKET = BACKEND / "storage/datasets/market1501/Market-1501-v15.09.15"
CAVIAR = BACKEND / "storage/demo_videos/caviar"
CACHE = BACKEND / "storage/datasets/caviar_reid_crops"
HOLDOUT = {
    "OneLeaveShopReenter1", "OneLeaveShopReenter2", "TwoEnterShop1", "TwoEnterShop2",
    "TwoEnterShop3", "EnterExitCrossingPaths1", "EnterExitCrossingPaths2",
}
H, W = 256, 128
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
_MARKET_RE = re.compile(r"^(-?\d+)_c(\d)")
_NOISE = np.random.default_rng(0)


# ----------------------------------------------------------------------------- data
def market_split(name: str) -> list[tuple[Path, int, int]]:
    out = []
    for p in sorted((MARKET / name).glob("*.jpg")):
        m = _MARKET_RE.match(p.name)
        if m and int(m.group(1)) > 0:  # -1 = junk, 0 = distractor
            out.append((p, int(m.group(1)), int(m.group(2))))
    return out


def caviar_crops(min_h: int = 48, step: int = 5) -> dict[str, list[tuple[Path, str, int]]]:
    """GT person crops per sequence, cached as small jpgs: {seq: [(path, identity, frame)]}."""
    index = CACHE / "index.json"
    if index.is_file():
        raw = json.loads(index.read_text())
        return {s: [(CACHE / f, i, n) for f, i, n in rows] for s, rows in raw.items()}
    CACHE.mkdir(parents=True, exist_ok=True)
    raw: dict[str, list] = defaultdict(list)
    for xml in sorted(CAVIAR.glob("*/gt_*.xml")):
        seq, view = xml.parent.name, xml.stem[3:]
        vids = list(xml.parent.glob(f"*_{view}.mpg"))
        if not vids:
            continue
        gt = {}
        for fr in ET.parse(xml).getroot().iter("frame"):
            n = int(fr.get("number"))
            if n % step == 0:
                gt[n] = [(int(o.get("id")), *(float(o.find("box").get(k)) for k in ("xc", "yc", "w", "h")))
                         for o in fr.iter("object")]
        cap, i = cv2.VideoCapture(str(vids[0])), -1
        while True:
            ok, frame = cap.read()
            i += 1
            if not ok:
                break
            for pid, xc, yc, w, h in gt.get(i, []):
                if h < min_h:
                    continue
                x1, y1 = int(max(0, xc - w / 2)), int(max(0, yc - h / 2))
                crop = frame[y1:int(yc + h / 2), x1:int(xc + w / 2)]
                if crop.size:
                    f = f"{seq}_{view}_{pid}_{i:05d}.jpg"
                    cv2.imwrite(str(CACHE / f), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    raw[seq].append((f, f"{seq}/{view}/{pid}", i))
        cap.release()
    index.write_text(json.dumps(raw))
    return {s: [(CACHE / f, i, n) for f, i, n in rows] for s, rows in raw.items()}


def load(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def to_tensor(rgb: np.ndarray) -> np.ndarray:
    x = cv2.resize(rgb, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    return ((x - MEAN) / STD).transpose(2, 0, 1)


def augment(rgb: np.ndarray, rng: random.Random) -> np.ndarray:
    x = cv2.resize(rgb, (W, H), interpolation=cv2.INTER_LINEAR)
    if rng.random() < 0.5:
        x = x[:, ::-1]
    x = cv2.copyMakeBorder(np.ascontiguousarray(x), 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
    oy, ox = rng.randint(0, 20), rng.randint(0, 20)
    x = x[oy:oy + H, ox:ox + W].astype(np.float32) / 255.0
    if rng.random() < 0.8:  # photometric jitter: CCTV exposure/white-balance differs per camera
        x = x * rng.uniform(0.7, 1.3) + rng.uniform(-0.1, 0.1)
        x = x * np.array([rng.uniform(0.9, 1.1) for _ in range(3)], np.float32)
    if rng.random() < 0.3:  # low-res CCTV: downscale then upscale
        s = rng.uniform(0.35, 0.7)
        x = cv2.resize(cv2.resize(x, (max(8, int(W * s)), max(16, int(H * s)))), (W, H))
    x = ((np.clip(x, 0, 1) - MEAN) / STD).transpose(2, 0, 1).copy()
    if rng.random() < 0.5:  # random erasing (occlusion by shelves/other shoppers)
        for _ in range(10):
            area = rng.uniform(0.02, 0.33) * H * W
            ar = math.exp(rng.uniform(math.log(0.3), math.log(3.3)))
            eh, ew = round(math.sqrt(area * ar)), round(math.sqrt(area / ar))
            if eh < H and ew < W:
                y0, x0 = rng.randint(0, H - eh), rng.randint(0, W - ew)
                x[:, y0:y0 + eh, x0:x0 + ew] = _NOISE.normal(0, 1, (3, eh, ew)).astype(np.float32)
                break
    return x


class PKSampler:
    """Each batch: P identities x K images (identities with < K images are sampled with replacement)."""

    def __init__(self, labels: list[int], p: int, k: int, seed: int):
        self.by_id: dict[int, list[int]] = defaultdict(list)
        for i, lab in enumerate(labels):
            self.by_id[lab].append(i)
        self.p, self.k, self.rng = p, k, random.Random(seed)

    def __len__(self) -> int:
        return max(1, len(self.by_id) // self.p)

    def __iter__(self):
        ids = list(self.by_id)
        self.rng.shuffle(ids)
        for b in range(len(self)):
            batch = []
            for pid in ids[b * self.p:(b + 1) * self.p]:
                pool = self.by_id[pid]
                batch += self.rng.sample(pool, self.k) if len(pool) >= self.k else self.rng.choices(pool, k=self.k)
            yield batch


def batch_hard_triplet(feat: torch.Tensor, labels: torch.Tensor, margin: float) -> torch.Tensor:
    d = torch.cdist(feat, feat)
    same = labels[:, None] == labels[None, :]
    hardest_pos = (d * same).max(1).values
    hardest_neg = (d + same * 1e6).min(1).values
    return F.relu(hardest_pos - hardest_neg + margin).mean()


# ----------------------------------------------------------------------------- eval
@torch.no_grad()
def embed(model, paths: list[Path], device, bs: int = 128) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(paths), bs):
        rgb = [load(p) for p in paths[i:i + bs]]
        x = torch.from_numpy(np.stack([to_tensor(r) for r in rgb])).to(device)
        xf = torch.flip(x, dims=[3])
        f = model(x) + model(xf)  # flip-averaged, same as the live pipeline's multi-view mean
        out.append(F.normalize(f, dim=1).cpu().numpy())
    return np.concatenate(out)


def eval_market(model, device) -> dict:
    q, g = market_split("query"), market_split("bounding_box_test")
    qf, gf = embed(model, [p for p, _, _ in q], device), embed(model, [p for p, _, _ in g], device)
    qp, qc = np.array([x[1] for x in q]), np.array([x[2] for x in q])
    gp, gc = np.array([x[1] for x in g]), np.array([x[2] for x in g])
    r1, aps = [], []
    for i in range(len(q)):
        s = gf @ qf[i]
        keep = ~((gp == qp[i]) & (gc == qc[i]))
        order = np.argsort(-s[keep])
        match = (gp[keep] == qp[i])[order]
        if not match.any():
            continue
        r1.append(bool(match[0]))
        hits = np.where(match)[0]
        aps.append(np.mean((np.arange(len(hits)) + 1) / (hits + 1)))
    return {"rank1": float(np.mean(r1)), "mAP": float(np.mean(aps)), "queries": len(r1)}


def eval_caviar(model, device, crops: dict) -> dict:
    rows = [r for s in HOLDOUT for r in crops.get(s, [])]
    if not rows:
        return {}
    E = embed(model, [r[0] for r in rows], device)
    seqs = np.array([str(r[0].name).split("_cam")[0] for r in rows])
    ident = np.array([r[1] for r in rows])
    fr = np.array([r[2] for r in rows])
    pos, neg = [], []
    for s in np.unique(seqs):
        idx = np.where(seqs == s)[0]
        S = E[idx] @ E[idx].T
        iu = np.triu(np.ones_like(S, bool), 1)
        same = ident[idx][:, None] == ident[idx][None, :]
        far = np.abs(fr[idx][:, None] - fr[idx][None, :]) >= 75  # >= 3 s apart
        pos.append(S[iu & same & far])
        neg.append(S[iu & ~same])
    pos, neg = np.concatenate(pos), np.concatenate(neg)
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort()[:len(pos)] + 1
    auc = (ranks.sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    th = float(np.quantile(neg, 0.99))
    return {"auc": float(auc), "thr_at_1pct_far": th, "tar_at_1pct_far": float((pos >= th).mean()),
            "same_median": float(np.median(pos)), "diff_median": float(np.median(neg)),
            "pos_pairs": len(pos), "neg_pairs": len(neg), "sequences": sorted(HOLDOUT)}


# ----------------------------------------------------------------------------- main
def build(arch: str, num_classes: int, init: Path | None):
    import torchreid

    model = torchreid.models.build_model(name=arch, num_classes=num_classes, loss="triplet", pretrained=False)
    if init:
        torchreid.utils.load_pretrained_weights(model, str(init))
    return model


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arch", default="osnet_ain_x1_0")
    ap.add_argument("--init", default="model_weight/osnet_ain_x1_0_msmt17.pth")
    ap.add_argument("--out", default="model_weight/osnet_ain_x1_0_omnitrack.pth")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--p", type=int, default=16, help="identities per batch")
    ap.add_argument("--k", type=int, default=4, help="images per identity")
    ap.add_argument("--lr", type=float, default=3.5e-4)
    ap.add_argument("--margin", type=float, default=0.3)
    ap.add_argument("--no-caviar", action="store_true", help="train on Market-1501 only")
    ap.add_argument("--eval-only", metavar="WEIGHTS")
    ap.add_argument("--skip-market-eval", action="store_true")
    ap.add_argument("--before", choices=["full", "caviar", "none"], default="full",
                    help="baseline evaluation before training (Market eval is ~23k images)")
    ap.add_argument("--max-steps", type=int, default=0, help="stop after N steps (smoke test)")
    ap.add_argument("--threads", type=int, default=0, help="CPU threads (0 = torch default)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--freeze", default="",
                    help="comma-separated top-level modules to freeze, e.g. conv1,maxpool,conv2 (cuts CPU cost; "
                         "frozen norm layers stay in eval mode)")
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    if args.threads:
        torch.set_num_threads(args.threads)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device={device} threads={torch.get_num_threads()}", flush=True)
    crops = caviar_crops()

    def evaluate(model, tag: str, market: bool = True) -> dict:
        t = time.time()
        res = {"caviar_holdout": eval_caviar(model, device, crops)}
        if market and not args.skip_market_eval:
            res["market1501"] = eval_market(model, device)
        print(f"[{tag}] {json.dumps(res)}  ({time.time() - t:.0f}s)", flush=True)
        return res

    if args.eval_only:
        evaluate(build(args.arch, 1, BACKEND / args.eval_only).to(device), "eval")
        return

    # identities: Market pid -> "m<pid>", CAVIAR -> "seq/view/pid"; drop ids with < 2 images
    samples: list[tuple[Path, str]] = [(p, f"m{pid}") for p, pid, _ in market_split("bounding_box_train")]
    if not args.no_caviar:
        samples += [(p, i) for s, rows in crops.items() if s not in HOLDOUT for p, i, _ in rows]
    counts = defaultdict(int)
    for _, i in samples:
        counts[i] += 1
    samples = [s for s in samples if counts[s[1]] >= 2]
    names = sorted({i for _, i in samples})
    lab_of = {n: j for j, n in enumerate(names)}
    labels = [lab_of[i] for _, i in samples]
    n_cav = sum(1 for n in names if not n.startswith("m"))
    print(f"train: {len(samples)} images, {len(names)} identities ({n_cav} CAVIAR)", flush=True)
    imgs = [load(p) for p, _ in samples]  # native-size crops (Market is 128x64): a few hundred MB

    model = build(args.arch, len(names), BACKEND / args.init).to(device)
    base = {} if args.before == "none" else evaluate(model, "before", market=args.before == "full")

    frozen = [getattr(model, m) for m in filter(None, args.freeze.split(","))]
    for m in frozen:
        m.requires_grad_(False)
    head = [p for n, p in model.named_parameters() if n.startswith("classifier")]
    body = [p for n, p in model.named_parameters() if not n.startswith("classifier") and p.requires_grad]
    opt = torch.optim.AdamW([{"params": body, "lr": args.lr}, {"params": head, "lr": args.lr * 10}],
                            weight_decay=5e-4)
    sampler = PKSampler(labels, args.p, args.k, args.seed)
    steps = args.epochs * len(sampler)
    warm = min(len(sampler) * 2, steps // 5)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))
    rng = random.Random(args.seed)
    step = 0
    for ep in range(args.epochs):
        model.train()
        for m in frozen:
            m.eval()
        t, tot = time.time(), defaultdict(float)
        for batch in sampler:
            x = torch.from_numpy(np.stack([augment(imgs[i], rng) for i in batch])).to(device)
            y = torch.tensor([labels[i] for i in batch], device=device)
            logits, feat = model(x)
            ce = F.cross_entropy(logits, y, label_smoothing=0.1)
            tri = batch_hard_triplet(feat, y, args.margin)
            loss = ce + tri
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step(); sched.step(); step += 1
            tot["ce"] += ce.item(); tot["tri"] += tri.item(); tot["n"] += 1
            if step % 20 == 0:
                print(f"  ep{ep + 1} step {step}/{steps} ce={ce.item():.3f} tri={tri.item():.3f} "
                      f"{(time.time() - t) / tot['n']:.2f}s/it", flush=True)
            if args.max_steps and step >= args.max_steps:
                print(f"smoke: {step} steps OK, {(time.time() - t) / tot['n']:.2f}s/it", flush=True)
                return
        print(f"epoch {ep + 1}/{args.epochs}: ce={tot['ce'] / tot['n']:.3f} tri={tot['tri'] / tot['n']:.3f} "
              f"({time.time() - t:.0f}s)", flush=True)
        state = {k: v.cpu() for k, v in model.state_dict().items() if not k.startswith("classifier")}
        torch.save(state, BACKEND / (args.out + ".part"))

    final = evaluate(model, "after")
    (BACKEND / (args.out + ".part")).replace(BACKEND / args.out)
    report = {
        "arch": args.arch, "init": args.init, "weights": args.out,
        "train": {"images": len(samples), "identities": len(names), "caviar_identities": n_cav,
                  "epochs": args.epochs, "batch": f"{args.p}x{args.k}", "lr": args.lr, "device": str(device)},
        "before": base, "after": final,
        "notes": ["Market-1501 numbers are in-domain after fine-tuning (standard train/test protocol).",
                  "CAVIAR hold-out sequences were never trained on; they are the cross-camera demo clips."],
    }
    out = REPO / "eval/results/reid_finetune.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"saved {args.out} and {out}", flush=True)


if __name__ == "__main__":
    main()
