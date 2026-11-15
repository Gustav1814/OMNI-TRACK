"""Person re-identification Rank-1 / mAP on Market-1501 (standard query/gallery protocol).

Proposal criterion: Re-ID Rank-1 > 70%.

Model: the shipped extractor (app.ai.reid.PersonReID: osnet_ain_x1_0 with
the MSMT17-trained weights, 256x128, L2-normalised 512-d, cosine distance).
The weights were never trained on Market-1501, so this is a cross-domain
(MSMT17 -> Market) number; torchreid's model zoo lists ~70% Rank-1 / ~43% mAP
for this exact checkpoint.

Protocol (Zheng et al. 2015 / torchreid): 3,368 queries vs 15,913 gallery
images (junk pid -1 removed, distractors 0000 kept); for each query the gallery
images of the same identity taken by the same camera are excluded. No
re-ranking, no flip test (the pipeline does not use them).

    python eval/fetch_datasets.py market1501
    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/reid.py
"""

from __future__ import annotations

import re
import time

import numpy as np

import fetch_datasets
from common import (
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

MANIFEST = "market1501_test"
_NAME = re.compile(r"^(-?\d+)_c(\d)")  # torchreid's rule; a few released files are named *.jpg.jpg


def build_manifest() -> dict:
    root = fetch_datasets.market1501()
    items = []
    for split, folder in (("query", "query"), ("gallery", "bounding_box_test")):
        for f in sorted((root / folder).glob("*.jpg")):
            m = _NAME.match(f.name)
            if not m or int(m.group(1)) == -1:  # junk images are excluded by the protocol
                continue
            items.append({"split": split, "file": f"{folder}/{f.name}", "pid": int(m.group(1)), "cam": int(m.group(2))})
    return {
        "dataset": "Market-1501 (v15.09.15) query / bounding_box_test",
        "source": f"https://huggingface.co/datasets/{fetch_datasets.MARKET['repo']} @ {fetch_datasets.MARKET['revision']}",
        "license": "Market-1501: research use only, no redistribution",
        "split": "standard test protocol: 3,368 query vs 15,913 gallery (junk removed)",
        "items": items,
    }


def evaluate(qf, q_pid, q_cam, gf, g_pid, g_cam, chunk: int = 256):
    """CMC rank-1/5/10 and mAP, Market-1501 protocol (same pid + same cam removed per query)."""
    cmc_hits = np.zeros(10)
    aps, valid = [], 0
    for s in range(0, len(qf), chunk):
        sim = qf[s:s + chunk] @ gf.T
        order = np.argsort(-sim, axis=1)
        for i in range(sim.shape[0]):
            qi = s + i
            o = order[i]
            keep = ~((g_pid[o] == q_pid[qi]) & (g_cam[o] == q_cam[qi]))
            match = (g_pid[o] == q_pid[qi])[keep]
            if not match.any():
                continue
            valid += 1
            first = int(np.argmax(match))
            if first < 10:
                cmc_hits[first:] += 1
            hits = np.cumsum(match)
            ranks = np.where(match)[0]
            aps.append(float(np.mean(hits[ranks] / (ranks + 1))))
    cmc = cmc_hits / max(valid, 1)
    return {
        "rank1": round(float(cmc[0]), 4),
        "rank5": round(float(cmc[4]), 4),
        "rank10": round(float(cmc[9]), 4),
        "mAP": round(float(np.mean(aps)) if aps else 0.0, 4),
        "n_valid_queries": valid,
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--batch", type=int, default=64)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    manifest = load_or_freeze_manifest(MANIFEST, build_manifest, refreeze=args.refreeze)
    root = fetch_datasets.market1501()
    items = manifest["items"]

    import cv2  # noqa: PLC0415

    weight = WEIGHTS_DIR / "osnet_ain_x1_0_msmt17.pth"
    with ml_lock():
        torch_threads(args.threads)
        device = resolve_device(args.device)
        from app.ai.reid import PersonReID  # noqa: PLC0415

        reid = PersonReID("osnet_ain_x1_0", weights=str(weight), device=device)
        if not reid.is_loaded:
            raise SystemExit(f"Re-ID model failed to load: {reid.error}")
        feats = []
        t0 = time.perf_counter()
        for s in range(0, len(items), 512):
            crops = [cv2.imread(str(root / it["file"])) for it in items[s:s + 512]]
            feats.append(reid.extract_batch(crops, batch_size=args.batch))
            if (s // 512) % 8 == 0:
                log(f"  embedded {s + len(crops)}/{len(items)}")
        embed_s = time.perf_counter() - t0
    f = np.concatenate(feats)
    split = np.array([it["split"] for it in items])
    pid = np.array([it["pid"] for it in items])
    cam = np.array([it["cam"] for it in items])
    q, g = split == "query", split == "gallery"
    m = evaluate(f[q], pid[q], cam[q], f[g], pid[g], cam[g])
    m.update({"n_query": int(q.sum()), "n_gallery": int(g.sum()), "ms_per_crop": round(1000 * embed_s / len(items), 2)})
    log(f"{m}")
    write_result(
        "reid",
        value=m["rank1"],
        metrics=m,
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(weight, arch="osnet_ain_x1_0", trained_on="MSMT17", input="256x128", distance="cosine"),
        device=device,
        started=started,
        notes=[
            "Cross-domain: weights trained on MSMT17 only, never on Market-1501.",
            "Single query, no re-ranking, no flip augmentation - same features the live pipeline stores.",
            "torchreid model zoo reference for this checkpoint: ~70.1% Rank-1 / ~43.3% mAP (MSMT17 -> Market).",
        ],
    )


if __name__ == "__main__":
    main()
