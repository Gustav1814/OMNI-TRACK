from typing import Any

import cv2
import numpy as np

from constants.models import KEYPOINT_NAMES, SKELETON_CONNECTIONS
from dtos.request.request_register_use_case import Region
from services.managers.color_manager import ColorManager
from services.visualization.detection_annotation_renderer import DetectionAnnotationRenderer
from services.visualization.master_annotation_renderer import MasterAnnotationRenderer


class PoseAnnotationRenderer(MasterAnnotationRenderer):
    SKELETON_CONNECTIONS = SKELETON_CONNECTIONS
    KEYPOINT_NAMES = KEYPOINT_NAMES

    def render(
        self,
        frame: np.ndarray,
        detection: dict[str, Any],
        regions: list[Region],
        color_manager: ColorManager,
        confidence_threshold: float = 0.5,
        draw_labels: bool = False,
        **kwargs: Any,
    ) -> np.ndarray:
        keypoints = detection.get("keypoints", [])
        if not keypoints:
            return DetectionAnnotationRenderer().render(
                frame, detection, regions, color_manager, **kwargs
            )

        region_name = detection.get("region_name", "")
        track_id = detection.get("track_id", -1)
        class_id = detection.get("class_id", 0)
        global_id = detection.get("global_id", "N/A")

        # Use consistent color for tracked objects, random for non-tracked
        color_track_label = self.get_color_track(region_name, track_id, global_id)
        skeleton_color = color_manager.get_color(color_track_label, class_id)

        keypoint_color = (255, 0, 255)
        label_color = (255, 255, 0)

        for connection in self.SKELETON_CONNECTIONS:
            pt1_idx, pt2_idx = connection
            if pt1_idx >= len(keypoints) or pt2_idx >= len(keypoints):
                continue
            kp1 = keypoints[pt1_idx]
            kp2 = keypoints[pt2_idx]
            if (
                len(kp1) >= 3
                and len(kp2) >= 3
                and kp1[2] > confidence_threshold
                and kp2[2] > confidence_threshold
            ):
                pt1 = (int(kp1[0]), int(kp1[1]))
                pt2 = (int(kp2[0]), int(kp2[1]))
                cv2.line(frame, pt1, pt2, skeleton_color, 2, cv2.LINE_AA)

        for i, keypoint in enumerate(keypoints):
            if len(keypoint) >= 3 and keypoint[2] > confidence_threshold:
                x, y = int(keypoint[0]), int(keypoint[1])
                cv2.circle(frame, (x, y), 4, keypoint_color, -1, cv2.LINE_AA)
                cv2.circle(frame, (x, y), 5, (255, 255, 255), 1, cv2.LINE_AA)

                if draw_labels and i < len(self.KEYPOINT_NAMES):
                    label_text = self.KEYPOINT_NAMES[i].replace("_", " ")
                    font_scale = 0.35
                    thickness = 1
                    (text_width, text_height), baseline = cv2.getTextSize(
                        label_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
                    )
                    label_x, label_y = x + 8, y + 4
                    cv2.rectangle(
                        frame,
                        (label_x - 2, label_y - text_height - 2),
                        (label_x + text_width + 2, label_y + baseline + 2),
                        (0, 0, 0),
                        -1,
                    )
                    cv2.putText(
                        frame,
                        label_text,
                        (label_x, label_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale,
                        label_color,
                        thickness,
                        cv2.LINE_AA,
                    )

        annotated_frame = DetectionAnnotationRenderer().render(
            frame, detection, regions, color_manager, **kwargs
        )
        # Draw timestamp on the frame
        annotated_frame = self._draw_timestamp(annotated_frame, **kwargs)
        return annotated_frame
