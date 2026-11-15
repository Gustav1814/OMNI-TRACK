"""Re-ID same-person vs different-person separation on held-out CAVIAR store sequences.

Complements eval/reid.py (Market-1501 Rank-1): this is the in-store domain the
demo runs on. Uses the shipped extractor (app.ai.reid.PersonReID) on the GT
person crops of the CAVIAR sequences that backend/scripts/train_reid.py holds
out (HOLDOUT) - they are never trained on.

Protocol (same as train_reid.eval_caviar, so before/after fine-tuning numbers
are comparable): within each held-out sequence, positive pairs = same GT
identity (sequence/view/person) at least 75 frames (3 s) apart; negative pairs =
different identities. Reports ROC AUC, the threshold at 1% FAR, TAR there, and
the pair accept rates at the shipped REID_T_MATCH.

Caveat: CAVIAR person ids are per view (the two cameras are not linked in GT),
so a cross-view pair of the same physical person counts as a negative. This
makes the reported FAR slightly pessimistic.

    python eval/reid_caviar.py                                   # shipped REID_WEIGHTS_PATH
    python eval/reid_caviar.py --weights osnet_ain_x1_0_omnitrack.pth   # after PC fine-tuning
"""

from __future__ import annotations

import sys
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

MANIFEST = "caviar_reid_holdout"


def build_manifest(by_name: dict, holdout) -> dict:
    if not by_name:
        raise SystemExit("No CAVIAR hold-out crops: backend/storage/demo_videos/caviar/<seq>/ (videos + gt_*.xml) missing")
    return {
        "dataset": "CAVIAR shopping-centre GT person crops (h >= 48 px, every 5th frame)",
        "split": f"hold-out sequences never used for training: {', '.join(sorted(holdout))}",
        "source": "https://groups.inf.ed.ac.uk/vision/CAVIAR/CAVIARDATA1/",
        "license": "CAVIAR: free for research use with acknowledgement (EC IST-2001-37540)",
        "items": [{"crop": n, "identity": r[1], "frame": int(r[2])} for n, r in sorted(by_name.items())],
    }


def pair_stats(E: np.ndarray, seqs: np.ndarray, ident: np.ndarray, fr: np.ndarray, t_match: float) -> dict:
    pos, neg = [], []
    for s in np.unique(seqs):
        idx = np.where(seqs == s)[0]
        S = E[idx] @ E[idx].T
        iu = np.triu(np.ones_like(S, bool), 1)
        same = ident[idx][:, None] == ident[idx][None, :]
        far = np.abs(fr[idx][:, None] - fr[idx][None, :]) >= 75
        pos.append(S[iu & same & far])
        neg.append(S[iu & ~same])
    pos, neg = np.concatenate(pos), np.concatenate(neg)
    if not len(pos) or not len(neg):
        return {}
    ranks = np.concatenate([pos, neg]).argsort().argsort()[:len(pos)] + 1
    auc = (ranks.sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    th = float(np.quantile(neg, 0.99))
    return {
        "auc": round(float(auc), 4),
        "thr_at_1pct_far": round(th, 4),
        "tar_at_1pct_far": round(float((pos >= th).mean()), 4),
        "t_match": t_match,
        "tar_at_t_match": round(float((pos >= t_match).mean()), 4),
        "far_at_t_match": round(float((neg >= t_match).mean()), 4),
        "same_median": round(float(np.median(pos)), 4),
        "diff_median": round(float(np.median(neg)), 4),
        "pos_pairs": int(len(pos)),
        "neg_pairs": int(len(neg)),
        # 21-bin histograms for the report figure (cosine similarity in [-1, 1] in practice [0, 1])
        "hist_edges": np.linspace(0, 1, 21).round(2).tolist(),
        "hist_same": np.histogram(pos, np.linspace(0, 1, 21))[0].tolist(),
        "hist_diff": np.histogram(neg, np.linspace(0, 1, 21))[0].tolist(),
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--weights", action="append", default=None,
                   help="weight file in model_weight/ (repeatable; first is primary). Default: REID_WEIGHTS_PATH")
    p.add_argument("--batch", type=int, default=64)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)

    sys.path.insert(0, str(BACKEND / "scripts"))
    import cv2  # noqa: PLC0415
    from train_reid import HOLDOUT, caviar_crops  # noqa: PLC0415 - same crops and hold-out as training

    from app.config import settings  # noqa: PLC0415

    weights = args.weights or [settings.REID_WEIGHTS_PATH]
    crops = caviar_crops()  # builds storage/datasets/caviar_reid_crops from demo_videos/caviar once
    by_name = {r[0].name: r for s in sorted(HOLDOUT) for r in crops.get(s, [])}

    manifest = load_or_freeze_manifest(MANIFEST, lambda: build_manifest(by_name, HOLDOUT), refreeze=args.refreeze)
    missing = [it["crop"] for it in manifest["items"] if it["crop"] not in by_name]
    if missing:
        raise SystemExit(f"{len(missing)} manifest crops are missing (e.g. {missing[0]}); rebuild the crop cache "
                         "from the same CAVIAR copy or pass --refreeze")
    rows = [by_name[it["crop"]] for it in manifest["items"]]
    seqs = np.array([r[0].name.split("_cam")[0] for r in rows])
    ident = np.array([r[1] for r in rows])
    fr = np.array([r[2] for r in rows])

    variants = {}
    with ml_lock():
        torch_threads(args.threads)
        device = resolve_device(args.device)
        from app.ai.reid import PersonReID  # noqa: PLC0415

        for w in weights:
            reid = PersonReID(settings.REID_MODEL, weights=str(WEIGHTS_DIR / w), device=device)
            if not reid.is_loaded:
                log(f"skip {w}: {reid.error}")
                continue
            t0 = time.perf_counter()
            feats = [reid.extract_batch([cv2.imread(str(r[0])) for r in rows[s:s + 512]], batch_size=args.batch)
                     for s in range(0, len(rows), 512)]
            ms = 1000 * (time.perf_counter() - t0) / len(rows)
            m = pair_stats(np.concatenate(feats), seqs, ident, fr, settings.REID_T_MATCH)
            m["ms_per_crop"] = round(ms, 2)
            variants[w] = m
            log(f"{w}: auc={m.get('auc')} tar@1%far={m.get('tar_at_1pct_far')} "
                f"tar/far@{settings.REID_T_MATCH}={m.get('tar_at_t_match')}/{m.get('far_at_t_match')}")
    if not variants:
        raise SystemExit("No Re-ID weights loaded - nothing measured")
    primary = next(iter(variants))
    write_result(
        "reid_caviar",
        value=variants[primary].get("auc"),
        metrics={**variants[primary], "variants": variants},
        dataset={
            **manifest_ref(MANIFEST, manifest),
            "n_crops": len(rows),
            "n_identities": int(len(set(ident.tolist()))),
        },
        model=model_info(WEIGHTS_DIR / primary, arch=settings.REID_MODEL, input="256x128", distance="cosine"),
        device=device,
        started=started,
        notes=[
            "Pairs are within one held-out sequence; positives are >= 3 s apart (same protocol as train_reid.py).",
            "CAVIAR ids are per camera view, so a cross-view pair of the same person counts as a negative "
            "(FAR slightly pessimistic).",
        ],
    )


if __name__ == "__main__":
    main()
