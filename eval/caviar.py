"""CAVIAR shopping-centre ground truth (EC Funded CAVIAR project/IST 2001 37540).

Every local sequence folder holds two synchronised views of the same scene:
``<seq>_cam1_corridor.mpg`` (looking along the corridor) and
``<seq>_cam2_front.mpg`` (looking at the shop front), each with a GT XML
(``gt_cam1_corridor.xml`` / ``gt_cam2_front.xml``). Boxes are centre-x,
centre-y, width, height in pixels on 384x288 frames; frame numbers are 0-based
and follow decode order.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import numpy as np

from common import CAVIAR_DIR

VIEWS = ("cam1_corridor", "cam2_front")


def sequences(root: Path = CAVIAR_DIR) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("gt_*.xml")))


def video_path(seq: str, view: str, root: Path = CAVIAR_DIR) -> Path:
    return root / seq / f"{seq}_{view}.mpg"


def gt_path(seq: str, view: str, root: Path = CAVIAR_DIR) -> Path:
    return root / seq / f"gt_{view}.xml"


@lru_cache(maxsize=8)
def load_gt(seq: str, view: str, root: Path = CAVIAR_DIR) -> dict[int, np.ndarray]:
    """frame -> (K, 5) array of [id, x1, y1, x2, y2] in pixels."""
    out: dict[int, np.ndarray] = {}
    for fr in ET.parse(gt_path(seq, view, root)).getroot().iter("frame"):
        n = int(fr.get("number"))
        rows = []
        for obj in fr.iter("object"):
            b = obj.find("box")
            xc, yc, w, h = (float(b.get(k)) for k in ("xc", "yc", "w", "h"))
            rows.append((int(obj.get("id")), xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2))
        out[n] = np.asarray(rows, dtype=np.float64).reshape(-1, 5)
    return out


def iter_frames(path: Path, wanted: set[int] | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Decode a clip sequentially (MPEG-1 seeking is unreliable) yielding (index, BGR frame)."""
    import cv2  # noqa: PLC0415

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(path)
    last = max(wanted) if wanted else None
    i = -1
    try:
        while True:
            ok = cap.grab()
            i += 1
            if not ok or (last is not None and i > last):
                break
            if wanted is not None and i not in wanted:
                continue
            ok, frame = cap.retrieve()
            if ok:
                yield i, frame
    finally:
        cap.release()
