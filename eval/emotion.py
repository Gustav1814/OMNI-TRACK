"""Facial emotion accuracy on the FER-2013 public test split (7,178 faces).

Proposal criterion: emotion classification accuracy > 70%.

Model: the shipped HSEmotion enet_b0_8_best_afew.onnx (AffectNet, 8 classes),
run through the app's own preprocessing (EmotionRecognizer._preprocess: resize
to 224, BGR->RGB, ImageNet mean/std). FER-2013 faces are 48x48 grayscale and
already cropped, so the face detector is not involved.

Class mapping (8 model classes vs 7 FER classes): FER has no "contempt".
  * primary   - the contempt logit is dropped and the argmax is taken over the
                7 shared classes (the fairest comparison for a 7-class test set);
  * strict    - plain 8-way argmax; a "contempt" prediction counts as wrong.
Both numbers, per-class accuracy and the confusion matrix are recorded.

    python eval/fetch_datasets.py fer2013
    OMNITRACK_ML_LOCK=/tmp/omnitrack-ml.lock python eval/emotion.py
"""

from __future__ import annotations

import tarfile
import time

import numpy as np

import fetch_datasets as datasets
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
    write_result,
)

MANIFEST = "fer2013_test"
FER_CLASSES = datasets.FER["classes"]  # angry, disgusted, fearful, happy, neutral, sad, surprised
MODEL_LABELS = ["angry", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
# FER class index -> model output index
FER_TO_MODEL = [0, 2, 3, 4, 5, 6, 7]
CONTEMPT = 1


def read_shards():
    """Yield (shard, key, label, jpeg_bytes) from the webdataset tars in a stable order."""
    root = DATASETS_DIR / "fer2013"
    for shard in sorted(datasets.FER["shards"]):
        samples: dict[str, dict[str, bytes]] = {}
        with tarfile.open(root / shard) as tf:
            for m in tf:
                if not m.isfile():
                    continue
                key, _, ext = m.name.rpartition(".")
                samples.setdefault(key, {})[ext] = tf.extractfile(m).read()
        for key in sorted(samples):
            s = samples[key]
            if "cls" in s and ("jpg" in s or "png" in s):
                yield shard, key, int(s["cls"].decode().strip()), s.get("jpg") or s.get("png")


def build_manifest() -> dict:
    items = [{"shard": sh, "key": k, "label": y} for sh, k, y, _ in read_shards()]
    return {
        "dataset": "FER-2013 public test split",
        "source": f"https://huggingface.co/datasets/{datasets.FER['repo']} @ {datasets.FER['revision']}",
        "license": "FER-2013 (ICML 2013 representation-learning challenge), research use",
        "split": "test (PublicTest + PrivateTest, all 7,178 images)",
        "classes": FER_CLASSES,
        "items": items,
    }


def main() -> None:
    p = base_parser(__doc__.split("\n")[0])
    p.add_argument("--batch", type=int, default=64)
    args = p.parse_args()
    started = time.time()
    cap_threads(args.threads)
    datasets.fer2013()
    manifest = load_or_freeze_manifest(MANIFEST, build_manifest, refreeze=args.refreeze)
    wanted = {(it["shard"], it["key"]): it["label"] for it in manifest["items"]}

    import cv2  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415

    from app.ai.emotion import EmotionRecognizer  # noqa: PLC0415 - shipped preprocessing

    weight = WEIGHTS_DIR / "enet_b0_8_best_afew.onnx"
    logits_all, labels = [], []
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
        crops: list[np.ndarray] = []
        t_infer = 0.0

        def flush():
            nonlocal t_infer
            if crops:
                t = time.perf_counter()
                logits_all.append(sess.run(None, {name: EmotionRecognizer._preprocess(crops)})[0])
                t_infer += time.perf_counter() - t
                crops.clear()

        for shard, key, label, data in read_shards():
            if (shard, key) not in wanted:
                continue
            gray = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
            crops.append(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
            labels.append(label)
            if len(crops) >= args.batch:
                flush()
        flush()

    logits = np.concatenate(logits_all)
    y = np.asarray(labels)
    n = len(y)
    if n == 0:
        raise SystemExit("No FER-2013 images were evaluated")
    shared = logits[:, FER_TO_MODEL]
    pred7 = shared.argmax(1)
    pred8 = logits.argmax(1)
    model_to_fer = {m: f for f, m in enumerate(FER_TO_MODEL)}
    strict_ok = np.array([model_to_fer.get(int(p), -1) == t for p, t in zip(pred8, y)])
    acc = float((pred7 == y).mean())
    conf = np.zeros((7, 7), dtype=int)
    for t, pr in zip(y, pred7):
        conf[t, pr] += 1
    per_class = {c: round(float(conf[i, i] / max(conf[i].sum(), 1)), 4) for i, c in enumerate(FER_CLASSES)}
    metrics = {
        "accuracy_7class": round(acc, 4),
        "accuracy_strict_8way": round(float(strict_ok.mean()), 4),
        "contempt_predictions": int((pred8 == CONTEMPT).sum()),
        "macro_recall": round(float(np.mean(list(per_class.values()))), 4),
        "per_class_recall": per_class,
        "class_support": {c: int(conf[i].sum()) for i, c in enumerate(FER_CLASSES)},
        "confusion_matrix": {"rows_true_cols_pred": FER_CLASSES, "counts": conf.tolist()},
        "n_images": n,
        "ms_per_face": round(1000 * t_infer / n, 2),
    }
    log(f"accuracy={acc:.4f} strict={metrics['accuracy_strict_8way']} macro={metrics['macro_recall']}")
    write_result(
        "emotion",
        value=acc,
        metrics=metrics,
        dataset=manifest_ref(MANIFEST, manifest),
        model=model_info(weight, input=224, preprocessing="app.ai.emotion.EmotionRecognizer._preprocess",
                         labels=MODEL_LABELS),
        device=device,
        started=started,
        notes=[
            "Model is trained on AffectNet (8 classes); FER-2013 is a cross-dataset test (48x48 grayscale faces).",
            "Primary = argmax over the 7 classes FER shares with the model (contempt logit dropped); "
            "accuracy_strict_8way counts a 'contempt' prediction as wrong.",
            "Published FER-2013 state of the art is ~73-76% for models trained on FER-2013 itself.",
        ],
    )


if __name__ == "__main__":
    main()
