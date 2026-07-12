from typing import Any

import cv2
import numpy as np

from dtos.request.request_register_use_case import Region
from services.managers.color_manager import ColorManager
from services.visualization.master_annotation_renderer import MasterAnnotationRenderer


class DetectionAnnotationRenderer(MasterAnnotationRenderer):
    def render(
        self,
        frame: np.ndarray,
        detection: dict[str, Any],
        regions: list[Region],
        color_manager: ColorManager,
        **kwargs: Any,
    ) -> np.ndarray:
        del regions
        bbox = detection.get("bbox", [0, 0, 0, 0])
        class_name = detection.get("class_name", "unknown")
        in_region = detection.get("in_region", False)
        region_name = detection.get("region_name", "")
        track_id = detection.get("track_id", -1)
        global_id = detection.get("global_id", "N/A")
        class_id = detection.get("class_id", 0)
        confidence = detection.get("confidence", 0.5)

        # Use consistent color for tracked objects, random for non-tracked
        color_track_label = self.get_color_track(region_name, track_id, global_id)
        color = color_manager.get_color(color_track_label, class_id)

        cv2.rectangle(frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 2)

        if track_id != -1:
            label = f"{class_name} T_{track_id} G_{global_id} {confidence:.2f}"
        else:
            label = class_name

        if in_region and region_name:
            label += f" ({region_name})"

        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        cv2.rectangle(
            frame,
            (bbox[0], bbox[1] - label_size[1] - 10),
            (bbox[0] + label_size[0], bbox[1]),
            color,
            -1,
        )
        cv2.putText(
            frame, label, (bbox[0], bbox[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
        )
        # Draw timestamp on the frame
        frame = self._draw_timestamp(frame, **kwargs)
        return frame
