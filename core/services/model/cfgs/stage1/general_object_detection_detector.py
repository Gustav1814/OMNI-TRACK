from typing import Any, cast

import torch
from ultralytics import YOLO

from constants.detections_constant import BBOX, CLASS_ID, CLASS_NAME, CONFIDENCE, MODEL_ID
from services.model.cfgs.ibase_stage import BaseStage


class GeneralObjectDetectorStage1(BaseStage):
    """
    Stage 2 - Detect number plates from car crops.
    Depends on CarDetector output.
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
        results = self.model.predict(image, device=self.device, verbose=False)[0]
        detections: list[dict[str, Any]] = []

        boxes = results.boxes
        if boxes is None:
            return detections

        for i in range(len(boxes)):
            box = boxes[i]
            cls = results.names[int(box.cls)]
            if cls.lower() in self.tag or "all" in self.tag:
                detection = {
                    BBOX: list(map(int, box.xyxy[0].tolist())),
                    CONFIDENCE: float(box.conf),
                    CLASS_ID: int(box.cls),
                    CLASS_NAME: cls.lower(),
                    MODEL_ID: self.model_id,
                }
                self._ensure_guid(detection)
                detections.append(detection)
        return detections

    @property
    def names(self) -> dict[int, str]:
        return cast(
            dict[int, str], BaseStage._ensure_name_mapping(getattr(self.model, "names", None))
        )
