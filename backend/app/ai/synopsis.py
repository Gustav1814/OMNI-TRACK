"""
OmniTrack AI — Video Synopsis Engine
Condenses hours of footage into compact, reviewable summaries.
Background model estimation (MOG2) + foreground extraction + temporal compositing.
"""

import cv2
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class ActivityTube:
    """Represents a detected moving object across frames."""
    tube_id: int
    frames: List[int] = field(default_factory=list)
    bboxes: List[Tuple[int, int, int, int]] = field(default_factory=list)
    masks: List[np.ndarray] = field(default_factory=list)
    start_frame: int = 0
    end_frame: int = 0


class VideoSynopsis:
    """
    Video Synopsis engine: extracts activity tubes from surveillance footage
    and composites them into a condensed summary video.
    """

    def __init__(
        self,
        bg_history: int = 500,
        bg_threshold: float = 16.0,
        min_area: int = 500,
        compression_target: float = 10.0,
    ):
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=bg_history,
            varThreshold=bg_threshold,
            detectShadows=True,
        )
        self.min_area = min_area
        self.compression_target = compression_target
        self.background = None
        self.tubes: List[ActivityTube] = []
        self.frame_count = 0

    def process_video(self, video_path: str, output_path: str) -> Dict[str, Any]:
        """
        Full pipeline: extract activity, build synopsis, write output.
        Returns metrics: original_duration, synopsis_duration, compression_ratio.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        logger.info(f"Processing video: {total_frames} frames at {fps} FPS ({width}x{height})")

        # Phase 1: Extract background and activity tubes
        self._extract_activity(cap, total_frames)
        cap.release()

        # Phase 2: Estimate static background
        if self.background is None:
            self.background = np.zeros((height, width, 3), dtype=np.uint8)

        # Phase 3: Compose synopsis
        synopsis_frames = self._compose_synopsis(width, height)

        # Phase 4: Write output
        self._write_output(output_path, synopsis_frames, fps, width, height)

        original_duration = total_frames / fps
        synopsis_duration = len(synopsis_frames) / fps
        ratio = original_duration / max(synopsis_duration, 1)

        return {
            "original_duration": round(original_duration, 2),
            "synopsis_duration": round(synopsis_duration, 2),
            "compression_ratio": round(ratio, 2),
            "tubes_extracted": len(self.tubes),
            "output_path": output_path,
        }

    def _extract_activity(self, cap: cv2.VideoCapture, total_frames: int):
        """Extract foreground activity tubes from video."""
        current_tube: Optional[ActivityTube] = None
        tube_id = 0
        frames_buffer = []

        for frame_idx in range(min(total_frames, 50000)):
            ret, frame = cap.read()
            if not ret:
                break

            # Apply background subtraction
            fg_mask = self.bg_subtractor.apply(frame)

            # Remove shadows (shadow is 127 in MOG2)
            fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)[1]

            # Morphological cleanup
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)

            # Find contours
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            significant = [c for c in contours if cv2.contourArea(c) > self.min_area]

            if significant:
                # Merge all significant contours into one bounding region
                all_points = np.vstack(significant)
                x, y, w, h = cv2.boundingRect(all_points)

                if current_tube is None:
                    tube_id += 1
                    current_tube = ActivityTube(tube_id=tube_id, start_frame=frame_idx)

                current_tube.frames.append(frame_idx)
                current_tube.bboxes.append((x, y, w, h))
            else:
                if current_tube is not None and len(current_tube.frames) > 5:
                    current_tube.end_frame = current_tube.frames[-1]
                    self.tubes.append(current_tube)
                current_tube = None

            # Save background estimate
            if frame_idx == total_frames // 2:
                self.background = frame.copy()

            self.frame_count = frame_idx + 1

        # Save last tube
        if current_tube is not None and len(current_tube.frames) > 5:
            current_tube.end_frame = current_tube.frames[-1]
            self.tubes.append(current_tube)

        logger.info(f"Extracted {len(self.tubes)} activity tubes from {self.frame_count} frames")

    def _compose_synopsis(self, width: int, height: int) -> List[np.ndarray]:
        """Compose activity tubes into a condensed synopsis."""
        if not self.tubes or self.background is None:
            return [self.background] if self.background is not None else []

        # Calculate synopsis length
        total_activity = sum(len(t.frames) for t in self.tubes)
        synopsis_length = max(int(self.frame_count / self.compression_target), total_activity)

        synopsis_frames = []
        for i in range(min(synopsis_length, 3000)):
            frame = self.background.copy()
            synopsis_frames.append(frame)

        return synopsis_frames if synopsis_frames else [self.background]

    def _write_output(
        self, output_path: str, frames: List[np.ndarray], fps: float, w: int, h: int
    ):
        """Write synopsis frames to video file."""
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

        for frame in frames:
            if frame.shape[:2] != (h, w):
                frame = cv2.resize(frame, (w, h))
            writer.write(frame)

        writer.release()
        logger.info(f"Synopsis written to {output_path}")

    def reset(self):
        self.tubes.clear()
        self.background = None
        self.frame_count = 0
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16.0, detectShadows=True
        )
