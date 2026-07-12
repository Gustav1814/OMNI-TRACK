# services/trackers/base_tracker.py
from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from constants.detections_constant import (
    BBOX,
    CENTRE,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    DETECT_TRACK_ID,
    KEYPOINTS,
    MODEL_ID,
    SKELETON,
)
from services.trackers.interface.itracker import ITracker


# add global_id_association here
class BaseTracker(ITracker, ABC):
    """
    Base class for all trackers providing common functionality.
    Inherit from this class to implement specific tracking algorithms.
    """

    def __init__(self, tracker_name: str):
        """
        Initialize base tracker.

        Args:
            tracker_name: Name/identifier for this tracker
        """
        self.tracker_name = tracker_name

    @abstractmethod
    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Track objects across frames.
        Must be implemented by subclasses.

        Args:
            frame: Input frame
            model: The model object to use for tracking
            device: Device to run tracking on
            persist: Whether to persist tracks across frames
            conf: Confidence threshold
            **kwargs: Additional tracker-specific parameters

        Returns:
            List of detections with tracking information
        """

    def reset(self) -> None:
        """
        Reset tracker state.
        Override in subclass if tracker maintains state.
        """
        pass

    def _process_detection(
        self,
        box: Any,
        result: Any,
        i: int,
        track_id: int | None,
        model_id: str,
        tag: list[str],
    ) -> dict[str, Any] | None:
        """
        Common detection processing logic.
        Converts raw model output to standardized detection format.

        Args:
            box: Bounding box from result
            result: YOLO result object
            i: Index of current detection
            track_id: Track ID if available
            model_id: Model identifier
            tag: List of valid class tags to filter

        Returns:
            Detection dictionary or None if filtered out by tag
        """
        cls = result.names[int(box.cls)]

        # Filter by tag
        if not (cls.lower() in tag or "all" in tag):
            return None

        detection: dict[str, Any] = {
            BBOX: list(map(int, box.xyxy[0].tolist())),
            CONFIDENCE: float(box.conf),
            CLASS_ID: int(box.cls),
            CLASS_NAME: cls.lower(),
            MODEL_ID: model_id,
        }

        # Add track_id if available
        if track_id is not None:
            detection[DETECT_TRACK_ID] = int(track_id)

        # Add keypoints if available (for pose models)
        if hasattr(result, "keypoints") and result.keypoints is not None:
            kps = result.keypoints[i].data.cpu().numpy().squeeze().tolist()
            if isinstance(kps[0][0], list):  # nested case
                kps = kps[0]
            detection[KEYPOINTS] = kps
            from constants.models import SKELETON_CONNECTIONS

            detection[SKELETON] = SKELETON_CONNECTIONS

        # Add center point
        detection[CENTRE] = [
            int((detection[BBOX][0] + detection[BBOX][2]) / 2),
            int((detection[BBOX][1] + detection[BBOX][3]) / 2),
        ]

        return detection

    def _extract_detections_from_result(
        self, result: Any, model_id: str, tag: list[str]
    ) -> list[dict[str, Any]]:
        """
        Extract and format detections from YOLO result.

        Args:
            result: YOLO result object
            model_id: Model identifier
            tag: List of valid class tags

        Returns:
            List of formatted detections
        """
        detections: list[dict[str, Any]] = []

        if result.boxes is None:
            return detections

        track_ids = (
            result.boxes.id.cpu().numpy().astype(int) if result.boxes.id is not None else None
        )

        for i, box in enumerate(result.boxes):
            track_id = int(track_ids[i]) if track_ids is not None and i < len(track_ids) else None

            detection = self._process_detection(
                box=box, result=result, i=i, track_id=track_id, model_id=model_id, tag=tag
            )

            if detection is not None:
                detections.append(detection)

        return detections
