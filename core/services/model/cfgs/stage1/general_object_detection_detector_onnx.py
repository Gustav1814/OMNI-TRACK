import uuid
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort
import torch

from constants.detections_constant import BBOX, CLASS_ID, CLASS_NAME, CONFIDENCE, MODEL_ID
from services.model.cfgs.ibase_stage import BaseStage


class GeneralObjectDetectorOnnxStage1(BaseStage):
    """
    ONNX Object Detector (Final stable version)
    Supports:
    - Raw YOLO ONNX (no NMS)
    - ONNX with built-in NMS
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
    ):
        super().__init__(model_id)

        # -------------------------
        # DEVICE
        # -------------------------
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        providers = ["CPUExecutionProvider"]
        if self.device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        # -------------------------
        # ONNX SESSION
        # -------------------------
        self.session = ort.InferenceSession(model_path, providers=providers)

        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]

        # -------------------------
        # CONFIG
        # -------------------------
        self.tag = tag
        self.img_size = 640

        # ⚠️ Adjust if custom model
        self.class_names = {
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

    # -------------------------
    # PREPROCESS
    # -------------------------
    def _preprocess(self, image):
        img = cv2.resize(image, (self.img_size, self.img_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = np.expand_dims(img, axis=0)
        return img

    # -------------------------
    # INFERENCE
    # -------------------------
    def _infer(self, image):
        img = self._preprocess(image)
        outputs = self.session.run(self.output_names, {self.input_name: img})
        return outputs[0]

    # -------------------------
    # POSTPROCESS
    # -------------------------
    def _postprocess_with_nms(self, preds, original_shape):
        detections = []
        h, w = original_shape[:2]
        scale_x = w / self.img_size
        scale_y = h / self.img_size

        for det in preds:
            x1, y1, x2, y2, conf, cls_id = det

            if conf < 0.25:
                continue

            cls_id = int(cls_id)

            if cls_id >= len(self.class_names):
                continue

            detections.append(
                {
                    "guid_id": str(uuid.uuid4()),  # ✅ REQUIRED
                    BBOX: [
                        int(x1 * scale_x),
                        int(y1 * scale_y),
                        int(x2 * scale_x),
                        int(y2 * scale_y),
                    ],
                    CONFIDENCE: float(conf),
                    CLASS_ID: cls_id,
                    CLASS_NAME: self.class_names[cls_id],
                    MODEL_ID: self.model_id,
                }
            )

        return detections

    def _postprocess(self, outputs, original_shape):
        detections = []

        preds = np.squeeze(outputs)

        # -------------------------
        # CASE 1: ONNX already has NMS
        # shape → (N, 6)
        # -------------------------
        if len(preds.shape) == 2 and preds.shape[1] == 6:
            return self._postprocess_with_nms(preds, original_shape)

        # -------------------------
        # CASE 2: Raw YOLO output
        # -------------------------
        # Fix shape (84, 8400) → (8400, 84)
        if preds.shape[0] < preds.shape[1]:
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
            obj_conf = pred[4]
            class_scores = pred[5:]

            cls_id = np.argmax(class_scores)
            cls_conf = class_scores[cls_id]

            conf = obj_conf * cls_conf

            if conf < conf_threshold:
                continue

            x1 = (cx - bw / 2) * scale_x
            y1 = (cy - bh / 2) * scale_y
            x2 = (cx + bw / 2) * scale_x
            y2 = (cy + bh / 2) * scale_y

            boxes.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])
            scores.append(float(conf))
            class_ids.append(int(cls_id))

        # -------------------------
        # NMS
        # -------------------------
        indices = cv2.dnn.NMSBoxes(boxes, scores, conf_threshold, iou_threshold)

        for raw_i in indices:
            i = int(raw_i[0] if isinstance(raw_i, list | tuple | np.ndarray) else raw_i)

            x, y, bw, bh = boxes[i]
            cls_id_int = int(class_ids[i])

            if cls_id_int >= len(self.class_names):
                continue

            detections.append(
                {
                    # "guid_id": str(uuid.uuid4()),  # ✅ REQUIRED
                    BBOX: [x, y, x + bw, y + bh],
                    CONFIDENCE: scores[i],
                    CLASS_ID: cls_id_int,
                    CLASS_NAME: self.class_names[cls_id_int],
                    MODEL_ID: self.model_id,
                }
            )
            for det in detections:
                self._ensure_guid(det)

        return detections

    # -------------------------
    # FORWARD
    # -------------------------
    def forward(
        self,
        image: np.ndarray,
        prev_results: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        detections = []

        # normalize tags
        if isinstance(self.tag, str):
            tag_list = [t.lower() for t in self.tag.split(",")]
        else:
            tag_list = [t.lower() for t in self.tag]

        try:
            outputs = self._infer(image)

            # DEBUG (uncomment if needed)
            # print("Output shape:", outputs.shape)

            pred_detections = self._postprocess(outputs, image.shape)

        except Exception as e:
            print(f"ONNX inference failed: {e}")
            return []

        for det in pred_detections:
            cls_name = det[CLASS_NAME].lower()

            if "all" in tag_list or cls_name in tag_list:
                detections.append(det)

        print(f"Detection: {detections}")

        return detections

    @property
    def names(self) -> dict[int, str]:
        return self.names
