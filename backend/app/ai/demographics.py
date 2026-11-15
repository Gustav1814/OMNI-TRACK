"""
Age band + gender estimation on the emotion module's face crops.

Model: FairFace ResNet-34 (Karkkainen & Joo, WACV 2021; github.com/joojs/fairface, CC BY 4.0),
exported to ONNX by scripts/download_default_weights.py with only its gender (2) and age-band (9)
heads. The race head is dropped at export and never used by the product.

Readings are aggregated per track visit: a visit gets an age band / gender only after
DEMOGRAPHICS_MIN_FACE_SAMPLES face readings, and a gender only when the averaged probability is
clear. If the model file is missing the feature is disabled (`available` False) and the API
reports "model_not_installed"; nothing is ever estimated without the model.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from loguru import logger

from app.config import settings

AGE_BANDS: Tuple[str, ...] = ("0-2", "3-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70+")
GENDERS: Tuple[str, ...] = ("male", "female")  # FairFace output order
MODEL_NAME = "FairFace ResNet-34 (gender + age band)"
MODEL_LICENCE = "CC BY 4.0 (joojs/fairface)"
BIAS_CAVEAT = (
    "Age band and gender are statistical estimates from a face classifier (FairFace ResNet-34). "
    "Accuracy drops on small, blurred, side-on or low-light CCTV faces and differs between "
    "demographic groups; gender is a binary visual estimate, not identity. Use only as aggregate "
    "trends, never to make decisions about individuals."
)
GENDER_MIN_PROB = 0.6  # averaged probability needed to label a visit's gender; otherwise "unknown"

_INPUT = 224
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)


class DemographicsClassifier:
    """ONNX Runtime wrapper. `available` is False when the model is missing or fails to load."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = Path(model_path or settings.DEMOGRAPHICS_MODEL_PATH)
        self._session = None
        self._lock = threading.Lock()
        self.available = self._load()

    def _load(self) -> bool:
        if not self.model_path.is_file():
            logger.error(
                f"Demographics: model {self.model_path} not installed (run scripts/download_default_weights.py); "
                "demographics disabled."
            )
            return False
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = max(1, int(settings.EMOTION_THREADS))
            opts.inter_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            if settings.DEVICE in ("auto", "cuda") and "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")
            self._session = ort.InferenceSession(str(self.model_path), opts, providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            outs = [o.name for o in self._session.get_outputs()]
            if outs[:2] != ["gender", "age"]:
                raise RuntimeError(f"unexpected outputs {outs}; expected ['gender', 'age']")
        except Exception as e:
            logger.error(f"Demographics: failed to load {self.model_path.name} ({e}); demographics disabled.")
            self._session = None
            return False
        logger.info(f"Demographics ready: {self.model_path.name} providers={self._session.get_providers()}")
        return True

    @staticmethod
    def preprocess(crops: Sequence[np.ndarray]) -> np.ndarray:
        batch = np.empty((len(crops), 3, _INPUT, _INPUT), dtype=np.float32)
        for i, crop in enumerate(crops):
            rgb = cv2.cvtColor(cv2.resize(crop, (_INPUT, _INPUT), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
            batch[i] = ((rgb.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)
        return batch

    def classify(self, crops: Sequence[np.ndarray]) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """(gender probs N x 2, age-band probs N x 9) for BGR face crops; None when unavailable."""
        crops = [c for c in crops if c is not None and c.size]
        if not crops or self._session is None:
            return None
        with self._lock:
            gender, age = self._session.run(["gender", "age"], {self._input_name: self.preprocess(crops)})
        return _softmax(gender), _softmax(age)


def match_faces_to_tracks(
    face_boxes: Sequence[Sequence[float]], track_boxes: Dict[int, Sequence[float]]
) -> Dict[int, int]:
    """face index -> track id. A face belongs to a track when its centre lies in the upper half of
    exactly one person box; ambiguous faces (overlapping people) are left out."""
    out: Dict[int, int] = {}
    for i, (fx1, fy1, fx2, fy2) in enumerate(face_boxes):
        cx, cy = (fx1 + fx2) / 2.0, (fy1 + fy2) / 2.0
        hits = [
            tid for tid, (x1, y1, x2, y2) in track_boxes.items()
            if x1 <= cx <= x2 and y1 <= cy <= y1 + 0.5 * (y2 - y1)
        ]
        if len(hits) == 1:
            out[i] = hits[0]
    taken: Dict[int, int] = {}
    for i, tid in out.items():  # two faces on one person box -> ambiguous, drop both
        taken[tid] = taken.get(tid, 0) + 1
    return {i: tid for i, tid in out.items() if taken[tid] == 1}


@dataclass
class _Visit:
    gender_sum: np.ndarray = field(default_factory=lambda: np.zeros(len(GENDERS)))
    age_sum: np.ndarray = field(default_factory=lambda: np.zeros(len(AGE_BANDS)))
    n: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0


class VisitAggregator:
    """Averages per-face probabilities per (camera, track) and finalises one result per visit."""

    def __init__(self, min_samples: Optional[int] = None, max_samples: Optional[int] = None):
        self.min_samples = max(1, int(min_samples if min_samples is not None else settings.DEMOGRAPHICS_MIN_FACE_SAMPLES))
        self.max_samples = max(self.min_samples, int(
            max_samples if max_samples is not None else settings.DEMOGRAPHICS_MAX_SAMPLES_PER_VISIT
        ))
        self._visits: Dict[Tuple[str, int], _Visit] = {}

    def wants(self, camera: str, track_id: int) -> bool:
        v = self._visits.get((camera, track_id))
        return v is None or v.n < self.max_samples

    def add(self, camera: str, track_id: int, gender_p: np.ndarray, age_p: np.ndarray, ts: Optional[float] = None) -> None:
        ts = time.time() if ts is None else ts
        v = self._visits.get((camera, track_id))
        if v is None:
            v = self._visits[(camera, track_id)] = _Visit(first_ts=ts)
        if v.n >= self.max_samples:
            return
        v.gender_sum += np.asarray(gender_p, dtype=np.float64)
        v.age_sum += np.asarray(age_p, dtype=np.float64)
        v.n += 1
        v.last_ts = ts

    def finish(self, camera: str, track_id: int) -> Optional[dict]:
        """Drop the visit; return its result, or None when it has too few face readings."""
        v = self._visits.pop((camera, track_id), None)
        if v is None or v.n < self.min_samples:
            return None
        g = v.gender_sum / v.n
        a = v.age_sum / v.n
        gi, ai = int(g.argmax()), int(a.argmax())
        return {
            "age_band": AGE_BANDS[ai],
            "age_conf": float(a[ai]),
            "gender": GENDERS[gi] if g[gi] >= GENDER_MIN_PROB else "unknown",
            "gender_conf": float(g[gi]),
            "sample_count": v.n,
            "first_ts": v.first_ts,
            "last_ts": v.last_ts,
        }

    def drop_camera(self, camera: str) -> None:
        for key in [k for k in self._visits if k[0] == camera]:
            del self._visits[key]

    def prune(self, older_than_s: float, now: Optional[float] = None) -> int:
        """Forget visits with no reading for `older_than_s` (their track_visit never arrived)."""
        cutoff = (time.time() if now is None else now) - older_than_s
        stale = [k for k, v in self._visits.items() if v.last_ts < cutoff]
        for k in stale:
            del self._visits[k]
        return len(stale)

    def __len__(self) -> int:
        return len(self._visits)


def summarize(
    rows: Iterable[Tuple[Optional[str], Optional[str]]],
    *,
    total_visits: int,
    installed: bool,
    min_sample: Optional[int] = None,
) -> dict:
    """API payload from per-visit (age_band, gender) rows.

    total_visits: finished visits in the same window (attributed + not); the difference is reported
    as `unattributed`. Percentages are hidden until `min_sample` visits are attributed."""
    min_sample = int(settings.DEMOGRAPHICS_MIN_VISITS if min_sample is None else min_sample)
    age_counts = {b: 0 for b in AGE_BANDS}
    gender_counts = {"male": 0, "female": 0, "unknown": 0}
    n = 0
    for band, gender in rows:
        if band not in age_counts:
            continue
        n += 1
        age_counts[band] += 1
        gender_counts[gender if gender in gender_counts else "unknown"] += 1
    out = {
        "sample_count": n,
        "min_sample": min_sample,
        "unattributed": max(0, int(total_visits) - n),
        "model": {"name": MODEL_NAME, "installed": bool(installed), "licence": MODEL_LICENCE},
        "bias_caveat": BIAS_CAVEAT,
        "age_bands": {},
        "gender": {},
    }
    if n == 0:
        out["status"] = "no_data" if installed else "model_not_installed"
        return out
    if n < min_sample:
        out["status"] = "insufficient_samples"
        return out
    out["status"] = "ok"
    out["age_bands"] = {b: round(100.0 * c / n, 1) for b, c in age_counts.items()}
    out["gender"] = {g: round(100.0 * c / n, 1) for g, c in gender_counts.items()}
    return out


def demographics_installed() -> bool:
    return Path(settings.DEMOGRAPHICS_MODEL_PATH).is_file()


__all__: List[str] = [
    "AGE_BANDS", "GENDERS", "BIAS_CAVEAT", "MODEL_NAME", "DemographicsClassifier",
    "VisitAggregator", "match_faces_to_tracks", "summarize", "demographics_installed",
]
