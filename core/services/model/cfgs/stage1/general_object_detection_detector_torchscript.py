from typing import Any

import cv2
import numpy as np
import torch

from constants.detections_constant import BBOX, CLASS_ID, CLASS_NAME, CONFIDENCE, MODEL_ID
from services.model.cfgs.ibase_stage import BaseStage


class GeneralObjectDetectorTypescriptStage1(BaseStage):
    """
    TorchScript/TypeScript Object Detector
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
    ):
        super().__init__(model_id)

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = torch.jit.load(model_path, map_location=self.device)
        self.model.eval()

        self.tag = tag
        self.img_size = 640

        self.class_names: dict[int, str] = {
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

        # ── JIT warmup ──────────────────────────────────────────────────────
        # TorchScript compiles on the FIRST forward pass (can be 3-4s).
        # Running a dummy pass at init time moves that cost to startup so
        # live-frame inference is instant.
        try:
            dummy = torch.zeros(1, 3, self.img_size, self.img_size, device=self.device)
            with torch.no_grad():
                self.model(dummy)
        except Exception:
            pass  # warmup failure is non-fatal

    def _preprocess(self, image):
        img = cv2.resize(image, (self.img_size, self.img_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(img).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0).to(self.device)
        return tensor

    def _infer(self, image_tensor):
        with torch.no_grad():
            outputs = self.model(image_tensor)
        return outputs.detach().cpu().numpy()

    def _postprocess(self, outputs, original_shape):
        detections: list[dict[str, Any]] = []
        preds = np.squeeze(outputs)

        # YOLOv8 raw output
        if len(preds.shape) == 2 and preds.shape[0] < preds.shape[1]:
            preds = preds.T

        h, w = original_shape[:2]
        scale_x = w / self.img_size
        scale_y = h / self.img_size

        boxes = []
        scores = []
        class_ids = []

        conf_threshold = 0.25
        iou_threshold = 0.45

        for pred in preds:
            cx, cy, bw, bh = pred[:4]
            class_scores = pred[4:]

            cls_conf = float(class_scores.max())
            cls_id = int(class_scores.argmax())

            if cls_conf < conf_threshold:
                continue

            x1 = (cx - bw / 2) * scale_x
            y1 = (cy - bh / 2) * scale_y
            x2 = (cx + bw / 2) * scale_x
            y2 = (cy + bh / 2) * scale_y

            boxes.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])
            scores.append(cls_conf)
            class_ids.append(cls_id)

        if not boxes:
            return detections

        indices = cv2.dnn.NMSBoxes(boxes, scores, conf_threshold, iou_threshold)

        for i in indices:
            i = i[0] if isinstance(i, list | tuple | np.ndarray) else i

            x, y, bw, bh = boxes[i]
            cls_id = class_ids[i]

            if cls_id >= len(self.class_names):
                continue

            detections.append(
                {
                    # "guid_id": str(uuid.uuid4()),
                    BBOX: [x, y, x + bw, y + bh],
                    CONFIDENCE: scores[i],
                    CLASS_ID: cls_id,
                    CLASS_NAME: self.class_names[cls_id],
                    MODEL_ID: self.model_id,
                }
            )
            # endure guid from base satge

            for det in detections:
                self._ensure_guid(det)

        return detections

    def forward(
        self,
        image: np.ndarray,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        detections = []

        if isinstance(self.tag, str):
            tag_list = [t.lower() for t in self.tag.split(",")]
        else:
            tag_list = [t.lower() for t in self.tag]

        try:
            tensor = self._preprocess(image)
            outputs = self._infer(tensor)
            pred_detections = self._postprocess(outputs, image.shape)
        except Exception as e:
            print(f"TorchScript inference failed: {e}")
            return []

        for det in pred_detections:
            cls_name = det[CLASS_NAME].lower()
            if "all" in tag_list or cls_name in tag_list:
                detections.append(det)

        return detections

    @property
    def names(self) -> dict[int, str]:
        # Shahmeer Bugged Fixed {i: name for i, name in enumerate(self.class_names)}
        return self.names
