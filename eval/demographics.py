"""Age-band and gender accuracy on the FairFace validation split (10,954 faces) plus CPU latency per face.

No proposal target: this documents what the demographics feature can and cannot claim.

Model: the shipped fairface_res34.onnx (FairFace ResNet-34, gender + 9 age bands; the race head
is dropped at export), run through the app's own preprocessing
(app.ai.demographics.DemographicsClassifier.preprocess: resize 224, BGR->RGB, ImageNet mean/std).
FairFace validation images are already face crops with 0.25 padding, so no face detector is involved.

Reported:
  * gender accuracy (primary), age-band exact accuracy and within +/-1 band;
  * per-race-group and per-gender breakdowns (the dataset's own labels) to expose accuracy gaps;
  * the same metrics on a CCTV-like subset (every 4th face downscaled to --lowres px and back up);
  * CPU latency per face at batch 1 and amortised batch throughput.

    python eval/fetch_datasets.py fairface   # or the pinned parquet already in storage/datasets/fairface
    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/demographics.py
"""

from __future__ import annotations

import time

import numpy as np

from common import (
    DATASETS_DIR,
    WEIGHTS_DIR,
    base_parser,
    cap_threads,
    load_or_freeze_manifest,
    log,
    manifest_ref,
    ml_lock,
    model_info,
    sha256_file,
    write_result,
)

MANIFEST = "fairface_val"
PARQUET = DATASETS_DIR / "fairface" / "validation-0.25.parquet"
PARQUET_SHA256 = "d0cfac2888356b3d3fa3d837bc30961d8c860934e4c5df862e8cb87812bfa137"
HF_REPO = "HuggingFaceM4/FairFace"
HF_REVISION = "54d573cdb8b5af490ba8da9da2799628f6e5c496"
RACES = ["East Asian", "Indian", "Black", "White", "Middle Eastern", "Latino_Hispanic", "Southeast Asian"]
GENDERS = ["male", "female"]
LOWRES_EVERY = 4


def read_rows(columns):
    import polars as pl  # noqa: PLC0415

    return pl.read_parquet(PARQUET, columns=columns)


def build_manifest() -> dict:
    from app.ai.demographics import AGE_BANDS  # noqa: PLC0415

    df = read_rows(["image", "age", "gender", "race"])
    items = [
        {"row": i, "path": img["path"], "age": int(a), "gender": int(g), "race": int(r)}
        for i, (img, a, g, r) in enumerate(zip(df["image"], df["age"], df["gender"], df["race"]))
    ]
    return {
        "dataset": "FairFace validation split (0.25 padding)",
        "source": f"https://huggingface.co/datasets/{HF_REPO} @ {HF_REVISION} (data/0.25/validation)",
        "license": "CC BY 4.0 (Karkkainen & Joo, FairFace, WACV 2021)",
        "split": f"validation, all {len(items)} faces; parquet sha256 {PARQUET_SHA256}",
        "age_bands": list(AGE_BANDS),
        "races": RACES,
        "items": items,
    }


def _scores(g_pred, g_true, a_pred, a_true) -> dict:
    n = len(g_true)
    if n == 0:
        return {"n": 0}
    return {
        "n": int(n),
        "gender_accuracy": round(float((g_pred == g_true).mean()), 4),
        "age_exact": round(float((a_pred == a_true).mean()), 4),
        "age_within_1": round(float((np.abs(a_pred - a_true) <= 1).mean()), 4),
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lowres", type=int, default=32, help="face width in px for the CCTV-like subset")
    p.add_argument("--latency-faces", type=int, default=200)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    if not PARQUET.is_file():
        raise SystemExit(f"Missing {PARQUET}; fetch the FairFace validation parquet first")
    if sha256_file(PARQUET) != PARQUET_SHA256:
        raise SystemExit(f"{PARQUET} sha256 mismatch; refusing to evaluate an unknown file")
    manifest = load_or_freeze_manifest(MANIFEST, build_manifest, refreeze=args.refreeze)
    items = manifest["items"]

    import cv2  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415

    from app.ai.demographics import AGE_BANDS, DemographicsClassifier  # noqa: PLC0415 - shipped preprocessing

    weight = WEIGHTS_DIR / "fairface_res34.onnx"
    if not weight.is_file():
        raise SystemExit(f"Missing {weight}; run backend/scripts/download_default_weights.py fairface_res34.onnx")
    images = read_rows(["image"])["image"]
    prep = DemographicsClassifier.preprocess

    g_pred, a_pred, lr_g, lr_a, lr_idx = [], [], [], [], []
    with ml_lock():
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = args.threads
        opts.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        if args.device != "cpu" and "CUDAExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "CUDAExecutionProvider")
        sess = ort.InferenceSession(str(weight), opts, providers=providers)
        device = "cuda" if providers[0].startswith("CUDA") else "cpu"
        name = sess.get_inputs()[0].name

        def run(crops):
            g, a = sess.run(["gender", "age"], {name: prep(crops)})
            return g.argmax(1), a.argmax(1)

        crops, lowres = [], []
        t_infer = 0.0

        def flush(final=False):
            nonlocal t_infer
            if crops and (final or len(crops) >= args.batch):
                t = time.perf_counter()
                g, a = run(crops)
                t_infer += time.perf_counter() - t
                g_pred.extend(g.tolist())
                a_pred.extend(a.tolist())
                crops.clear()
            if lowres and (final or len(lowres) >= args.batch):
                g, a = run(lowres)
                lr_g.extend(g.tolist())
                lr_a.extend(a.tolist())
                lowres.clear()

        for k, it in enumerate(items):
            img = cv2.imdecode(np.frombuffer(images[it["row"]]["bytes"], np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise SystemExit(f"undecodable image row {it['row']} ({it['path']})")
            crops.append(img)
            if k % LOWRES_EVERY == 0:
                h, w = img.shape[:2]
                small = cv2.resize(img, (args.lowres, max(1, round(h * args.lowres / w))), interpolation=cv2.INTER_AREA)
                lowres.append(small)  # preprocess upsamples it to 224 like a small CCTV face crop
                lr_idx.append(k)
            flush()
            if k and k % 2000 == 0:
                log(f"{k}/{len(items)} faces")
        flush(final=True)

        # latency: one face per call (live pipeline case) and amortised over a full batch
        sample = [cv2.imdecode(np.frombuffer(images[it["row"]]["bytes"], np.uint8), cv2.IMREAD_COLOR)
                  for it in items[: args.latency_faces]]
        run(sample[:1])  # warm-up
        t = time.perf_counter()
        for c in sample:
            run([c])
        ms_batch1 = 1000 * (time.perf_counter() - t) / len(sample)
        t = time.perf_counter()
        for i in range(0, len(sample), args.batch):
            run(sample[i: i + args.batch])
        ms_batched = 1000 * (time.perf_counter() - t) / len(sample)

    n = len(items)
    if n == 0 or len(g_pred) != n:
        raise SystemExit(f"evaluated {len(g_pred)} of {n} faces")
    g_true = np.array([it["gender"] for it in items])
    a_true = np.array([it["age"] for it in items])
    race = np.array([it["race"] for it in items])
    gp, ap = np.array(g_pred), np.array(a_pred)
    overall = _scores(gp, g_true, ap, a_true)

    per_race = {r: _scores(gp[race == i], g_true[race == i], ap[race == i], a_true[race == i])
                for i, r in enumerate(RACES)}
    per_gender = {g: _scores(gp[g_true == i], g_true[g_true == i], ap[g_true == i], a_true[g_true == i])
                  for i, g in enumerate(GENDERS)}
    gaps = {
        key: round(max(v[key] for v in per_race.values()) - min(v[key] for v in per_race.values()), 4)
        for key in ("gender_accuracy", "age_exact", "age_within_1")
    }
    conf = np.zeros((len(AGE_BANDS), len(AGE_BANDS)), dtype=int)
    for t_, p_ in zip(a_true, ap):
        conf[t_, p_] += 1
    li = np.array(lr_idx)
    lowres_scores = _scores(np.array(lr_g), g_true[li], np.array(lr_a), a_true[li])
    native_same = _scores(gp[li], g_true[li], ap[li], a_true[li])

    metrics = {
        **{k: v for k, v in overall.items() if k != "n"},
        "n_faces": n,
        "per_race_group": per_race,
        "per_true_gender": per_gender,
        "max_gap_between_race_groups": gaps,
        "age_band_recall": {b: round(float(conf[i, i] / max(conf[i].sum(), 1)), 4) for i, b in enumerate(AGE_BANDS)},
        "age_confusion_matrix": {"rows_true_cols_pred": list(AGE_BANDS), "counts": conf.tolist()},
        "cctv_like_lowres": {
            "face_width_px": args.lowres,
            "subset": f"every {LOWRES_EVERY}th face",
            "lowres": lowres_scores,
            "native_same_subset": native_same,
        },
        "cpu_latency" if device == "cpu" else "latency": {
            "ms_per_face_batch1": round(ms_batch1, 2),
            "ms_per_face_batched": round(ms_batched, 2),
            "batch": args.batch,
            "faces_timed": len(sample),
            "threads": args.threads,
        },
        "ms_per_face_eval_pass": round(1000 * t_infer / n, 2),
    }
    log(f"gender={overall['gender_accuracy']} age_exact={overall['age_exact']} "
        f"age+/-1={overall['age_within_1']} lowres={lowres_scores} ms/face(b1)={ms_batch1:.1f}")
    write_result(
        "demographics",
        value=overall["gender_accuracy"],
        metrics=metrics,
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(weight, input=224, preprocessing="app.ai.demographics.DemographicsClassifier.preprocess",
                         source="FairFace res34_fair_align_multi_7_20190809.pt (github.com/joojs/fairface, CC BY 4.0)",
                         outputs={"gender": GENDERS, "age": list(AGE_BANDS)}),
        device=device,
        started=started,
        out_dir=args.out_dir,
        notes=[
            "FairFace authors trained on the FairFace train split; this validation split is their held-out set, "
            "so these numbers are in-distribution and higher than on real CCTV faces.",
            "Race labels are used only to measure accuracy gaps between groups; the product never predicts race.",
            f"cctv_like_lowres: faces downscaled to {args.lowres}px wide then upsampled by the shipped preprocessing.",
            "The product only labels a visit after DEMOGRAPHICS_MIN_FACE_SAMPLES readings and reports 'unknown' "
            "gender below 0.6 averaged probability; per-face accuracy here is the raw classifier.",
        ],
    )


if __name__ == "__main__":
    main()
