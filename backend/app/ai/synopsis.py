"""
OmniTrack AI — streaming video synopsis (Pritch/Rav-Acha style, bounded memory).

Condenses long surveillance footage by moving activity "tubes" (one moving
object across the frames where it is visible) in time so events that happened
at different moments play simultaneously on top of a static background.

Pipeline (memory is O(frame) + O(index), never O(video)):

1. Background   — median of ~50 frames sampled across the clip (seeking; falls
                  back to reservoir sampling in one pass when seeking fails).
                  The median is computed in row bands so the float workspace
                  stays small even at 1080p.
2. Tubes        — ONE sequential decode. MOG2 (primed on the samples) gives the
                  foreground; connected components are linked frame-to-frame by
                  IoU. Each object's crop + mask is appended to an on-disk spool
                  (JPEG + PNG), and finished tubes (frame indices + boxes) are
                  written to ``<output>.tubes.jsonl``. Only *open* tubes live in RAM.
3. Scheduling   — greedy temporal shifting on a coarse occupancy grid: every
                  tube gets the earliest offset with little spatial collision and
                  at most ``max_parallel_tubes`` concurrent tubes. The timeline
                  grows past the target only when a tube cannot fit, so the
                  reported compression is what was actually achieved.
4. Composite    — ONE streaming pass over synopsis frames: background copy +
                  feathered alpha-blend of the spooled crops scheduled for that
                  frame, each labelled with its original timestamp. Written
                  straight to the encoder; the spool is deleted afterwards.

Public API (unchanged):
    metrics = VideoSynopsis(compression_target=10).process_video("in.mpg", "out.webm")
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import tempfile
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger
from numpy.lib.stride_tricks import sliding_window_view

Box = Tuple[int, int, int, int]  # x, y, w, h in full-resolution pixels

_GRID_W, _GRID_H = 32, 24          # collision grid for scheduling
_PROC_MAX_W = 480                  # motion analysis resolution
_MEDIAN_BYTES_BUDGET = 192 << 20   # bound on the sampled-frame stack


@dataclass
class _OpenTube:
    tube_id: int
    start: int
    last_frame: int
    last_box: Box
    frames: List[int] = field(default_factory=list)
    boxes: List[Box] = field(default_factory=list)
    area_sum: float = 0.0


@dataclass
class TubeMeta:
    tube_id: int
    start: int
    end: int
    n_frames: int
    mean_area: float
    occupancy: np.ndarray  # (steps, grid cells) bool, sampled every `stride` frames

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _iou(a: Box, b: Box) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = min(ax2, bx2) - max(a[0], b[0])
    ih = min(ay2, by2) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / float(a[2] * a[3] + b[2] * b[3] - inter)


def _centre_dist(a: Box, b: Box) -> float:
    return math.hypot(a[0] + a[2] / 2 - b[0] - b[2] / 2, a[1] + a[3] / 2 - b[1] - b[3] / 2)


def _open_writer(path: str, fps: float, size: Tuple[int, int]) -> Tuple[cv2.VideoWriter, str]:
    """``.webm`` -> VP8 (browser-playable, ~40 ms/frame at 720p on CPU; VP9 was 10x
    slower). ``.mp4`` -> H.264 when the OpenCV build has it, else mp4v (not
    playable in most browsers, download only)."""
    suffix = Path(path).suffix.lower()
    order = {".webm": ["VP80", "VP90"], ".mp4": ["avc1", "mp4v"], ".avi": ["MJPG"]}.get(suffix, [])
    for codec in order:
        w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), fps, size)
        if w.isOpened():
            return w, codec
        w.release()
    raise RuntimeError(f"No usable video encoder for {suffix}")


class VideoSynopsis:
    def __init__(
        self,
        compression_target: float = 10.0,
        min_area: int = 400,
        min_tube_seconds: float = 0.6,
        max_gap_frames: int = 6,
        max_parallel_tubes: int = 6,
        max_overlap: float = 0.15,
        background_samples: int = 50,
        max_synopsis_seconds: Optional[float] = None,
        spool_dir: Optional[str] = None,
        progress: Optional[Callable[[str, float], None]] = None,
    ):
        self.compression_target = max(1.0, float(compression_target))
        self.min_area = int(min_area)            # in full-resolution px^2
        self.min_tube_seconds = float(min_tube_seconds)
        self.max_gap_frames = int(max_gap_frames)
        self.max_parallel_tubes = max(1, int(max_parallel_tubes))
        self.max_overlap = float(max_overlap)
        self.background_samples = max(5, int(background_samples))
        self.max_synopsis_seconds = max_synopsis_seconds
        self.spool_dir = spool_dir
        self._progress = progress or (lambda stage, frac: None)

    # ───────────────────────────── public ─────────────────────────────

    def process_video(self, video_path: str, output_path: str) -> Dict[str, Any]:
        t0 = time.monotonic()
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not (1.0 <= fps <= 240.0):
            fps = 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        if width <= 0 or height <= 0:
            raise ValueError("Video has no decodable frames")

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tubes_path = out_path.with_suffix(".tubes.jsonl")
        work = Path(tempfile.mkdtemp(prefix="synopsis_", dir=self.spool_dir or str(out_path.parent)))
        try:
            self._progress("background", 0.0)
            background, samples = self._estimate_background(str(video_path), declared_frames, width, height)

            self._progress("tubes", 0.05)
            n_frames, tubes, index, spool_bytes, raw_tubes = self._extract_tubes(
                str(video_path), background, samples, fps, width, height,
                declared_frames, work / "spool.bin", tubes_path,
            )
            del samples
            if n_frames == 0:
                raise ValueError("Video has no decodable frames")

            self._progress("schedule", 0.75)
            stride = self._stride(fps)
            offsets, syn_len, dropped = self._schedule(tubes, n_frames, fps, stride)

            self._progress("composite", 0.8)
            codec = self._composite(
                background, tubes, offsets, index, syn_len, work / "spool.bin",
                str(out_path), fps, width, height,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        preserved = len(offsets)
        kept = len(tubes)
        original_duration = n_frames / fps
        synopsis_duration = syn_len / fps
        result = {
            "status": "completed" if kept else "no_activity",
            "output_path": str(out_path),
            "tubes_path": str(tubes_path),
            "codec": codec,
            "fps": round(fps, 3),
            "resolution": [width, height],
            "original_frames": n_frames,
            "synopsis_frames": syn_len,
            "original_duration": round(original_duration, 2),
            "synopsis_duration": round(synopsis_duration, 2),
            "compression_target": self.compression_target,
            "compression_ratio": round(n_frames / syn_len, 2) if syn_len else None,
            "tubes_detected": raw_tubes,
            "tubes_kept": kept,
            "tubes_filtered_as_noise": raw_tubes - kept,
            "tubes_preserved": preserved,
            "tubes_dropped": dropped,
            "tubes_preserved_pct": round(100.0 * preserved / kept, 2) if kept else None,
            "object_frames": int(sum(t.n_frames for t in tubes)),
            "background_samples": int(self._bg_used),
            "spool_bytes": int(spool_bytes),
            "processing_seconds": round(time.monotonic() - t0, 2),
        }
        self._progress("done", 1.0)
        logger.info(
            "[Synopsis] {} frames -> {} ({}x), tubes kept {}/{}, preserved {}",
            n_frames, syn_len, result["compression_ratio"], kept, raw_tubes, preserved,
        )
        return result

    # ───────────────────────────── 1. background ─────────────────────────────

    def _estimate_background(self, path: str, total: int, w: int, h: int):
        frame_bytes = w * h * 3
        n = int(min(self.background_samples, max(5, _MEDIAN_BYTES_BUDGET // max(1, frame_bytes))))
        samples: List[np.ndarray] = []
        cap = cv2.VideoCapture(path)
        try:
            if total > n * 2:
                for idx in np.linspace(0, total - 1, n).astype(int):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.shape[:2] == (h, w):
                        samples.append(frame)
            if len(samples) < max(5, n // 2):
                # Seeking unreliable / unknown length: reservoir sample in one pass.
                samples = []
                cap.release()
                cap = cv2.VideoCapture(path)
                rng = random.Random(1234)
                seen = 0
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    seen += 1
                    if len(samples) < n:
                        samples.append(frame)
                    else:
                        j = rng.randrange(seen)
                        if j < n:
                            samples[j] = frame
        finally:
            cap.release()
        if not samples:
            raise ValueError("Video has no decodable frames")
        self._bg_used = len(samples)
        stack = np.stack(samples, axis=0)  # uint8 (n, h, w, 3)
        bg = np.empty((h, w, 3), dtype=np.uint8)
        band = max(8, int(8_000_000 // max(1, len(samples) * w * 3)))  # rows per median band
        for y in range(0, h, band):
            bg[y:y + band] = np.median(stack[:, y:y + band], axis=0).astype(np.uint8)
        del stack
        return bg, samples

    # ───────────────────────────── 2. tubes ─────────────────────────────

    @staticmethod
    def _stride(fps: float) -> int:
        return max(1, int(round(fps / 5.0)))  # occupancy sampled at ~5 Hz

    def _extract_tubes(self, path, background, samples, fps, w, h, declared, spool_path, tubes_path):
        scale = min(1.0, _PROC_MAX_W / float(w))
        pw, ph = max(1, int(w * scale)), max(1, int(h * scale))
        min_area_p = max(12.0, self.min_area * scale * scale)
        min_len = max(3, int(round(self.min_tube_seconds * fps)))
        stride = self._stride(fps)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=24, detectShadows=True)
        bg_small = cv2.resize(background, (pw, ph), interpolation=cv2.INTER_AREA)
        for _ in range(3):
            mog.apply(bg_small, learningRate=0.5)
        for s in samples:
            mog.apply(cv2.resize(s, (pw, ph), interpolation=cv2.INTER_AREA), learningRate=0.02)
        bg_gray = cv2.GaussianBlur(cv2.cvtColor(bg_small, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        # Index columns: tube_id, frame, x, y, w, h, spool offset, jpeg len, mask len
        cols = [array("q") for _ in range(9)]
        open_tubes: List[_OpenTube] = []
        kept: List[TubeMeta] = []
        raw_count = 0
        next_id = 0
        spool_bytes = 0

        def close(t: _OpenTube, fh) -> None:
            nonlocal raw_count
            raw_count += 1
            if len(t.frames) < min_len or (t.last_frame - t.start + 1) < min_len:
                t.boxes.clear()
                return
            occ = np.zeros(((t.last_frame - t.start) // stride + 1, _GRID_W * _GRID_H), dtype=bool)
            for f, (x, y, bw, bh) in zip(t.frames, t.boxes):
                if (f - t.start) % stride:
                    continue
                gx0, gx1 = int(x * _GRID_W / w), int(math.ceil((x + bw) * _GRID_W / w))
                gy0, gy1 = int(y * _GRID_H / h), int(math.ceil((y + bh) * _GRID_H / h))
                cell = np.zeros((_GRID_H, _GRID_W), dtype=bool)
                cell[gy0:max(gy1, gy0 + 1), gx0:max(gx1, gx0 + 1)] = True
                occ[(f - t.start) // stride] = cell.ravel()
            kept.append(TubeMeta(t.tube_id, t.start, t.last_frame, len(t.frames),
                                 t.area_sum / max(1, len(t.frames)), occ))
            fh.write(json.dumps({
                "tube_id": t.tube_id, "start": t.start, "end": t.last_frame,
                "frames": t.frames, "boxes": [list(b) for b in t.boxes],
            }, separators=(",", ":")) + "\n")
            t.boxes.clear()

        cap = cv2.VideoCapture(path)
        frame_idx = -1
        with open(spool_path, "wb") as spool, open(tubes_path, "w", encoding="utf-8") as tfh:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frame_idx += 1
                if frame.shape[:2] != (h, w):
                    frame = cv2.resize(frame, (w, h))
                small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
                fg = mog.apply(small, learningRate=0.002)
                fg = np.where(fg == 255, 255, 0).astype(np.uint8)  # drop shadows (127)
                diff = cv2.absdiff(cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0), bg_gray)
                fg = cv2.bitwise_and(fg, np.where(diff > 18, 255, 0).astype(np.uint8))
                fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
                fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=2)
                fg = cv2.dilate(fg, kernel)

                n_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
                dets: List[Tuple[Box, int]] = []
                for lbl in range(1, n_lbl):
                    x, y, bw, bh, area = stats[lbl]
                    if area < min_area_p:
                        continue
                    pad = 2
                    x0, y0 = max(0, x - pad), max(0, y - pad)
                    x1, y1 = min(pw, x + bw + pad), min(ph, y + bh + pad)
                    box = (int(x0 / scale), int(y0 / scale),
                           max(1, int(math.ceil((x1 - x0) / scale))), max(1, int(math.ceil((y1 - y0) / scale))))
                    box = (box[0], box[1], min(box[2], w - box[0]), min(box[3], h - box[1]))
                    dets.append((box, lbl))

                # Greedy IoU / proximity association with open tubes.
                pairs = []
                for di, (box, _) in enumerate(dets):
                    for ti, t in enumerate(open_tubes):
                        iou = _iou(box, t.last_box)
                        near = _centre_dist(box, t.last_box) < 0.6 * max(box[2], box[3], t.last_box[2], t.last_box[3])
                        if iou > 0.1 or near:
                            pairs.append((iou + (0.05 if near else 0.0), di, ti))
                pairs.sort(reverse=True)
                used_d, used_t, assign = set(), set(), {}
                for _, di, ti in pairs:
                    if di in used_d or ti in used_t:
                        continue
                    used_d.add(di)
                    used_t.add(ti)
                    assign[di] = open_tubes[ti]
                for di, (box, _) in enumerate(dets):
                    if di not in assign:
                        t = _OpenTube(next_id, frame_idx, frame_idx, box)
                        next_id += 1
                        open_tubes.append(t)
                        assign[di] = t

                for di, (box, lbl) in enumerate(dets):
                    t = assign[di]
                    t.last_frame, t.last_box = frame_idx, box
                    t.frames.append(frame_idx)
                    t.boxes.append(box)
                    t.area_sum += box[2] * box[3]
                    x, y, bw, bh = box
                    crop = frame[y:y + bh, x:x + bw]
                    m_small = (labels == lbl).astype(np.uint8) * 255
                    mask = cv2.resize(m_small, (w, h), interpolation=cv2.INTER_NEAREST)[y:y + bh, x:x + bw]
                    ok1, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    ok2, png = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                    if not (ok1 and ok2):
                        continue
                    for col, v in zip(cols, (t.tube_id, frame_idx, x, y, bw, bh, spool_bytes, len(jpg), len(png))):
                        col.append(int(v))
                    spool.write(jpg.tobytes())
                    spool.write(png.tobytes())
                    spool_bytes += len(jpg) + len(png)

                still_open = []
                for t in open_tubes:
                    if frame_idx - t.last_frame > self.max_gap_frames:
                        close(t, tfh)
                    else:
                        still_open.append(t)
                open_tubes = still_open
                if declared and frame_idx % 50 == 0:
                    self._progress("tubes", 0.05 + 0.7 * min(1.0, frame_idx / declared))
            for t in open_tubes:
                close(t, tfh)
        cap.release()

        index = np.stack([np.frombuffer(c, dtype=np.int64) for c in cols], axis=1) if cols[0] else np.zeros((0, 9), np.int64)
        return frame_idx + 1, kept, index, spool_bytes, raw_count

    # ───────────────────────────── 3. scheduling ─────────────────────────────

    def _schedule(self, tubes: List[TubeMeta], n_frames: int, fps: float, stride: int):
        """Return ({tube_id: synopsis start frame}, synopsis length in frames, dropped count)."""
        if not tubes:
            return {}, 0, 0
        longest = max(t.length for t in tubes)
        target = max(longest, int(math.ceil(n_frames / self.compression_target)))
        cap_len = n_frames
        if self.max_synopsis_seconds:
            cap_len = min(cap_len, max(1, int(self.max_synopsis_seconds * fps)))
        cap_len = max(cap_len, min(target, n_frames))
        steps_cap = cap_len // stride + 2
        occ = np.zeros((steps_cap, _GRID_W * _GRID_H), dtype=np.uint16)
        count = np.zeros(steps_cap, dtype=np.uint16)
        cur_len = min(target, cap_len)
        offsets: Dict[int, int] = {}
        dropped = 0

        for t in sorted(tubes, key=lambda m: (m.start, m.tube_id)):
            m = t.occupancy.shape[0]
            n_cand = (cap_len - t.length) // stride + 1
            if n_cand <= 0:
                dropped += 1
                continue
            occ_f = t.occupancy.astype(np.float32)
            area = float(occ_f.sum()) or 1.0
            cost = np.zeros(n_cand, dtype=np.float32)
            for k in range(m):
                if occ_f[k].any():
                    cost += occ[k:k + n_cand].astype(np.float32) @ occ_f[k]
            peak = sliding_window_view(count[:n_cand + m - 1], m).max(axis=1)[:n_cand]
            frac = cost / area
            feasible = (frac <= self.max_overlap) & (peak < self.max_parallel_tubes)
            ends = np.arange(n_cand) * stride + t.length
            inside = ends <= cur_len
            choice = None
            for mask in (feasible & inside, feasible):
                idx = np.flatnonzero(mask)
                if idx.size:
                    # earliest low-collision slot; a small penalty keeps chronology.
                    score = frac[idx] + 0.05 * idx / max(1, n_cand)
                    choice = int(idx[np.argmin(score)])
                    break
            if choice is None:
                dropped += 1
                continue
            occ[choice:choice + m] += t.occupancy
            count[choice:choice + m] += 1
            offsets[t.tube_id] = choice * stride
            cur_len = max(cur_len, choice * stride + t.length)
        used = max((offsets[t.tube_id] + t.length for t in tubes if t.tube_id in offsets), default=0)
        return offsets, used, dropped

    # ───────────────────────────── 4. composite ─────────────────────────────

    def _composite(self, background, tubes, offsets, index, syn_len, spool_path, out_path, fps, w, h) -> Optional[str]:
        if syn_len <= 0:
            # No activity: honest 1-second clip of the background so the file exists.
            syn_len = int(round(fps))
        starts = {t.tube_id: t.start for t in tubes}
        rows = [i for i in range(index.shape[0]) if int(index[i, 0]) in offsets]
        if rows:
            sel = index[rows]
            tid = sel[:, 0]
            syn = np.array([offsets[int(a)] for a in tid], dtype=np.int64) + sel[:, 1] - np.array(
                [starts[int(a)] for a in tid], dtype=np.int64)
            order = np.lexsort((sel[:, 1], syn))
            sel, syn = sel[order], syn[order]
        else:
            sel, syn = np.zeros((0, 9), np.int64), np.zeros(0, np.int64)

        writer, codec = _open_writer(out_path, fps, (w, h))
        font = cv2.FONT_HERSHEY_SIMPLEX
        fscale = max(0.35, w / 1400.0)
        palette = [(80, 200, 255), (120, 255, 120), (255, 160, 80), (200, 120, 255), (90, 220, 220), (255, 230, 90)]
        ptr = 0
        try:
            with open(spool_path, "rb") as spool:
                for s in range(syn_len):
                    canvas = background.copy()
                    labels = []
                    while ptr < len(syn) and syn[ptr] == s:
                        tube_id, frame_no, x, y, bw, bh, off, jl, ml = (int(v) for v in sel[ptr])
                        ptr += 1
                        spool.seek(off)
                        blob = spool.read(jl + ml)
                        crop = cv2.imdecode(np.frombuffer(blob[:jl], np.uint8), cv2.IMREAD_COLOR)
                        mask = cv2.imdecode(np.frombuffer(blob[jl:], np.uint8), cv2.IMREAD_GRAYSCALE)
                        if crop is None or mask is None or crop.shape[:2] != mask.shape[:2]:
                            continue
                        alpha = cv2.GaussianBlur(mask, (7, 7), 0).astype(np.float32)[..., None] / 255.0
                        roi = canvas[y:y + bh, x:x + bw]
                        if roi.shape[:2] != crop.shape[:2]:
                            continue
                        canvas[y:y + bh, x:x + bw] = (crop * alpha + roi * (1.0 - alpha)).astype(np.uint8)
                        labels.append((tube_id, frame_no, x, y))
                    for tube_id, frame_no, x, y in labels:
                        secs = frame_no / fps
                        txt = f"{int(secs // 3600):02d}:{int(secs % 3600 // 60):02d}:{secs % 60:04.1f}"
                        colour = palette[tube_id % len(palette)]
                        ty = max(12, y - 4)
                        cv2.putText(canvas, txt, (x, ty), font, fscale, (0, 0, 0), 3, cv2.LINE_AA)
                        cv2.putText(canvas, txt, (x, ty), font, fscale, colour, 1, cv2.LINE_AA)
                    writer.write(canvas)
                    if s % 100 == 0:
                        self._progress("composite", 0.8 + 0.2 * s / max(1, syn_len))
        finally:
            writer.release()
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Encoder produced no output")
        return codec
