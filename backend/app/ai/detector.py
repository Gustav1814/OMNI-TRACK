"""
OmniTrack AI — person detector (one model, batched, optional export).

The person stream uses exactly one detector. It runs at a deliberately low
confidence (``conf=0.1``) with ``classes=[person]`` and NMS ``iou=0.6``:
ByteTrack's two-stage association decides which boxes become tracks, so the
detector must not throw away the weak boxes of partly-occluded people.

``predict_batch`` takes the frames of several cameras and runs one
``model.predict([...])`` call, which is what the shared inference worker in
the pipeline uses.

Export (``DETECTOR_EXPORT``):
  * ``none``     – plain PyTorch (FP16 on CUDA).
  * ``openvino`` – CPU; needs the ``openvino`` package.
  * ``onnx``     – ONNX Runtime (CPU, or CUDA when onnxruntime-gpu is present).
  * ``engine``   – TensorRT FP16 on CUDA; needs ``tensorrt``.
  * ``auto``     – CPU: OpenVINO, else ONNX Runtime (yolo11s@640 on CAVIAR,
                   i5-1245U/4 threads: ONNX 91 ms vs PyTorch 192 ms per frame,
                   identical AP50); CUDA: TensorRT when installed, else PyTorch FP16.
Every export failure logs a warning and falls back to PyTorch; it never
disables detection. Exports are cached next to the weights
(``model_weight/exports/``) and reused on the next start.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np
from loguru import logger

from app.config import settings

try:
    from ultralytics import YOLO

    ULTRALYTICS_AVAILABLE = True
except ImportError:  # pragma: no cover
    YOLO = None
    ULTRALYTICS_AVAILABLE = False
    logger.error("Ultralytics not installed. Person detection is unavailable.")


@dataclass
class Detections:
    """Person boxes for one frame. ``xyxy`` is (N, 4) float32 pixels, ``conf`` is (N,)."""

    xyxy: np.ndarray
    conf: np.ndarray

    @classmethod
    def empty(cls) -> "Detections":
        return cls(np.zeros((0, 4), np.float32), np.zeros(0, np.float32))

    def __len__(self) -> int:
        return int(len(self.conf))


def resolve_device(device: str = "auto") -> str:
    """``auto`` → ``cuda`` when a CUDA device is present, else ``cpu``."""
    device = (device or "auto").strip().lower()
    if device != "auto":
        return device
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def export_runtime_available(fmt: str) -> bool:
    return {
        "openvino": _has("openvino"),
        "onnx": _has("onnx") and _has("onnxruntime"),
        "engine": _has("tensorrt"),
        "none": True,
    }.get(fmt, False)


def choose_export_format(requested: str, device: str) -> str:
    requested = (requested or "none").strip().lower()
    if requested == "auto":
        if device == "cpu":
            for fmt in ("openvino", "onnx"):
                if export_runtime_available(fmt):
                    return fmt
            return "none"
        if device.startswith("cuda"):
            return "engine" if export_runtime_available("engine") else "none"
        return "none"
    if requested not in {"none", "openvino", "onnx", "engine"}:
        logger.warning(f"Unknown DETECTOR_EXPORT={requested!r}; using PyTorch")
        return "none"
    if requested == "engine" and not device.startswith("cuda"):
        logger.warning("DETECTOR_EXPORT=engine needs CUDA; using PyTorch on this device")
        return "none"
    if not export_runtime_available(requested):
        logger.warning(f"DETECTOR_EXPORT={requested} requested but its runtime is not installed; using PyTorch")
        return "none"
    return requested


class PersonDetector:
    """One YOLO person detector. Thread-safe; load once, never unloaded."""

    def __init__(
        self,
        model_path: str,
        conf: float = 0.1,
        iou: float = 0.6,
        imgsz: int = 640,
        device: str = "auto",
        export: str = "none",
        max_det: int = 300,
    ):
        if not ULTRALYTICS_AVAILABLE:
            raise RuntimeError("ultralytics is not installed")
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.max_det = int(max_det)
        self.device = resolve_device(device)
        self.half = self.device.startswith("cuda")
        self._lock = threading.Lock()
        self.weights = self._resolve(model_path)
        self.name = self.weights.name
        base = YOLO(str(self.weights), task="detect")
        names = dict(getattr(base, "names", {}) or {})
        self.person_classes = [int(k) for k, v in names.items() if str(v).lower() == "person"]
        if not self.person_classes:
            raise ValueError(
                f"{self.name} has no 'person' class (classes: {list(names.values())[:8]}); "
                "it cannot be used as the person detector"
            )
        self.backend = "pytorch"
        self.model = base
        fmt = choose_export_format(export, self.device)
        if fmt != "none":
            exported = self._load_export(base, fmt)
            if exported is not None:
                self.model, self.backend = exported, fmt
        self._lat_ms: List[float] = []
        self._warmup()
        logger.info(
            f"Person detector ready: {self.name} backend={self.backend} device={self.device} "
            f"imgsz={self.imgsz} conf={self.conf} iou={self.iou} classes={self.person_classes} half={self.half}"
        )

    # ── loading ───────────────────────────────────────────────────
    @staticmethod
    def _resolve(model_path: str) -> Path:
        path = Path(model_path).expanduser()
        if not path.is_absolute():
            path = settings.weight_path(model_path)
        if not path.is_file() and not path.is_dir():
            raise FileNotFoundError(f"Person detector weights not found: {path}")
        return path

    def _export_dir(self) -> Path:
        d = Path(settings.MODEL_WEIGHTS_DIR) / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_export(self, base, fmt: str):
        """Export once (cached by name/imgsz/format), then load. ``None`` on any failure."""
        stem = f"{self.weights.stem}_{self.imgsz}"
        target = {
            "openvino": self._export_dir() / f"{stem}_openvino_model",
            "onnx": self._export_dir() / f"{stem}.onnx",
            "engine": self._export_dir() / f"{stem}_fp16.engine",
        }[fmt]
        try:
            if not target.exists():
                t0 = time.perf_counter()
                logger.info(f"Exporting {self.name} to {fmt} at imgsz={self.imgsz} (one-off, cached in {target.parent})")
                kwargs = dict(format=fmt, imgsz=self.imgsz, verbose=False)
                if fmt == "engine":
                    kwargs.update(half=True, dynamic=True, batch=max(1, int(settings.MAX_CAMERAS)), device=0)
                else:
                    kwargs.update(dynamic=True)
                out = Path(str(base.export(**kwargs)))
                if out.resolve() != target.resolve():
                    out.rename(target)
                logger.info(f"Export done in {time.perf_counter() - t0:.0f}s → {target}")
            return YOLO(str(target), task="detect")
        except Exception as e:
            logger.warning(f"{fmt} export/load of {self.name} failed ({e}); using PyTorch")
            return None

    def _warmup(self) -> None:
        try:
            self.predict_batch([np.zeros((self.imgsz, self.imgsz, 3), np.uint8)])
        except Exception as e:
            raise RuntimeError(f"Person detector {self.name} failed its warm-up inference: {e}") from e
        self._lat_ms.clear()

    # ── inference ─────────────────────────────────────────────────
    def predict_batch(self, frames: Sequence[np.ndarray]) -> List[Detections]:
        """One batched predict over frames from any number of cameras."""
        if not frames:
            return []
        kwargs = dict(
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            classes=self.person_classes,
            max_det=self.max_det,
            verbose=False,
        )
        if self.backend == "pytorch":
            kwargs.update(device=self.device)
            if self.half:  # ultralytics 8.4 warns on every call when half= is passed at all
                kwargs["half"] = True
        t0 = time.perf_counter()
        with self._lock:
            results = self.model.predict(list(frames), **kwargs)
        self._lat_ms.append((time.perf_counter() - t0) * 1000.0)
        if len(self._lat_ms) > 200:
            del self._lat_ms[:-200]
        out: List[Detections] = []
        for r in results:
            if r.boxes is None or len(r.boxes) == 0:
                out.append(Detections.empty())
                continue
            out.append(Detections(
                r.boxes.xyxy.cpu().numpy().astype(np.float32),
                r.boxes.conf.cpu().numpy().astype(np.float32),
            ))
        return out

    def detect(self, frame: np.ndarray) -> Detections:
        return self.predict_batch([frame])[0]

    # ── introspection ─────────────────────────────────────────────
    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def get_class_names(self) -> Dict[int, str]:
        return {i: "person" for i in self.person_classes}

    def info(self) -> Dict[str, object]:
        lat = sorted(self._lat_ms)
        return {
            "model": self.name,
            "backend": self.backend,
            "device": self.device,
            "imgsz": self.imgsz,
            "conf": self.conf,
            "iou": self.iou,
            "half": self.half,
            "batch_latency_ms_p50": round(lat[len(lat) // 2], 1) if lat else None,
        }


class SpecialistDetector:
    """A non-person YOLO model (fire/smoke, products) run on its own cadence.

    Its boxes never enter the tracker or any people count.
    """

    def __init__(self, model_path: str, conf: float = 0.4, imgsz: int = 640, device: str = "auto", label: str = ""):
        if not ULTRALYTICS_AVAILABLE:
            raise RuntimeError("ultralytics is not installed")
        self.weights = PersonDetector._resolve(model_path)
        self.name = self.weights.name
        self.label = label or self.weights.stem
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = resolve_device(device)
        self.half = self.device.startswith("cuda")
        self.model = YOLO(str(self.weights), task="detect")
        self.names = {int(k): str(v) for k, v in (getattr(self.model, "names", {}) or {}).items()}
        self._lock = threading.Lock()

    def predict_batch(self, frames: Sequence[np.ndarray]) -> List[List[Dict[str, object]]]:
        if not frames:
            return []
        with self._lock:
            results = self.model.predict(
                list(frames), conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False,
                **({"half": True} if self.half else {}),
            )
        out = []
        for r in results:
            items = []
            if r.boxes is not None and len(r.boxes):
                xyxy = r.boxes.xyxy.cpu().numpy()
                conf = r.boxes.conf.cpu().numpy()
                cls = r.boxes.cls.cpu().numpy().astype(int)
                for b, c, k in zip(xyxy, conf, cls):
                    x1, y1, x2, y2 = (float(v) for v in b)
                    items.append({
                        "bbox": [x1, y1, x2 - x1, y2 - y1],
                        "xyxy": [x1, y1, x2, y2],
                        "confidence": float(c),
                        "class_name": self.names.get(int(k), str(int(k))),
                    })
            out.append(items)
        return out
