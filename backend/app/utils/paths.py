"""
Path resolution helpers for model weight files.

All AI loaders (PersonDetector, FireSmokeDetector, ItemDetector, ...)
should call ``resolve_model_path("yolo11n.pt")`` instead of passing the
bare filename to ``YOLO(...)`` directly. The resolver tries, in order:

  1. An absolute path that exists.
  2. A path relative to ``settings.MODEL_WEIGHTS_DIR`` that exists
     (this is the common case — drop a .pt into ``backend/model_weight``
     and reference it by filename).
  3. The original string (so Ultralytics can still auto-download the
     official COCO weights like ``yolov8n.pt``).

Logging at INFO level on first resolution makes startup transparent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import settings


def resolve_model_path(name: Optional[str]) -> Optional[str]:
    """Resolve a model identifier to an actual loadable path.

    Returns ``None`` if the input is falsy. Otherwise returns a string
    that is safe to pass to ``ultralytics.YOLO()``.
    """
    if not name:
        return None

    p = Path(name)
    if p.is_absolute() and p.exists():
        return str(p)

    weights_dir = Path(settings.MODEL_WEIGHTS_DIR)
    candidate = weights_dir / p.name
    if candidate.exists():
        return str(candidate.resolve())

    # Fall back to the raw string so Ultralytics' own zoo /
    # auto-download path can still kick in for things like "yolov8n.pt".
    return name
