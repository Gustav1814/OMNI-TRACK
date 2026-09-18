r"""
OmniTrack AI — Adversarial Robustness Evaluation (Proposal: ART)
════════════════════════════════════════════════════════════════

Proposal criterion: "YOLO model evaluated against FGSM, PGD, and adversarial
patch attacks using the Adversarial Robustness Toolbox (ART)" — documented
resilience.

What this measures
------------------
FGSM and PGD are WHITE-BOX attacks: they need the gradient of the model's own
loss with respect to the input pixels. This module therefore wraps the real
Ultralytics detection network in an ART `PyTorchClassifier`, so ART's attacks
differentiate through the actual weights being defended.

That distinction matters. An earlier version of this file attacked a small,
randomly-initialised surrogate CNN and then measured YOLO on the result. Because
the surrogate never learned anything, its gradients were unrelated to YOLO's, so
the "adversarial" images were barely more than structured noise and any
robustness figure derived from them would have been meaningless. Attacking the
network under test is what makes the number real.

Method
------
  1. Wrap `yolo.model` so it emits two logits per image: best person-class score
     across anchors, and best non-person score. Verified to backpropagate to the
     input, which is all FGSM/PGD require.
  2. Generate adversarial batches with ART at a chosen L-inf budget (`eps`).
  3. Run the UNMODIFIED YOLO detector on clean and adversarial batches and count
     person detections. Retention = adversarial count / clean count.

Reading the result
------------------
`detection_retention_*` near 1.0 means the attack failed to suppress detections
at that budget; near 0.0 means the detector was blinded. `eps` is in [0,1] pixel
units, so 0.03 is roughly 8/255 — the standard L-inf budget in the literature.

The clean baseline must be non-zero for retention to mean anything. If the
sample images contain no people, the eval reports `baseline_usable: false` and
the retention figures should be ignored.

Adversarial patch: ART ships `AdversarialPatch` for object detection, which
needs a training loop over many images rather than a single forward/backward.
It is documented as future work rather than implemented here.

Install:
  pip install adversarial-robustness-toolbox

Standalone:
  cd backend
  ..\.venv\Scripts\python.exe -m app.security.adversarial_eval
"""

from typing import Any, Dict, List, Optional

from loguru import logger

_ART_AVAILABLE = False
_ART_EVAL_RESULT: Optional[Dict[str, Any]] = None

try:
    import numpy as np
    from art.attacks.evasion import FastGradientMethod, ProjectedGradientDescent
    from art.estimators.classification import PyTorchClassifier
    _ART_AVAILABLE = True
except ImportError:
    np = None  # type: ignore


def get_robustness_status() -> Dict[str, Any]:
    """
    Return documented resilience status. Safe to call when ART is absent.
    """
    return {
        "art_available": _ART_AVAILABLE,
        "evaluated_attacks": ["FGSM", "PGD"],
        "attack_surface": "white-box (gradients taken through the YOLO network itself)",
        "adversarial_patch": (
            "documented as future work; ART provides AdversarialPatch for object "
            "detection, which requires an optimisation loop over many images "
            "rather than the single-step gradient FGSM/PGD use"
        ),
        "last_eval": _ART_EVAL_RESULT,
        "proposal_criterion": "YOLO evaluated against FGSM, PGD, adversarial patch (ART)",
        "install": "pip install adversarial-robustness-toolbox",
    }


# ── sample loading ─────────────────────────────────────────────────────

def _frames_from_dir(image_dir: str, n: int) -> List["np.ndarray"]:
    import os
    import cv2
    if not os.path.isdir(image_dir):
        return []
    names = [
        f for f in sorted(os.listdir(image_dir))
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))
    ][:n]
    out = []
    for f in names:
        img = cv2.imread(os.path.join(image_dir, f))
        if img is not None:
            out.append(img)
    return out


def _frames_from_video(video_path: str, n: int) -> List["np.ndarray"]:
    """Evenly spaced frames, so we do not sample n near-identical neighbours."""
    import os
    import cv2
    if not os.path.isfile(video_path):
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    out = []
    if total > 0:
        idxs = np.linspace(0, max(total - 1, 0), min(n, total)).astype(int)
        for i in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, frame = cap.read()
            if ok and frame is not None:
                out.append(frame)
    else:
        while len(out) < n:
            ok, frame = cap.read()
            if not ok:
                break
            out.append(frame)
    cap.release()
    return out


def _to_batch(frames: List["np.ndarray"], size: int = 640) -> "np.ndarray":
    """BGR frames -> float32 NCHW RGB in [0,1], the range ART clips against."""
    import cv2
    prepared = []
    for img in frames:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (size, size))
        prepared.append((rgb.astype(np.float32) / 255.0).transpose(2, 0, 1))
    return np.stack(prepared, axis=0)


# ── evaluation ─────────────────────────────────────────────────────────

def run_detector_robustness_eval(
    model_path: str = "yolov8n.pt",
    sample_size: int = 8,
    eps_fgsm: float = 0.03,
    eps_pgd: float = 0.03,
    pgd_steps: int = 10,
    image_dir: Optional[str] = None,
    video_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run FGSM and PGD against the real detector and report detection retention.
    """
    global _ART_EVAL_RESULT
    if not _ART_AVAILABLE:
        logger.warning("ART not installed; skipping adversarial robustness eval")
        _ART_EVAL_RESULT = {"skipped": True, "reason": "ART not installed"}
        return _ART_EVAL_RESULT

    try:
        import torch
        from torch import nn
        from ultralytics import YOLO
    except ImportError as e:
        _ART_EVAL_RESULT = {"skipped": True, "reason": str(e)}
        return _ART_EVAL_RESULT

    try:
        yolo = YOLO(model_path)
        net = yolo.model
        net.eval()
        for p in net.parameters():       # attack the input, never the weights
            p.requires_grad_(False)

        class _YoloPersonLogits(nn.Module):
            """
            Ultralytics detect head -> two logits ART can attack.

            The head emits [N, 4 + num_classes, anchors]; channel 4 onward are
            class scores. We reduce to the strongest person score and the
            strongest non-person score across every anchor, which turns "is a
            person visible anywhere in this image" into a 2-class problem —
            exactly the decision FGSM/PGD should be pushing across.
            """

            def __init__(self, detect_net):
                super().__init__()
                self.net = detect_net

            def forward(self, x):
                out = self.net(x)
                preds = out[0] if isinstance(out, (tuple, list)) else out
                cls = preds[:, 4:, :]
                person = cls[:, 0, :].amax(dim=1)
                if cls.shape[1] > 1:
                    other = cls[:, 1:, :].amax(dim=2).amax(dim=1)
                else:
                    other = torch.zeros_like(person)
                return torch.stack([other, person], dim=1)

        wrapped = _YoloPersonLogits(net)
        wrapped.eval()

        classifier = PyTorchClassifier(
            model=wrapped,
            loss=nn.CrossEntropyLoss(),
            input_shape=(3, 640, 640),
            nb_classes=2,
            clip_values=(0.0, 1.0),
            device_type="cpu",
        )

        # ── samples ────────────────────────────────────────────────
        from pathlib import Path

        from app.config import settings

        frames: List["np.ndarray"] = []
        source = "none"
        if image_dir:
            frames = _frames_from_dir(image_dir, sample_size)
            source = f"images:{image_dir}"
        if not frames and video_path:
            frames = _frames_from_video(video_path, sample_size)
            source = f"video:{Path(video_path).name}"
        if not frames:
            fd = getattr(settings, "FOOTAGE_DIR", None)
            if fd:
                frames = _frames_from_dir(str(fd), sample_size)
                source = f"images:{fd}"
        if not frames:
            default_clip = Path(__file__).resolve().parents[2].parent / "emotion test.mp4"
            if default_clip.exists():
                frames = _frames_from_video(str(default_clip), sample_size)
                source = f"video:{default_clip.name}"

        used_real_images = bool(frames)
        if frames:
            x = _to_batch(frames)
        else:
            # Keeps the run from crashing, but a noise baseline detects nobody,
            # so retention would be 0/0 and is flagged unusable below.
            x = np.random.rand(sample_size, 3, 640, 640).astype(np.float32)
            source = "random noise"

        y = np.ones(x.shape[0], dtype=np.int64)   # ground-truth label: person present

        # ── attacks ────────────────────────────────────────────────
        fgsm = FastGradientMethod(estimator=classifier, eps=eps_fgsm)
        x_fgsm = fgsm.generate(x=x, y=y)

        pgd = ProjectedGradientDescent(
            estimator=classifier,
            eps=eps_pgd,
            eps_step=max(eps_pgd / max(pgd_steps, 1), 1e-4),
            max_iter=pgd_steps,
        )
        x_pgd = pgd.generate(x=x, y=y)

        # ── measure the real detector, not the wrapper ─────────────
        import cv2

        def _count_persons(batch: "np.ndarray") -> Dict[str, float]:
            counts, confs = [], []
            for i in range(batch.shape[0]):
                frame = (batch[i].transpose(1, 2, 0) * 255.0).clip(0, 255).astype(np.uint8)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                try:
                    results = yolo.predict(source=frame, verbose=False, conf=0.25)
                except Exception:
                    counts.append(0)
                    continue
                n = 0
                for r in results:
                    if r.boxes is None:
                        continue
                    for box in r.boxes:
                        try:
                            if int(box.cls[0]) == 0:
                                n += 1
                                confs.append(float(box.conf[0]))
                        except Exception:
                            continue
                counts.append(n)
            return {
                "avg_count": float(np.mean(counts)) if counts else 0.0,
                "total": int(sum(counts)),
                "avg_conf": float(np.mean(confs)) if confs else 0.0,
            }

        clean = _count_persons(x)
        fgsm_r = _count_persons(x_fgsm)
        pgd_r = _count_persons(x_pgd)

        baseline_usable = clean["total"] > 0

        def _retention(a: Dict[str, float]) -> Optional[float]:
            if not baseline_usable:
                return None
            return round(a["total"] / clean["total"], 4)

        _ART_EVAL_RESULT = {
            "skipped": False,
            "art_version": __import__("art").__version__,
            "attacks": ["FGSM", "PGD"],
            "attack_surface": "white-box",
            "model": model_path,
            "sample_size": int(x.shape[0]),
            "sample_source": source,
            "used_real_images": used_real_images,
            "baseline_usable": baseline_usable,
            "eps_fgsm": eps_fgsm,
            "eps_pgd": eps_pgd,
            "pgd_steps": pgd_steps,
            "linf_actual_fgsm": round(float(np.abs(x_fgsm - x).max()), 5),
            "linf_actual_pgd": round(float(np.abs(x_pgd - x).max()), 5),
            "persons_clean": clean,
            "persons_fgsm": fgsm_r,
            "persons_pgd": pgd_r,
            "detection_retention_fgsm": _retention(fgsm_r),
            "detection_retention_pgd": _retention(pgd_r),
            "note": (
                "retention = adversarial person detections / clean person "
                "detections; lower means the attack suppressed more of the "
                "detector. Ignore retention when baseline_usable is false."
            ),
        }
        logger.info(
            "Adversarial eval: clean={} fgsm={} pgd={}".format(
                clean["total"], fgsm_r["total"], pgd_r["total"]
            )
        )
        return _ART_EVAL_RESULT

    except Exception as e:
        logger.exception("Adversarial robustness eval failed")
        _ART_EVAL_RESULT = {"skipped": True, "error": str(e)}
        return _ART_EVAL_RESULT


if __name__ == "__main__":
    import json

    status = get_robustness_status()
    print("ART available:", status["art_available"])
    if status["art_available"]:
        print(json.dumps(run_detector_robustness_eval(), indent=2, default=str))
