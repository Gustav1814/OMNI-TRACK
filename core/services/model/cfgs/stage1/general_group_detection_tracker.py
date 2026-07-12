import hashlib
from typing import Any

import numpy as np
import torch
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from ultralytics import YOLO

from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    GROUP_ID,
    MODEL_ID,
    TRACK_ID,
)
from services.managers.tracker_factory import TrackerFactory
from services.model.cfgs.ibase_stage import BaseStage
from services.trackers.interface.itracker import ITracker


class GeneralGroupTrackerStage1(BaseStage):
    """
    Track people in groups based on horizontal overlap and vertical alignment.
    Optimized for production with vectorized operations and deterministic group IDs.
    Uses pluggable tracker implementations (ByteTrack, BoTSORT, etc.)
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        tracker_name: str = "bytetrack",
    ):
        super().__init__(model_id)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = YOLO(model_path)
        try:
            self.model.to(self.device)
        except AttributeError:
            pass

        self.tag = self._normalize_tags(tag)
        self.names_map = BaseStage._ensure_name_mapping(getattr(self.model, "names", None))
        self.y_alignment_threshold: float = 0.2
        self.horizontal_overlap_threshold: int = 1

        # Initialize tracker using factory
        self.tracker: ITracker = TrackerFactory.create_tracker(tracker_name)

    # -----------------------
    # Main inference
    # -----------------------
    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Track groups of people across frames using configured tracker.

        Process:
        1. Normalize tag to list of lowercase class names
        2. Run model predictions to get raw detections
        3. Build class ids list from filtered classes
        4. Call tracker.track() with class filter
        5. Extract tracked detections and assign group IDs
        """
        # Normalize tag to list of lowercase class names
        tag_list = self.tag

        # Step 1: Run model predictions to get raw detections
        try:
            pred_results = self.model.predict(image, device=self.device, verbose=False, conf=0.5)
        except Exception as e:
            print(f"Prediction failed: {e}")
            return []

        if not pred_results:
            return []

        pred_result = pred_results[0]

        # Step 2: Build list of class ids that match the requested tag
        classes_to_track = self._get_classes_to_track(pred_result, tag_list)

        # Step 3: Call tracker.track() from tracker instance
        try:
            tracked_detections = self.tracker.track(
                frame=image,
                model=self.model,
                device=self.device,
                persist=True,
                conf=0.5,
                model_id=self.model_id,
                tag=tag_list,
                classes=classes_to_track,
            )
        except Exception as e:
            print(f"Tracking failed: {e}")
            return []

        if not tracked_detections:
            return []

        # Step 4: Process tracked detections and assign group IDs
        detections = tracked_detections
        for det in detections:
            self._ensure_guid(det)

        # Aggregate group detections
        det_boxes = []
        det_confs = []
        det_tracks = []
        for det in detections:
            bbox = det.get(BBOX, [])
            if bbox:
                det_boxes.append(np.array(bbox))
                det_confs.append(det.get(CONFIDENCE, 0.0))
                det_tracks.append(det.get(TRACK_ID))

        if not detections:
            return []

        # Assign group IDs
        group_ids, group_members = self._assign_group_ids(detections)
        for det, gid in zip(detections, group_ids, strict=True):
            det[GROUP_ID] = gid or ""

        group_detections = self._build_group_detections(
            group_members, det_boxes, det_confs, det_tracks
        )

        detections.extend(group_detections)
        return detections

    @staticmethod
    def _get_classes_to_track(pred_result: Any, tag_list: list[str]) -> list[int] | None:
        if "all" in tag_list or not pred_result.names:
            return None
        classes = [
            class_id
            for class_id, class_name in pred_result.names.items()
            if str(class_name).lower() in tag_list
        ]
        return classes if classes else None

    def _build_group_detections(
        self,
        group_members: dict[str, list[int]],
        det_boxes: list[np.ndarray],
        det_confs: list[Any],
        det_tracks: list[Any],
    ) -> list[dict[str, Any]]:
        group_detections: list[dict[str, Any]] = []
        for group_id, member_indices in group_members.items():
            if len(member_indices) < 2:
                continue

            member_boxes = np.array([det_boxes[i] for i in member_indices])
            member_confs = [
                det_confs[i] for i in member_indices if isinstance(det_confs[i], int | float)
            ]
            member_track_ids = [
                det_tracks[i] for i in member_indices if isinstance(det_tracks[i], int)
            ]

            if not member_track_ids or len(member_boxes) < 2:
                continue

            x1, y1 = member_boxes[:, 0].min(), member_boxes[:, 1].min()
            x2, y2 = member_boxes[:, 2].max(), member_boxes[:, 3].max()
            avg_conf = float(np.mean(member_confs)) if member_confs else 0.0

            # Deterministic group track ID
            sorted_ids_str = "-".join(map(str, sorted(member_track_ids)))
            group_track_id = int(hashlib.sha1(sorted_ids_str.encode()).hexdigest(), 16) % 10_000_000

            group_detection = {
                BBOX: [int(x1), int(y1), int(x2), int(y2)],
                CONFIDENCE: avg_conf,
                CLASS_ID: 1,
                CLASS_NAME: "group",
                MODEL_ID: self.model_id,
                TRACK_ID: group_track_id,
                GROUP_ID: group_id,
            }
            self._ensure_guid(group_detection)
            group_detections.append(group_detection)
        return group_detections

    # -----------------------
    # Helper properties
    # -----------------------
    @property
    def names(self) -> dict[int, str]:
        return self.names_map

    @staticmethod
    def _normalize_tags(tag: list[str] | str) -> list[str]:
        if isinstance(tag, str):
            tag = [tag]
        return [t.lower() for t in tag]

    @staticmethod
    def _compute_height(box: list[int]) -> int:
        return max(1, box[3] - box[1])

    @staticmethod
    def _horizontal_overlap(box_a: list[int], box_b: list[int]) -> int:
        ax1, _, ax2, _ = box_a
        bx1, _, bx2, _ = box_b
        if ax2 <= bx1 or bx2 <= ax1:
            return 0
        return min(ax2, bx2) - max(ax1, bx1)

    def _same_horizontal_band(self, box_a: list[int], box_b: list[int]) -> bool:
        bottom_a, bottom_b = box_a[3], box_b[3]
        max_height = max(self._compute_height(box_a), self._compute_height(box_b))
        return abs(bottom_a - bottom_b) / max_height <= self.y_alignment_threshold

    def _should_link(self, box_a: list[int], box_b: list[int]) -> bool:
        return (
            self._same_horizontal_band(box_a, box_b)
            and self._horizontal_overlap(box_a, box_b) >= self.horizontal_overlap_threshold
        )

    # -----------------------
    # Fully vectorized group assignment
    # -----------------------
    def _assign_group_ids(
        self, detections: list[dict[str, Any]]
    ) -> tuple[list[str], dict[str, list[int]]]:
        n = len(detections)
        if n == 0:
            return [], {}

        group_ids = [""] * n
        group_to_members: dict[str, list[int]] = {}

        boxes = np.array([det[BBOX] for det in detections])
        track_ids = np.array([det.get(TRACK_ID, -1) for det in detections], dtype=int)
        heights = np.maximum(1, boxes[:, 3] - boxes[:, 1])
        bottoms = boxes[:, 3]

        x1, _, x2, _ = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]

        # Horizontal overlap
        x_overlap = np.minimum(x2[:, None], x2[None, :]) - np.maximum(x1[:, None], x1[None, :])
        x_overlap[x_overlap < self.horizontal_overlap_threshold] = 0

        # Vertical alignment
        max_height = np.maximum(heights[:, None], heights[None, :])
        y_delta = np.abs(bottoms[:, None] - bottoms[None, :]) / max_height
        y_align = y_delta <= self.y_alignment_threshold

        adjacency = (x_overlap > 0) & y_align
        np.fill_diagonal(adjacency, False)

        # Connected components
        adj_sparse = csr_matrix(adjacency)
        n_components, labels = connected_components(adj_sparse, directed=False)

        for comp_id in range(n_components):
            members = np.where(labels == comp_id)[0]
            member_track_ids = [track_ids[i] for i in members if track_ids[i] >= 0]
            if len(member_track_ids) < 2:
                continue
            group_id = "-".join(map(str, sorted(member_track_ids)))
            for m in members:
                group_ids[m] = group_id
            group_to_members[group_id] = sorted(members.tolist())

        return group_ids, group_to_members
