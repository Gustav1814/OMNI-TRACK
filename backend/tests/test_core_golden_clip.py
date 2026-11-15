"""Golden clip: the configured person detector + ByteTrack on 20 s of CAVIAR, scored against GT.

Runs the exact components and settings the live pipeline uses (``PersonDetector`` with
PERSON_CONF / PERSON_IOU / the device's model+imgsz, ``PersonTracker`` with the TRACK_*
settings) at the pipeline's PROCESSING_FPS on CAVIAR ThreePastShop1 (corridor view,
frames 0-499 = 20 s @ 25 fps, 9 annotated people).

Measured per GT person (IoU >= 0.5 Hungarian matching on every processed frame):
* ``id_switches``  - CLEAR-MOT IDSW: a GT person matched to a different confirmed track
                      than the last time it was matched.
* ``confirmed``    - distinct confirmed track IDs (fragmentation shows up here).
* ``recall``       - fraction of GT boxes (on processed frames) covered by a confirmed track.

Reference run (yolo11s@640, CPU, PROCESSING_FPS=6 -> 6.25 fps, 2026-11-15): 9 GT people,
779 GT boxes, 9 confirmed tracks, 1 ID switch, recall 0.756 (one person split in two).
The bounds leave headroom for CPU/GPU numeric differences but fail on a real regression
(e.g. conf back to 0.5, buffer in frames instead of seconds, IDs from the Kalman counter,
duplicate tracks from an ensemble).

Slow (loads YOLO, decodes video): run under the ML lock, e.g.
    flock <ml.lock> .venv/bin/python -m pytest -m slow tests/test_core_golden_clip.py -s
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

CLIP_DIR = Path(__file__).resolve().parent.parent / "storage" / "demo_videos" / "caviar" / "ThreePastShop1"
VIDEO = CLIP_DIR / "ThreePastShop1_cam1_corridor.mpg"
GT_XML = CLIP_DIR / "gt_cam1_corridor.xml"
START, END = 0, 500  # 20 s at 25 fps
IOU_MATCH = 0.5

# Bounds (reference run in the module docstring: 1 / 9 / 0.756). GT has 9 people in this window.
MAX_ID_SWITCHES = 3
MAX_CONFIRMED_TRACKS = 12
MIN_RECALL = 0.68

pytestmark = pytest.mark.slow


def _gt(xml_path: Path):
    out = {}
    for fr in ET.parse(xml_path).getroot().iter("frame"):
        n = int(fr.get("number"))
        if not (START <= n < END):
            continue
        rows = []
        for obj in fr.iter("object"):
            b = obj.find("box")
            if b is None:
                continue
            xc, yc, w, h = (float(b.get(k)) for k in ("xc", "yc", "w", "h"))
            rows.append((int(obj.get("id")), [xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2]))
        out[n] = rows
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def _run_clip():
    cv2 = pytest.importorskip("cv2")
    torch = pytest.importorskip("torch")
    from scipy.optimize import linear_sum_assignment

    from app.ai.detector import PersonDetector, resolve_device
    from app.ai.tracker import PersonTracker
    from app.config import settings

    if not VIDEO.is_file() or not GT_XML.is_file():
        pytest.skip(f"CAVIAR clip not present: {CLIP_DIR}")
    torch.set_num_threads(4)
    device = resolve_device(settings.DEVICE)
    model_path, imgsz = settings.person_model_for(device)
    if not Path(model_path).is_file():
        pytest.skip(f"person model missing: {model_path} (scripts/download_default_weights.py)")
    det = PersonDetector(
        model_path, conf=settings.PERSON_CONF, iou=settings.PERSON_IOU, imgsz=imgsz,
        device=device, export=settings.DETECTOR_EXPORT,
    )
    cap = cv2.VideoCapture(str(VIDEO))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(src_fps / max(1, settings.PROCESSING_FPS)))
    fps = src_fps / step
    trk = PersonTracker(
        fps=fps,
        track_high_thresh=settings.TRACK_HIGH_THRESH,
        track_low_thresh=settings.TRACK_LOW_THRESH,
        new_track_thresh=settings.TRACK_NEW_THRESH,
        match_thresh=settings.TRACK_MATCH_THRESH,
        buffer_s=settings.TRACK_BUFFER_S,
        min_hits=settings.TRACK_MIN_HITS,
    )
    gt = _gt(GT_XML)

    last_match: dict = {}
    id_switches = 0
    gt_total = gt_hit = 0
    confirmed_ids = set()
    per_gt_tracks = defaultdict(set)
    n = 0
    try:
        while n < END:
            ok, frame = cap.read()
            if not ok:
                break
            if n < START or (n - START) % step:
                n += 1
                continue
            d = det.predict_batch([frame])[0]
            out = trk.update(d.xyxy, d.conf, ts=n / src_fps)
            conf_tracks = out.confirmed
            confirmed_ids.update(t.track_id for t in conf_tracks)
            rows = gt.get(n, [])
            gt_total += len(rows)
            if rows and conf_tracks:
                g = np.array([r[1] for r in rows], np.float32)
                p = np.array([t.xyxy for t in conf_tracks], np.float32)
                iou = _iou(g, p)
                gi, pi = linear_sum_assignment(-iou)
                for i, j in zip(gi, pi):
                    if iou[i, j] < IOU_MATCH:
                        continue
                    gid, tid = rows[i][0], conf_tracks[j].track_id
                    gt_hit += 1
                    per_gt_tracks[gid].add(tid)
                    if gid in last_match and last_match[gid] != tid:
                        id_switches += 1
                    last_match[gid] = tid
            n += 1
    finally:
        cap.release()
    gt_ids = {gid for rows in gt.values() for gid, _ in rows}
    return {
        "model": Path(model_path).name, "imgsz": imgsz, "device": device, "processing_fps": round(fps, 2),
        "gt_people": len(gt_ids), "confirmed_tracks": len(confirmed_ids), "id_switches": id_switches,
        "recall": gt_hit / max(1, gt_total), "gt_boxes": gt_total,
        "fragments": {g: len(t) for g, t in sorted(per_gt_tracks.items())},
    }


def test_golden_clip_tracking_quality():
    r = _run_clip()
    print(f"\ngolden clip: {r}")
    assert r["gt_people"] >= 5, r
    assert r["id_switches"] <= MAX_ID_SWITCHES, r
    assert r["confirmed_tracks"] <= MAX_CONFIRMED_TRACKS, r
    assert r["recall"] >= MIN_RECALL, r
