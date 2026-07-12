from collections.abc import Mapping
from typing import Any

import numpy as np

from constants.detections_constant import (
    ACTIVITIES,
    CLASS_NAME,
    CONFIRMING,
    DEFAULT_ACTIVITY_NAME,
    DEFAULT_ACTIVITY_THRESHOLD,
    KEYPOINTS,
    LOG_UNKNOWN_ACTIVITY,
)
from services.loaders.idata_loader import IDataLoader
from services.managers.activity_matching_manager import ActivityMatching


class PoseDataLoaderWithActivity(IDataLoader):
    """Enhanced PoseDataLoader with flexible activity recognition."""

    def __init__(
        self,
        enable_activity_matching: bool = True,
        activities_to_detect: list[str] | None = None,
    ):
        """
        Initialize PoseDataLoader with activity recognition.

        Args:
            enable_activity_matching: Whether to perform activity recognition
            activities_to_detect: Specific activities to detect (None or empty list = detect all)
        """
        self.enable_activity_recognition = enable_activity_matching
        self.activities_to_detect = [] if activities_to_detect is None else activities_to_detect

    def load(self, results: Any, frame: np.ndarray) -> list[dict[str, Any]]:
        detections: list[dict[str, Any]] = []

        for _i, result in enumerate(results):
            detection: dict[str, Any] = {**result}

            # Pose keypoints extraction
            if detection[KEYPOINTS] is not None:
                # Activity recognition
                if self.enable_activity_recognition and frame is not None:
                    keypoints_array = np.array(detection[KEYPOINTS])

                    # Detect specific or all activities
                    activities = (
                        self._detect_specific_activities(
                            keypoints_array, frame, self.activities_to_detect
                        )
                        if self.activities_to_detect
                        else ActivityMatching.detect_all_activities(keypoints_array, frame)
                    )

                    detection[ACTIVITIES] = activities
                    detection[CLASS_NAME] = self._get_primary_activity(activities)

            detections.append(detection)

        return detections

    def _detect_specific_activities(
        self, keypoints: np.ndarray, frame: np.ndarray, activity_names: list[str]
    ) -> dict[str, float | bool]:
        """Detect only specified activities."""
        activities: dict[str, float | bool] = {}
        for activity_name in activity_names:
            try:
                activities[activity_name] = ActivityMatching.detect_activity(
                    activity_name, keypoints, frame
                )
            except ValueError:
                print(f"{LOG_UNKNOWN_ACTIVITY}: {activity_name}")
                activities[activity_name] = False
        return activities

    def _get_primary_activity(
        self,
        activities: Mapping[str, float | bool],
        threshold: float = DEFAULT_ACTIVITY_THRESHOLD,
    ) -> str:
        """Get the primary detected activity based on confidence scores."""
        if not activities:
            return DEFAULT_ACTIVITY_NAME

        # Sort activities by confidence
        sorted_activities = sorted(activities.items(), key=lambda x: float(x[1]), reverse=True)
        top_activity, top_score = sorted_activities[0]

        # Check confidence threshold
        if float(top_score) >= threshold:
            return top_activity

        # Fallback to first positive detection or confirming message
        detected_acts = [act for act, val in activities.items() if val]
        return detected_acts[0] if detected_acts else CONFIRMING
