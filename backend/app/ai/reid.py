"""
OmniTrack AI — person Re-ID: embedding model, crop quality gate, identity gallery.

* ``PersonReID`` turns person crops into 512-d L2-normalised OSNet embeddings
  (``osnet_ain_x1_0`` trained on MSMT17 by default). There is no fallback: if
  torch/torchreid or the Re-ID-trained weights are missing, ``is_loaded`` is
  False and no embedding is produced (ImageNet weights would invent matches).
* ``crop_quality`` is the gate a crop must pass before its embedding may
  enter a tracklet: tall enough, person-shaped, not touching the frame edge,
  not overlapped by another person, confident detection.
* ``IdentityGallery`` holds the *recent* identities in memory (the full
  history lives in pgvector). Each identity keeps up to ``max_views`` diverse
  views; similarity to an identity is the best cosine over its views.

Global-ID assignment (Hungarian matching, topology/transit gating, update
rules) lives in ``app/services/track_coordinator.py``.
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

try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    torch = None
    TORCH_AVAILABLE = False

try:
    import torchreid

    TORCHREID_AVAILABLE = True
except ImportError:  # pragma: no cover
    torchreid = None
    TORCHREID_AVAILABLE = False

EMBEDDING_DIM = 512
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def l2n(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    if v.ndim == 1:
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(n, 1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Crop quality gate
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QualityConfig:
    min_height_px: int = 128          # absolute floor for HD cameras …
    min_height_frac: float = 0.25     # … relaxed to this fraction of frame height on small frames
    min_aspect: float = 0.25          # width / height
    max_aspect: float = 0.6
    edge_margin_px: int = 2
    max_occlusion_iou: float = 0.3
    min_conf: float = 0.6

    @classmethod
    def from_settings(cls) -> "QualityConfig":
        return cls(
            min_height_px=int(settings.REID_MIN_CROP_HEIGHT),
            min_height_frac=float(settings.REID_MIN_CROP_HEIGHT_FRAC),
            min_aspect=float(settings.REID_MIN_ASPECT),
            max_aspect=float(settings.REID_MAX_ASPECT),
            edge_margin_px=int(settings.REID_EDGE_MARGIN_PX),
            max_occlusion_iou=float(settings.REID_MAX_OCCLUSION_IOU),
            min_conf=float(settings.REID_MIN_DET_CONF),
        )

    def min_height(self, frame_h: int) -> float:
        """128 px on a 1080p camera; a quarter of the frame on small (e.g. 288 px CAVIAR) frames."""
        return float(min(self.min_height_px, self.min_height_frac * max(1, frame_h)))


def box_iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, np.float32).reshape(-1, 4)
    b = np.asarray(b, np.float32).reshape(-1, 4)
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)), np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0).astype(np.float32)


def crop_quality(
    xyxy: Sequence[float],
    conf: float,
    frame_wh: Tuple[int, int],
    max_iou_with_others: float,
    cfg: QualityConfig,
) -> Tuple[bool, str, float]:
    """Return ``(passes, reason, score)``. ``score`` ranks crops of one tracklet (higher = better)."""
    x1, y1, x2, y2 = (float(v) for v in xyxy[:4])
    w, h = x2 - x1, y2 - y1
    fw, fh = int(frame_wh[0]), int(frame_wh[1])
    if h <= 0 or w <= 0:
        return False, "empty", 0.0
    if conf < cfg.min_conf:
        return False, "low_conf", 0.0
    min_h = cfg.min_height(fh)
    if h < min_h:
        return False, "too_small", 0.0
    aspect = w / h
    if aspect < cfg.min_aspect or aspect > cfg.max_aspect:
        return False, "aspect", 0.0
    m = cfg.edge_margin_px
    if x1 <= m or y1 <= m or x2 >= fw - 1 - m or y2 >= fh - 1 - m:
        return False, "edge", 0.0
    if max_iou_with_others >= cfg.max_occlusion_iou:
        return False, "occluded", 0.0
    # Prefer confident, larger, unoccluded crops. Size saturates at 2x the minimum.
    size = min(1.0, h / (2.0 * min_h))
    score = float(conf) * (0.5 + 0.5 * size) * (1.0 - max_iou_with_others)
    return True, "ok", score


# ─────────────────────────────────────────────────────────────────────────────
# Embedding model
# ─────────────────────────────────────────────────────────────────────────────


class PersonReID:
    """OSNet embedder. Thread-safe, loaded once, batched."""

    INPUT_HW = (256, 128)

    def __init__(self, model_name: str = "osnet_ain_x1_0", weights: Optional[str] = None, device: str = "auto"):
        from app.ai.detector import resolve_device

        self.model_name = model_name
        self.weights = Path(weights or settings.REID_WEIGHTS_PATH)
        self.model_version = self.weights.stem[:50]
        self.device = resolve_device(device)
        self.model = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._lat_ms: List[float] = []
        self._load()

    def _load(self) -> None:
        if not (TORCH_AVAILABLE and TORCHREID_AVAILABLE):
            self.error = "torch/torchreid not installed"
            logger.error(f"Re-ID disabled: {self.error}")
            return
        if not self.weights.is_file():
            # ImageNet weights are not a fallback: they score most different people as the
            # same person on CAVIAR, i.e. they would invent identities.
            self.error = f"weights missing: {self.weights}"
            logger.error(f"Re-ID disabled: {self.error}. Run scripts/download_default_weights.py.")
            return
        try:
            model = torchreid.models.build_model(name=self.model_name, num_classes=1, pretrained=False)
            torchreid.utils.load_pretrained_weights(model, str(self.weights))
            self.model = model.to(self.device).eval()
            logger.info(f"Re-ID model loaded: {self.model_name} weights={self.weights.name} device={self.device}")
        except Exception as e:  # pragma: no cover - depends on files
            self.error = f"load failed: {e}"
            self.model = None
            logger.error(f"Re-ID disabled: {self.error}")

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def _prep(self, crop: np.ndarray) -> np.ndarray:
        h, w = self.INPUT_HW
        x = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)
        x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return ((x - _MEAN) / _STD).transpose(2, 0, 1)

    def extract_batch(self, crops: Sequence[np.ndarray], batch_size: int = 32) -> Optional[np.ndarray]:
        """(N, 512) L2-normalised float32 embeddings, or ``None`` when the model is not loaded."""
        if self.model is None:
            return None
        if not crops:
            return np.zeros((0, EMBEDDING_DIM), np.float32)
        out = []
        t0 = time.perf_counter()
        with self._lock, torch.inference_mode():
            for i in range(0, len(crops), batch_size):
                x = np.stack([self._prep(c) for c in crops[i:i + batch_size]])
                t = torch.from_numpy(x).to(self.device)
                out.append(self.model(t).float().cpu().numpy())
        self._lat_ms.append((time.perf_counter() - t0) * 1000.0)
        if len(self._lat_ms) > 200:
            del self._lat_ms[:-200]
        return l2n(np.concatenate(out, axis=0))

    def extract_embedding(self, crop: np.ndarray) -> Optional[np.ndarray]:
        e = self.extract_batch([crop])
        return None if e is None else e[0]

    def info(self) -> Dict[str, object]:
        lat = sorted(self._lat_ms)
        return {
            "model": self.model_name,
            "weights": self.weights.name,
            "model_version": self.model_version,
            "device": self.device,
            "loaded": self.is_loaded,
            "error": self.error,
            "batch_latency_ms_p50": round(lat[len(lat) // 2], 1) if lat else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Gallery
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Identity:
    """One person across cameras. ``views`` are diverse L2-normalised embeddings."""

    global_id: str
    views: List[np.ndarray] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_camera_id: Optional[int] = None
    last_bbox: Optional[List[float]] = None
    last_foot_xy: Optional[Tuple[float, float]] = None  # floor-plane metres (calibrated cameras only)
    cameras: List[int] = field(default_factory=list)
    n_updates: int = 0
    restored: bool = False  # loaded from pgvector at start-up

    @property
    def centroid(self) -> Optional[np.ndarray]:
        if not self.views:
            return None
        return l2n(np.mean(np.stack(self.views), axis=0))

    def similarity(self, q: np.ndarray) -> float:
        if not self.views:
            return -1.0
        return float(np.max(np.stack(self.views) @ q))

    def add_view(self, v: np.ndarray, max_views: int) -> bool:
        """Add ``v`` keeping at most ``max_views`` *diverse* views. Returns True if stored."""
        v = l2n(v)
        if len(self.views) < max_views:
            self.views.append(v)
            return True
        V = np.stack(self.views)
        sim_new = V @ v
        # Redundant: nearly identical to a view we already have.
        if float(sim_new.max()) >= 0.97:
            return False
        # Replace one member of the most similar existing pair if the new view is more
        # distinct from the gallery than that pair is from each other.
        S = V @ V.T
        np.fill_diagonal(S, -np.inf)
        i, j = np.unravel_index(int(np.argmax(S)), S.shape)
        if float(sim_new.max()) < float(S[i, j]):
            # Drop the one of the pair that is closer to the rest (least unique).
            ri = float(np.sort(S[i])[-2]) if len(V) > 2 else 0.0
            rj = float(np.sort(S[j])[-2]) if len(V) > 2 else 0.0
            self.views[i if ri >= rj else j] = v
            return True
        return False

    def touch(self, ts: float, camera_id: Optional[int], bbox: Optional[List[float]] = None) -> None:
        self.last_seen = max(self.last_seen, float(ts))
        if camera_id is not None:
            self.last_camera_id = int(camera_id)
            if int(camera_id) not in self.cameras:
                self.cameras.append(int(camera_id))
        if bbox is not None:
            self.last_bbox = list(bbox)

    def summary(self) -> Dict[str, object]:
        return {
            "global_id": self.global_id,
            "views": len(self.views),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "last_camera_id": self.last_camera_id,
            "cameras": list(self.cameras),
            "n_updates": self.n_updates,
            "restored": self.restored,
        }


class IdentityGallery:
    """Recent identities in memory. Not thread-safe; the coordinator holds the lock."""

    def __init__(self, max_identities: int = 2000, max_views: int = 8, prefix: str = "PERSON"):
        self.max_identities = max(10, int(max_identities))
        self.max_views = max(1, int(max_views))
        self.prefix = prefix
        self._ids: Dict[str, Identity] = {}
        self._counter = 0

    # ── ids ──
    def new_id(self) -> str:
        while True:
            self._counter += 1
            gid = f"{self.prefix}-{self._counter:05d}"
            if gid not in self._ids:
                return gid

    def seed_counter(self, gids: Iterable[str]) -> None:
        for gid in gids:
            try:
                if gid.startswith(self.prefix + "-"):
                    self._counter = max(self._counter, int(gid.rsplit("-", 1)[1]))
            except ValueError:
                continue

    @property
    def counter(self) -> int:
        return self._counter

    # ── crud ──
    def create(self, v: np.ndarray, ts: float, camera_id: Optional[int], bbox=None, gid: Optional[str] = None) -> Identity:
        gid = gid or self.new_id()
        ident = Identity(global_id=gid, first_seen=float(ts), last_seen=float(ts))
        ident.add_view(v, self.max_views)
        ident.touch(ts, camera_id, bbox)
        self._ids[gid] = ident  # caller runs evict() with its protected (live) identities
        return ident

    def get(self, gid: str) -> Optional[Identity]:
        return self._ids.get(gid)

    def __contains__(self, gid: str) -> bool:
        return gid in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def identities(self) -> List[Identity]:
        return list(self._ids.values())

    def remove(self, gid: str) -> None:
        self._ids.pop(gid, None)

    def evict(self, protected: Iterable[str] = ()) -> int:
        """Drop the least recently seen identities above ``max_identities``."""
        if len(self._ids) <= self.max_identities:
            return 0
        keep, n = set(protected), 0
        for gid, _ in sorted(self._ids.items(), key=lambda kv: kv[1].last_seen):
            if len(self._ids) <= self.max_identities:
                break
            if gid not in keep:
                del self._ids[gid]
                n += 1
        return n

    def prune_older_than(self, cutoff_ts: float, protected: Iterable[str] = ()) -> int:
        keep = set(protected)
        old = [g for g, i in self._ids.items() if i.last_seen < cutoff_ts and g not in keep]
        for g in old:
            del self._ids[g]
        return len(old)

    # ── search ──
    def similarity_matrix(self, queries: np.ndarray, gids: Sequence[str]) -> np.ndarray:
        """(Q, G) best-view cosine similarity."""
        Q = np.asarray(queries, np.float32).reshape(-1, EMBEDDING_DIM)
        out = np.full((len(Q), len(gids)), -1.0, np.float32)
        for j, gid in enumerate(gids):
            ident = self._ids.get(gid)
            if ident is None or not ident.views:
                continue
            out[:, j] = (Q @ np.stack(ident.views).T).max(axis=1)
        return out

    def search(self, q: np.ndarray, top_k: int = 10, threshold: float = 0.0, exclude: Iterable[str] = ()) -> List[Dict[str, object]]:
        ex = set(exclude)
        gids = [g for g in self._ids if g not in ex]
        if not gids:
            return []
        sims = self.similarity_matrix(l2n(q)[None, :], gids)[0]
        order = np.argsort(-sims)[: max(1, int(top_k))]
        return [
            {"id": gids[i], "global_id": gids[i], "similarity": round(float(sims[i]), 4)}
            for i in order
            if sims[i] >= threshold
        ]

    def stats(self) -> Dict[str, object]:
        n_views = sum(len(i.views) for i in self._ids.values())
        return {
            "total_identities": len(self._ids),
            "total_views": n_views,
            "max_identities": self.max_identities,
            "max_views": self.max_views,
            "id_counter": self._counter,
        }
