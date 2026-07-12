from typing import Any

import cv2
import numpy as np
import onnxruntime as ort
import torch

from services.managers.tracker_factory import TrackerFactory
from services.model.cfgs.ibase_stage import BaseStage
from services.trackers.interface.itracker import ITracker


class GeneralObjectTrackerOnnxStage1(BaseStage):
    """
    Stage 1 - ONNX YOLO + Tracker
    Same behavior pattern as .pt version:
    - supports tag filtering by class names
    - supports 'all'
    - exposes 80 COCO classes via names property
    """

    COCO80_NAMES = {
        0: "person",
        1: "bicycle",
        2: "car",
        3: "motorcycle",
        4: "airplane",
        5: "bus",
        6: "train",
        7: "truck",
        8: "boat",
        9: "traffic light",
        10: "fire hydrant",
        11: "stop sign",
        12: "parking meter",
        13: "bench",
        14: "bird",
        15: "cat",
        16: "dog",
        17: "horse",
        18: "sheep",
        19: "cow",
        20: "elephant",
        21: "bear",
        22: "zebra",
        23: "giraffe",
        24: "backpack",
        25: "umbrella",
        26: "handbag",
        27: "tie",
        28: "suitcase",
        29: "frisbee",
        30: "skis",
        31: "snowboard",
        32: "sports ball",
        33: "kite",
        34: "baseball bat",
        35: "baseball glove",
        36: "skateboard",
        37: "surfboard",
        38: "tennis racket",
        39: "bottle",
        40: "wine glass",
        41: "cup",
        42: "fork",
        43: "knife",
        44: "spoon",
        45: "bowl",
        46: "banana",
        47: "apple",
        48: "sandwich",
        49: "orange",
        50: "broccoli",
        51: "carrot",
        52: "hot dog",
        53: "pizza",
        54: "donut",
        55: "cake",
        56: "chair",
        57: "couch",
        58: "potted plant",
        59: "bed",
        60: "dining table",
        61: "toilet",
        62: "tv",
        63: "laptop",
        64: "mouse",
        65: "remote",
        66: "keyboard",
        67: "cell phone",
        68: "microwave",
        69: "oven",
        70: "toaster",
        71: "sink",
        72: "refrigerator",
        73: "book",
        74: "clock",
        75: "vase",
        76: "scissors",
        77: "teddy bear",
        78: "hair drier",
        79: "toothbrush",
    }

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        tracker_name: str = "bytetrack",
        is_global_reid: bool = False,
        tag_at_reid: list[str] | str = "",
    ):
        super().__init__(model_id)

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        providers = ["CUDAExecutionProvider"] if self.device == "cuda" else ["CPUExecutionProvider"]

        self.session = ort.InferenceSession(model_path, providers=providers)

        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.img_size = 640

        self.tag = tag
        self.tag_at_reid = tag_at_reid

        # Tracker factory (same as pt version)
        self.tracker: ITracker
        if is_global_reid:
            self.tracker = TrackerFactory.create_tracker(
                tracker_name="global_base",
                tracker_config={"tracker_name": tracker_name},
            )
        else:
            self.tracker = TrackerFactory.create_tracker(tracker_name)

    # --------------------------------------------------
    # PREPROCESS
    # --------------------------------------------------

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        img = cv2.resize(image, (self.img_size, self.img_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = np.expand_dims(img, axis=0)
        return img

    # --------------------------------------------------
    # INFERENCE
    # --------------------------------------------------

    def _infer(self, image: np.ndarray) -> Any:
        tensor = self._preprocess(image)
        outputs = self.session.run(self.output_names, {self.input_name: tensor})
        return outputs[0]

    # --------------------------------------------------
    # POSTPROCESS
    # --------------------------------------------------

    def _postprocess(self, outputs: Any, shape: tuple[int, ...]) -> list[list[int | float]]:
        """
        Output expected:
        [x1, y1, x2, y2, conf, cls]
        """
        detections: list[list[int | float]] = []

        if outputs is None:
            return detections

        preds = np.squeeze(outputs)

        if preds.size == 0:
            return detections

        if preds.ndim == 1:
            preds = preds.reshape(1, -1)

        h, w = shape[:2]
        scale_x = w / self.img_size
        scale_y = h / self.img_size

        for p in preds:
            if len(p) < 6:
                continue

            x1, y1, x2, y2, conf, cls_id = p[:6]

            if conf < 0.25:
                continue

            x1 *= scale_x
            x2 *= scale_x
            y1 *= scale_y
            y2 *= scale_y

            detections.append(
                [
                    int(x1),
                    int(y1),
                    int(x2),
                    int(y2),
                    float(conf),
                    int(cls_id),
                ]
            )

        return detections

    # --------------------------------------------------
    # MAIN FORWARD
    # --------------------------------------------------

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        detections: list[dict[str, Any]] = []

        # normalize tags (same as pt version)
        if isinstance(self.tag, str):
            tag_list = [t.strip().lower() for t in self.tag.split(",")]
        else:
            tag_list = [t.strip().lower() for t in self.tag]

        # 1. ONNX inference
        try:
            raw = self._infer(image)
            detections_raw = self._postprocess(raw, image.shape)
        except Exception as e:
            print(f"ONNX inference failed: {e}")
            return detections

        if not detections_raw:
            return detections

        # 2. Build class filter EXACTLY like pt version
        classes_to_track = None

        if "all" not in tag_list:
            classes = []

            for class_id, class_name in self.names.items():
                if str(class_name).lower() in tag_list:
                    classes.append(class_id)

            classes_to_track = classes if classes else None

        # 3. Tracker call (same pattern as pt version)
        try:
            detections = self.tracker.track(
                frame=image,
                detections=detections_raw,
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

    # --------------------------------------------------
    # NAMES PROPERTY
    # --------------------------------------------------

    @property
    def names(self) -> dict[int, str]:
        return self.COCO80_NAMES
