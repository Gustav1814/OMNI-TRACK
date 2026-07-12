from typing import Any

import torch
from ultralytics import YOLO

from services.managers.tracker_factory import TrackerFactory
from services.model.cfgs.ibase_stage import BaseStage
from services.trackers.interface.itracker import ITracker


class GeneralObjectTrackerStage1(BaseStage):
    """
    Stage 1 - Detect and track objects across frames.
    Uses pluggable tracker implementations (ByteTrack, BoTSORT, DeepSORT, etc.)
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        tracker_name: str = "bytetrack",
        is_global_reid: bool = False,
        tag_at_reid: list[str] | str = "",
        #    tracker_config: Optional[Dict[str, Any]] = None,
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
        self.tag_at_reid = tag_at_reid

        # Initialize tracker using factory
        self.tracker: ITracker
        if is_global_reid:
            self.tracker = TrackerFactory.create_tracker(
                tracker_name="global_base", tracker_config={"tracker_name": tracker_name}
            )
        else:
            self.tracker = TrackerFactory.create_tracker(tracker_name)

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Track objects across frames using configured tracker.

        Process:
        1. Normalize tag to list of lowercase class names
        2. Run model predictions to get raw detections
        3. Build class ids list from filtered classes
        4. Call tracker.track() with class filter
        5. Return tracked detections filtered by tag
        """
        detections: list[dict[str, Any]] = []

        # Normalize tag to list of lowercase class names
        if isinstance(self.tag, str):
            tag_list = [t.lower() for t in self.tag.split(",")]
        else:
            tag_list = [t.lower() for t in self.tag]

        # Step 1: Run model predictions to get raw detections
        try:
            pred_results = self.model.predict(image, device=self.device, verbose=False, conf=0.5)
        except Exception as e:
            print(f"Prediction failed: {e}")
            return detections

        if not pred_results:
            return detections

        pred_result = pred_results[0]

        # Step 2: Build list of class ids that match the requested tag
        # This allows us to pass `classes` to tracker so it only processes relevant classes
        classes_to_track = None
        if "all" not in tag_list and pred_result.names:
            classes = []
            for class_id, class_name in pred_result.names.items():
                if str(class_name).lower() in tag_list:
                    classes.append(class_id)
            classes_to_track = classes if classes else None

        # Step 3: Call tracker.track() from tracker instance
        try:
            detections = self.tracker.track(
                frame=image,
                model=self.model,
                device=self.device,
                persist=True,
                conf=0.5,
                model_id=self.model_id,
                tag=tag_list,
                tag_at_reid=self.tag_at_reid,
                classes=classes_to_track,
            )
        except Exception as e:
            print(f"Tracking failed: {e}")
            detections = []

        for det in detections:
            self._ensure_guid(det)

        return detections

    @property
    def names(self) -> dict[int, str]:
        return BaseStage._ensure_name_mapping(getattr(self.model, "names", None))
