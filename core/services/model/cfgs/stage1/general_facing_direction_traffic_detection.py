from collections import defaultdict, deque
from typing import Any, cast

import numpy as np
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
from constants.models import KEYPOINT_NAMES, SKELETON_CONNECTIONS
from services.model.cfgs.ibase_stage import BaseStage


class GeneralFacingDirectionTrafficDetection(BaseStage):
    """
    Stage for classifying traffic based on facing direction and movement direction.

    Classifications:
    - "ignoring": facing direction is NOT down AND movement is left/right/up (not down)
    - "slightly_ignoring": facing direction is down for few frames BUT movement is left/right/up (not down)
    - "good_traffic": facing direction is down AND movement direction is also down
    """

    # Classification names
    CLASS_IGNORING = "ignoring"
    CLASS_SLIGHTLY_IGNORING = "slightly_ignoring"
    CLASS_GOOD_TRAFFIC = "good_traffic"

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        movement_history_size: int = 10,
        facing_down_threshold: float = 0.7,
        movement_down_threshold: float = 0.7,
        min_frames_for_slightly_ignoring: int = 3,
    ):
        super().__init__(model_id)
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.model = YOLO(model_path)
        try:
            self.model.to(self.device)
        except AttributeError:
            pass

        if isinstance(tag, str):
            tag = [tag]
        self.tag = [label.lower() for label in tag]

        self.movement_history_size = movement_history_size
        self.facing_down_threshold = facing_down_threshold
        self.movement_down_threshold = movement_down_threshold
        self.min_frames_for_slightly_ignoring = min_frames_for_slightly_ignoring

        # Track movement history per track_id: deque of (center_x, center_y) tuples
        self.movement_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=movement_history_size)
        )
        # Track facing direction history per track_id: deque of facing_is_down booleans
        self.facing_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=movement_history_size)
        )

        # Keypoint indices
        self._left_shoulder_idx = self._get_keypoint_index("left_shoulder")
        self._right_shoulder_idx = self._get_keypoint_index("right_shoulder")

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Stage 0: Track objects with pose estimation and classify based on facing and movement direction.
        Combines tracking and classification in a single stage.
        """
        # First, do tracking (like GeneralPETrackerStage1)
        results = self.model.track(
            image, device=self.device, verbose=False, tracker="bytetrack.yaml", persist=True
        )[0]
        detections = []

        has_keypoints = hasattr(results, "keypoints") and results.keypoints is not None
        if results.boxes is not None:
            for i in range(len(results.boxes)):
                box = results.boxes[i]
                cls = results.names[int(box.cls)]
                if cls.lower() in self.tag or "all" in self.tag:
                    detection: dict[str, Any] = {
                        BBOX: list(map(int, box.xyxy[0].tolist())),
                        CONFIDENCE: float(box.conf),
                        CLASS_ID: int(box.cls),
                        CLASS_NAME: cls.lower(),
                        MODEL_ID: self.model_id,
                        TRACK_ID: int(box.id) if box.id is not None else None,
                    }
                    self._ensure_guid(detection)

                    # Add keypoints if available (for pose models)
                    if has_keypoints and results.keypoints is not None:
                        kp_data = results.keypoints[i].data
                        kps_arr = (
                            kp_data.cpu().numpy()
                            if isinstance(kp_data, torch.Tensor)
                            else np.asarray(kp_data)
                        )
                        kps = kps_arr.squeeze().tolist()
                        # Ensure shape is [[x,y,conf], ...]
                        if isinstance(kps[0][0], list):  # nested case
                            kps = kps[0]
                        detection[KEYPOINTS] = kps
                        detection[SKELETON] = SKELETON_CONNECTIONS

                    detections.append(detection)

        # Now classify each detection based on facing and movement direction
        for detection in detections:
            # Get required data
            track_id = detection.get(TRACK_ID)
            keypoints = detection.get(KEYPOINTS, [])
            bbox = detection.get(BBOX)

            if track_id is None:
                continue

            if not keypoints or not bbox:
                continue

            # Calculate bbox center for movement tracking
            center_x = (bbox[0] + bbox[2]) / 2.0
            center_y = (bbox[1] + bbox[3]) / 2.0

            # Update movement history
            self.movement_history[track_id].append((center_x, center_y))

            # Calculate facing direction
            facing_is_down = self._calculate_facing_direction(keypoints)

            # Update facing history
            self.facing_history[track_id].append(facing_is_down)

            # Classify based on facing and movement
            classification = self._classify_traffic(track_id, facing_is_down)

            # Update detection with classification
            detection[CLASS_NAME] = classification

        return detections

    def _get_keypoint_index(self, name: str) -> int | None:
        """Get index of keypoint by name."""
        if name in KEYPOINT_NAMES:
            return int(KEYPOINT_NAMES.index(name))
        return None

    def _calculate_facing_direction(self, keypoints: list[list[float]]) -> bool:
        """
        Calculate if person is facing down based on shoulder keypoints.

        Returns:
            True if facing down, False otherwise
        """
        if (
            self._left_shoulder_idx is None
            or self._right_shoulder_idx is None
            or len(keypoints) <= max(self._left_shoulder_idx, self._right_shoulder_idx)
        ):
            return False

        kp_array = np.asarray(keypoints)
        confidence_threshold = 0.5

        left_shoulder = self._extract_point(kp_array, self._left_shoulder_idx, confidence_threshold)
        right_shoulder = self._extract_point(
            kp_array, self._right_shoulder_idx, confidence_threshold
        )

        if left_shoulder is None or right_shoulder is None:
            return False

        # Calculate direction vector from right to left shoulder
        direction_vector = left_shoulder - right_shoulder
        norm = np.linalg.norm(direction_vector)
        if norm == 0:
            return False

        direction_vector = direction_vector / norm

        # Rotate 90 degrees to get facing direction (perpendicular to shoulder line)
        facing_vector = self._rotate_vector_90_deg(direction_vector) * -1
        facing_norm = np.linalg.norm(facing_vector)
        if facing_norm == 0:
            return False

        facing_vector = facing_vector / facing_norm

        # Check if facing down (positive Y direction in image coordinates)
        # facing_vector[1] > threshold means facing downward
        is_facing_down = bool(facing_vector[1] > self.facing_down_threshold)

        return is_facing_down

    def _calculate_movement_direction(self, track_id: int) -> str | None:
        """
        Calculate movement direction based on bbox center history.

        Returns:
            "down", "up", "left", "right", or None if insufficient history
        """
        history = self.movement_history[track_id]
        if len(history) < 2:
            return None

        # Get first and last positions
        first_pos = np.array(history[0])
        last_pos = np.array(history[-1])

        # Calculate movement vector
        movement_vector = last_pos - first_pos
        movement_norm = np.linalg.norm(movement_vector)

        if movement_norm < 1.0:  # Too small movement
            return None

        movement_vector = movement_vector / movement_norm

        # Determine primary direction
        abs_x = abs(movement_vector[0])
        abs_y = abs(movement_vector[1])

        # Check if movement is primarily down (positive Y in image coordinates)
        if movement_vector[1] > self.movement_down_threshold:
            return "down"
        elif movement_vector[1] < -self.movement_down_threshold:
            return "up"
        elif abs_x > abs_y:
            if movement_vector[0] > 0:
                return "right"
            else:
                return "left"
        else:
            # If movement is ambiguous, check Y component
            if movement_vector[1] > 0:
                return "down"
            else:
                return "up"

    def _classify_traffic(self, track_id: int, current_facing_is_down: bool) -> str:
        """
        Classify traffic based on facing direction history and movement direction.

        Returns:
            Classification: "ignoring", "slightly_ignoring", or "good_traffic"
        """
        # Get movement direction
        movement_dir = self._calculate_movement_direction(track_id)
        if movement_dir is None:
            # Insufficient movement data, default to ignoring
            return self.CLASS_IGNORING

        # Get facing direction history
        facing_history = list(self.facing_history[track_id])
        if not facing_history:
            return self.CLASS_IGNORING

        # Count how many frames person was facing down
        frames_facing_down = sum(1 for f in facing_history if f)

        # Check if movement is down
        is_moving_down = movement_dir == "down"

        # Classification logic:
        # 1. "good_traffic": facing down AND moving down
        if current_facing_is_down and is_moving_down:
            return self.CLASS_GOOD_TRAFFIC

        # 2. "slightly_ignoring": facing down for few frames BUT moving left/right/up
        if frames_facing_down >= self.min_frames_for_slightly_ignoring and not is_moving_down:
            return self.CLASS_SLIGHTLY_IGNORING

        # 3. "ignoring": facing NOT down AND moving left/right/up (not down)
        if not current_facing_is_down and not is_moving_down:
            return self.CLASS_IGNORING

        # 4. "ignoring": facing NOT down BUT moving down (person moving down but not facing down)
        if not current_facing_is_down and is_moving_down:
            return self.CLASS_IGNORING

        # Default case: if facing down but not moving down (and not enough history for slightly_ignoring)
        # This handles edge cases where person is facing down but movement is ambiguous
        return self.CLASS_IGNORING

    @staticmethod
    def _extract_point(
        keypoints: np.ndarray, index: int, confidence_threshold: float
    ) -> np.ndarray | None:
        """Extract keypoint coordinates if confidence is above threshold."""
        if index >= len(keypoints):
            return None
        kp = keypoints[index]
        if len(kp) < 3 or kp[2] < confidence_threshold:
            return None
        arr = np.array([float(kp[0]), float(kp[1])], dtype=float)
        return cast(np.ndarray, arr)

    @staticmethod
    def _rotate_vector_90_deg(vector: np.ndarray) -> np.ndarray:
        """Rotate 2D vector 90 degrees counterclockwise."""
        arr = np.array([float(vector[1]), float(-vector[0])], dtype=float)
        return cast(np.ndarray, arr)

    @property
    def names(self) -> dict[int, str]:
        return cast(dict[int, str], BaseStage._ensure_name_mapping(None))
