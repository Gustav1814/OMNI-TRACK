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
)
from constants.models import SKELETON_CONNECTIONS
from services.managers.activity_matching_manager import ActivityMatching
from services.model.cfgs.ibase_stage import BaseStage


class GeneralPEStage1(BaseStage):
    """
    Stage 1 - Detect objects (e.g., persons, plates, etc.) and optionally extract keypoints/skeletons.
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
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

        self.tag = tag

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Runs YOLO detection on the input image and returns bounding boxes, confidence,
        class labels, and optionally keypoints if supported by the model.
        """
        results = self.model.predict(image, device=self.device, verbose=False)[0]
        detections: list[dict[str, Any]] = []

        has_keypoints = hasattr(results, "keypoints") and results.keypoints is not None

        if results.boxes is None:
            return detections

        for i in range(len(results.boxes)):
            box = results.boxes[i]
            cls = results.names[int(box.cls)]
            if not (cls.lower() in self.tag or "all" in self.tag):
                continue

            detection: dict[str, Any] = {
                BBOX: list(map(int, box.xyxy[0].tolist())),
                CONFIDENCE: float(box.conf),
                CLASS_ID: int(box.cls),
                CLASS_NAME: cls.lower(),
                MODEL_ID: self.model_id,
            }
            self._ensure_guid(detection)

            # If model outputs keypoints (e.g. YOLO-Pose)
            if has_keypoints and results.keypoints is not None:
                kp_data = results.keypoints[i].data
                kps_arr = (
                    kp_data.cpu().numpy()
                    if isinstance(kp_data, torch.Tensor)
                    else np.asarray(kp_data)
                )
                kps = kps_arr.squeeze().tolist()
                # ensure shape is [[x,y,conf], ...]
                if isinstance(kps[0][0], list):  # nested case
                    kps = kps[0]
                detection[KEYPOINTS] = kps  # [[x, y, conf], ...]
                detection[SKELETON] = SKELETON_CONNECTIONS

            detections.append(detection)

        return detections

    @property
    def names(self) -> dict[int, str]:
        if "activity_matching" in self.model_id:
            return {
                index: str(name) for index, name in enumerate(ActivityMatching.ACTIVITIES.keys())
            }
        else:
            return cast(
                dict[int, str],
                BaseStage._ensure_name_mapping(getattr(self.model, "names", None)),
            )
