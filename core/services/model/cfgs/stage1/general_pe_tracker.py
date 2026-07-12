from typing import Any

import torch
from ultralytics import YOLO

from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    KEYPOINTS,
    MODEL_ID,
    SKELETON,
    TRACK_ID,
)
from constants.models import SKELETON_CONNECTIONS
from services.managers.tracker_factory import TrackerFactory
from services.model.cfgs.ibase_stage import BaseStage
from services.trackers.interface.itracker import ITracker


class GeneralPeTrackerStage1(BaseStage):
    """
    Stage 1:
    - Run YOLO detection / pose
    - Run tracking (ByteTrack / other)
    - Attach track_id, keypoints, skeleton
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        tracker_name: str = "bytetrack",
        device: str | None = None,
    ):
        super().__init__(model_id)

        # -------- Device --------
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        # -------- Model --------
        self.model = YOLO(model_path)

        # -------- Tags --------
        if isinstance(tag, str):
            tag = [tag.lower()]
        self.tag = [t.lower() for t in tag]

        # -------- Tracker --------
        self.tracker: ITracker = TrackerFactory.create_tracker(tracker_name)

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Returns:
        [
            {
                bbox: [x1, y1, x2, y2],
                confidence: float,
                class_id: int,
                class_name: str,
                model_id: str,
                track_id: int,
                keypoints: [[x, y, conf], ...],
                skeleton: [...]
            }
        ]
        """

        # ---------- Tracking ----------
        tracked_objects = self.tracker.track(
            frame=image,
            model=self.model,
            device=self.device,
            persist=True,
            conf=0.5,
            model_id=self.model_id,
        )
        detections: list[dict[str, Any]] = []

        # ---------- Build Output ----------
        for obj in tracked_objects:
            cls_name = obj.get(CLASS_NAME, "").lower()

            if "all" not in self.tag and cls_name not in self.tag:
                continue

            detection: dict[str, Any] = {
                BBOX: list(map(int, obj[BBOX])),
                CONFIDENCE: float(obj[CONFIDENCE]),
                CLASS_ID: int(obj[CLASS_ID]),
                CLASS_NAME: cls_name,
                MODEL_ID: self.model_id,
                TRACK_ID: int(obj.get(TRACK_ID, -1)),
            }
            self._ensure_guid(detection)

            # ---------- Keypoints ----------
            if KEYPOINTS in obj:
                detection[KEYPOINTS] = obj[KEYPOINTS]
                detection[SKELETON] = SKELETON_CONNECTIONS

            detections.append(detection)

        return detections

    @property
    def names(self) -> dict[int, str]:
        return BaseStage._ensure_name_mapping(getattr(self.model, "names", None))
