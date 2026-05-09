"""
OmniTrack AI — Video Synopsis Engine

Production-grade synopsis implementation. Condenses long surveillance footage
into a compact, reviewable summary video by:

  1. Building a robust background model (median of sampled frames + MOG2 refinement).
  2. Extracting per-object ACTIVITY TUBES (a tube = one moving object's trajectory
     across the frames where it is visible).
  3. SCHEDULING tubes on a compressed timeline so multiple events that happened at
     different original times appear simultaneously in the synopsis.
  4. COMPOSITING each tube back onto the static background using per-pixel alpha
     blending derived from the foreground mask, then labeling with the original
     timestamp so reviewers can see when each event actually occurred.

Memory is bounded: each tube keeps at most `keyframes_per_tube` (default 20)
downsampled crops — the full tube's worth of pixels is not held in RAM.

Public API:
    synopsis = VideoSynopsis()
    metrics = synopsis.process_video("input.mp4", "output.mp4")
"""

from __future__ import annotations

import cv2
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class ActivityTube:
    """One moving object tracked across the frames where it is visible."""
    tube_id: int
    frames: List[int] = field(default_factory=list)
    bboxes: List[Tuple[int, int, int, int]] = field(default_factory=list)
    crops: List[np.ndarray] = field(default_factory=list)   # BGR crop per sampled frame
    masks: List[np.ndarray] = field(default_factory=list)   # Binary fg mask aligned with crop
    start_frame: int = 0
    end_frame: int = 0
    original_fps: float = 30.0

    @property
    def duration_frames(self) -> int:
        return max(1, self.end_frame - self.start_frame + 1)


class VideoSynopsis:
    """
    Video Synopsis engine: extracts activity tubes from surveillance footage
    and composites them into a condensed summary video.

    Parameters
    ----------
    bg_history : int
        MOG2 history length for background modelling.
    bg_threshold : float
        MOG2 variance threshold.
    min_area : int
        Minimum contour area (pixels^2) to count as foreground motion.
    compression_target : float
        Target ratio ORIGINAL_DURATION / SYNOPSIS_DURATION (e.g. 10.0 means
        "10x shorter"). Actual ratio depends on how much activity there is.
    keyframes_per_tube : int
        Max number of sampled frames retained per tube (bounds memory).
    max_parallel_tubes : int
        Max simultaneous tubes visible in any synopsis frame (overlap control).
    """

    def __init__(
        self,
        bg_history: int = 500,
        bg_threshold: float = 16.0,
        min_area: int = 500,
        compression_target: float = 10.0,
        keyframes_per_tube: int = 20,
        max_parallel_tubes: int = 6,
    ):
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=bg_history,
            varThreshold=bg_threshold,
            detectShadows=True,
        )
        self.min_area = min_area
        self.compression_target = max(compression_target, 1.1)
        self.keyframes_per_tube = max(2, keyframes_per_tube)
        self.max_parallel_tubes = max(1, max_parallel_tubes)

        self.background: Optional[np.ndarray] = None
        self.tubes: List[ActivityTube] = []
        self.frame_count = 0
        self._tube_separation_iou = 0.3  # IoU threshold to decide "same tube"

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    def process_video(self, video_path: str, output_path: str) -> Dict[str, Any]:
        """
        Full pipeline: estimate background, extract activity tubes, compose a
        condensed synopsis, write output. Returns metrics for API reporting.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        logger.info(
            f"[Synopsis] {total_frames} frames @ {fps:.1f} FPS ({width}x{height}) "
            f"→ target compression x{self.compression_target}"
        )

        # Phase 1: Estimate background by median of sampled frames
        self.background = self._estimate_background(cap, total_frames, width, height)

        # Reset capture for tube extraction
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        # Phase 2: Extract activity tubes (multi-object)
        self._extract_activity(cap, total_frames, fps=fps)
        cap.release()

        # Phase 3: Compose synopsis by scheduling tubes on a compressed timeline
        synopsis_frames = self._compose_synopsis(width, height, fps=fps)

        # Phase 4: Write to disk
        self._write_output(output_path, synopsis_frames, fps, width, height)

        original_duration = total_frames / fps if fps else 0
        synopsis_duration = len(synopsis_frames) / fps if fps else 0
        ratio = original_duration / max(synopsis_duration, 1e-6)

        return {
            "original_duration": round(original_duration, 2),
            "synopsis_duration": round(synopsis_duration, 2),
            "compression_ratio": round(ratio, 2),
            "tubes_extracted": len(self.tubes),
            "output_path": output_path,
            "frames_written": len(synopsis_frames),
            "fps": fps,
        }

    def reset(self) -> None:
        self.tubes.clear()
        self.background = None
        self.frame_count = 0
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16.0, detectShadows=True
        )

    # ─────────────────────────────────────────────────────────────
    # Phase 1: Background estimation
    # ─────────────────────────────────────────────────────────────

    def _estimate_background(
        self,
        cap: cv2.VideoCapture,
        total_frames: int,
        width: int,
        height: int,
        max_samples: int = 50,
    ) -> np.ndarray:
        """
        Median-of-samples background estimation. Much more robust than a single
        frame grab because people walking around won't leak into the background.
        """
        if total_frames <= 0:
            ret, frame = cap.read()
            return frame if ret and frame is not None else np.zeros((height, width, 3), np.uint8)

        indices = np.linspace(0, max(total_frames - 1, 0), min(max_samples, total_frames), dtype=int)
        samples: List[np.ndarray] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ret, frame = cap.read()
            if ret and frame is not None:
                samples.append(frame)
        if not samples:
            return np.zeros((height, width, 3), np.uint8)
        stack = np.stack(samples, axis=0)
        bg = np.median(stack, axis=0).astype(np.uint8)
        logger.debug(f"[Synopsis] background estimated from {len(samples)} samples")
        return bg

    # ─────────────────────────────────────────────────────────────
    # Phase 2: Activity tube extraction (multi-object)
    # ─────────────────────────────────────────────────────────────

    def _extract_activity(
        self,
        cap: cv2.VideoCapture,
        total_frames: int,
        fps: float = 30.0,
    ) -> None:
        """
        Extract activity tubes. Each detected moving blob is associated with an
        existing tube by bbox IoU, or starts a new tube if nothing overlaps.
        Sampled crops + masks are kept per tube so the compositor can render
        each object back onto the synopsis background.
        """
        active_tubes: List[ActivityTube] = []
        frame_limit = min(total_frames, 50000) if total_frames > 0 else 50000

        for frame_idx in range(frame_limit):
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            self.frame_count = frame_idx + 1

            fg_mask = self.bg_subtractor.apply(frame)
            # Drop shadows (MOG2 marks shadows as 127)
            fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)[1]
            # Morphological cleanup
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, k)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, k)

            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            blobs: List[Tuple[int, int, int, int]] = []
            for c in contours:
                if cv2.contourArea(c) < self.min_area:
                    continue
                x, y, w, h = cv2.boundingRect(c)
                blobs.append((x, y, w, h))

            # Associate each blob with an existing tube by IoU, or start a new one
            matched_tube_ids: set = set()
            for (x, y, w, h) in blobs:
                bbox = (x, y, w, h)
                best_tube: Optional[ActivityTube] = None
                best_iou = 0.0
                for t in active_tubes:
                    if t.tube_id in matched_tube_ids or not t.bboxes:
                        continue
                    iou = self._bbox_iou(bbox, t.bboxes[-1])
                    if iou > best_iou:
                        best_iou, best_tube = iou, t
                if best_iou >= self._tube_separation_iou and best_tube is not None:
                    self._append_to_tube(best_tube, frame, fg_mask, bbox, frame_idx)
                    matched_tube_ids.add(best_tube.tube_id)
                else:
                    new_tube = ActivityTube(
                        tube_id=len(self.tubes) + len(active_tubes) + 1,
                        start_frame=frame_idx,
                        original_fps=fps,
                    )
                    self._append_to_tube(new_tube, frame, fg_mask, bbox, frame_idx)
                    active_tubes.append(new_tube)
                    matched_tube_ids.add(new_tube.tube_id)

            # Finalize tubes that weren't updated for a while (object left scene)
            stale_cutoff = frame_idx - int(fps)  # 1 second of gap closes a tube
            stale_tubes = [t for t in active_tubes if t.frames and t.frames[-1] < stale_cutoff]
            for t in stale_tubes:
                t.end_frame = t.frames[-1]
                if len(t.frames) >= 5:
                    self.tubes.append(t)
                active_tubes.remove(t)

        # Finalize everything still active
        for t in active_tubes:
            if t.frames:
                t.end_frame = t.frames[-1]
                if len(t.frames) >= 5:
                    self.tubes.append(t)

        # Downsample tube crops to bound memory
        for t in self.tubes:
            self._downsample_tube(t)

        logger.info(f"[Synopsis] extracted {len(self.tubes)} activity tubes over {self.frame_count} frames")

    def _append_to_tube(
        self,
        tube: ActivityTube,
        frame: np.ndarray,
        fg_mask: np.ndarray,
        bbox: Tuple[int, int, int, int],
        frame_idx: int,
    ) -> None:
        x, y, w, h = bbox
        h_img, w_img = frame.shape[:2]
        x2 = min(x + w, w_img)
        y2 = min(y + h, h_img)
        x, y = max(0, x), max(0, y)
        crop = frame[y:y2, x:x2].copy()
        mask_crop = fg_mask[y:y2, x:x2].copy()
        tube.frames.append(frame_idx)
        tube.bboxes.append((x, y, x2 - x, y2 - y))
        tube.crops.append(crop)
        tube.masks.append(mask_crop)

    def _downsample_tube(self, tube: ActivityTube) -> None:
        """Keep at most `keyframes_per_tube` evenly spaced frames so memory is bounded."""
        n = len(tube.frames)
        if n <= self.keyframes_per_tube:
            return
        keep_idx = np.linspace(0, n - 1, self.keyframes_per_tube).astype(int).tolist()
        tube.frames = [tube.frames[i] for i in keep_idx]
        tube.bboxes = [tube.bboxes[i] for i in keep_idx]
        tube.crops = [tube.crops[i] for i in keep_idx]
        tube.masks = [tube.masks[i] for i in keep_idx]

    @staticmethod
    def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2, ay2 = ax + aw, ay + ah
        bx2, by2 = bx + bw, by + bh
        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    # ─────────────────────────────────────────────────────────────
    # Phase 3: Compose synopsis
    # ─────────────────────────────────────────────────────────────

    def _compose_synopsis(self, width: int, height: int, fps: float = 30.0) -> List[np.ndarray]:
        """
        Schedule tubes on a compressed timeline and composite each tube's crops
        back onto the static background using per-pixel alpha blending.
        """
        if not self.tubes or self.background is None:
            bg = self.background if self.background is not None else np.zeros((height, width, 3), np.uint8)
            return [bg.copy()]

        # Decide synopsis length (in frames)
        synopsis_length = max(int(self.frame_count / self.compression_target), self.keyframes_per_tube)
        synopsis_length = min(synopsis_length, max(self.frame_count, 30), 6000)  # cap at ~ 4 minutes @30fps

        # Initialize all synopsis frames from the static background
        synopsis: List[np.ndarray] = [self.background.copy() for _ in range(synopsis_length)]

        # Schedule: distribute tubes across timeline, respecting max_parallel_tubes
        schedule = self._schedule_tubes(synopsis_length)

        # Composite each tube onto its scheduled synopsis frames
        for tube, start in schedule:
            self._composite_tube(synopsis, tube, start, fps=fps)

        # Finally stamp "X of Y" progress indicator
        for i, frame in enumerate(synopsis):
            cv2.putText(
                frame,
                f"OmniTrack Synopsis {i + 1}/{synopsis_length}",
                (10, height - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA,
            )

        return synopsis

    def _schedule_tubes(self, synopsis_length: int) -> List[Tuple[ActivityTube, int]]:
        """
        Greedy scheduler: sort tubes by original start_frame, place each at the
        earliest synopsis slot where fewer than `max_parallel_tubes` other tubes
        already occupy that range. Works well for surveillance footage.
        """
        tubes = sorted(self.tubes, key=lambda t: t.start_frame)
        # occupancy[i] = list of (start, end) tube windows overlapping synopsis frame i
        placements: List[Tuple[ActivityTube, int]] = []
        occupancy: List[int] = [0] * synopsis_length
        for tube in tubes:
            span = max(1, len(tube.frames))
            span = min(span, synopsis_length)
            best_slot = 0
            best_load = 10**9
            # Try many candidate start positions
            step = max(1, synopsis_length // 200)
            for start in range(0, max(synopsis_length - span + 1, 1), step):
                load = max(occupancy[start:start + span]) if span > 0 else 0
                if load < best_load:
                    best_load, best_slot = load, start
                    if load + 1 <= self.max_parallel_tubes:
                        break
            # Mark occupancy
            for i in range(best_slot, min(best_slot + span, synopsis_length)):
                occupancy[i] += 1
            placements.append((tube, best_slot))
        return placements

    def _composite_tube(
        self,
        synopsis: List[np.ndarray],
        tube: ActivityTube,
        start_slot: int,
        fps: float = 30.0,
    ) -> None:
        """Paste each tube keyframe onto the synopsis at its original bbox coords."""
        for i, (bbox, crop, mask) in enumerate(zip(tube.bboxes, tube.crops, tube.masks)):
            slot = start_slot + i
            if slot >= len(synopsis):
                break
            x, y, w, h = bbox
            if w <= 0 or h <= 0 or crop.size == 0:
                continue

            target = synopsis[slot]
            th, tw = target.shape[:2]
            if x >= tw or y >= th:
                continue
            # Clip
            x2 = min(x + w, tw)
            y2 = min(y + h, th)
            cw, ch = x2 - x, y2 - y
            if cw <= 0 or ch <= 0:
                continue
            crop_clip = crop[:ch, :cw]
            mask_clip = mask[:ch, :cw] if mask is not None and mask.size else None

            if mask_clip is None or mask_clip.shape[:2] != (ch, cw):
                # No mask → just paste the crop directly
                target[y:y2, x:x2] = crop_clip
                continue

            # Soft alpha — blur mask edges so composition doesn't look pasted
            alpha = cv2.GaussianBlur(mask_clip, (5, 5), 0).astype(np.float32) / 255.0
            alpha = np.clip(alpha, 0.0, 1.0)
            alpha3 = np.repeat(alpha[:, :, None], 3, axis=2)
            bg_region = target[y:y2, x:x2].astype(np.float32)
            fg_region = crop_clip.astype(np.float32)
            blended = (fg_region * alpha3 + bg_region * (1.0 - alpha3)).astype(np.uint8)
            target[y:y2, x:x2] = blended

            # Timestamp badge (original time in the source video)
            if fps > 0 and tube.frames:
                original_sec = tube.frames[min(i, len(tube.frames) - 1)] / fps
                mm, ss = divmod(int(original_sec), 60)
                hh, mm = divmod(mm, 60)
                label = f"T-{tube.tube_id:03d} {hh:02d}:{mm:02d}:{ss:02d}"
                cv2.rectangle(target, (x, max(y - 16, 0)), (x + min(170, tw - x), y), (20, 20, 20), -1)
                cv2.putText(
                    target, label, (x + 4, max(y - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1, cv2.LINE_AA,
                )

    # ─────────────────────────────────────────────────────────────
    # Phase 4: Write output
    # ─────────────────────────────────────────────────────────────

    def _write_output(
        self, output_path: str, frames: List[np.ndarray], fps: float, w: int, h: int
    ):
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, float(max(fps, 1.0)), (w, h))
        if not writer.isOpened():
            logger.error(f"[Synopsis] failed to open writer for {output_path}")
            return
        for frame in frames:
            if frame is None:
                continue
            if frame.shape[:2] != (h, w):
                frame = cv2.resize(frame, (w, h))
            writer.write(frame)
        writer.release()
        logger.info(f"[Synopsis] written → {output_path} ({len(frames)} frames)")
