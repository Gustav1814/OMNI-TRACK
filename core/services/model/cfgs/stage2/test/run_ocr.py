"""Quick manual runner for the OCR stage using EasyOCR."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from urllib.request import urlopen

import cv2
import matplotlib.pyplot as plt
import numpy as np

from constants.detections_constant import BBOX, CLASS_ID, CLASS_NAME, CONFIDENCE, DEFAULT_CLASS_ID
from services.managers.color_manager import ColorManager
from services.model.cfgs.stage2.ocr_stage2 import OCR_STAGE2
from services.visualization.detection_annotation_renderer import DetectionAnnotationRenderer
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer

DEFAULT_TEXT = "ABC1234"
WINDOW_NAME = "OCR Stage Demo"
# Set this to a path if you want to use a specific image instead of the synthetic sample.
IMAGE_PATH: str | None = "https://ai-public-videos.s3.us-east-2.amazonaws.com/Inferenced+Videos/image_2025-11-08_215055686.png"


def _create_demo_image(text: str) -> np.ndarray:
    canvas: np.ndarray = np.full((240, 640, 3), 255, dtype=np.uint8)
    cv2.putText(
        canvas,
        text,
        (40, 160),
        cv2.FONT_HERSHEY_SIMPLEX,
        3.0,
        (0, 0, 0),
        5,
        cv2.LINE_AA,
    )
    return canvas


def _load_source(image_path: str | None, text: str) -> np.ndarray:
    if not image_path:
        return _create_demo_image(text)

    parsed = urlparse(image_path)
    if parsed.scheme in {"http", "https"}:
        with urlopen(image_path) as response:
            data = np.asarray(bytearray(response.read()), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    else:
        resolved = Path(image_path).expanduser().resolve()
        image = cv2.imread(str(resolved))

    if image is None:
        raise FileNotFoundError(f"Could not read image from {image_path}")
    return cast(np.ndarray, image)


def _render_with_renderer(
    frame: np.ndarray,
    detections: Iterable[dict[str, Any]],
    renderer: IAnnotationRenderer,
    color_manager: ColorManager,
    regions: list[Any],
) -> None:
    for detection in detections:
        detection_to_render = dict(detection)

        bbox = detection_to_render.get(BBOX)
        if bbox is not None:
            detection_to_render[BBOX] = [int(coord) for coord in bbox]

        class_id_value = detection_to_render.get(CLASS_ID)
        if not isinstance(class_id_value, int):
            detection_to_render[CLASS_ID] = DEFAULT_CLASS_ID

        renderer.render(frame, detection_to_render, regions, color_manager)


def main(image_path: str | None = IMAGE_PATH, text: str = DEFAULT_TEXT) -> None:
    frame = _load_source(image_path, text)
    ocr_stage = OCR_STAGE2(model_path="", model_id="ocr", tag=["en"])
    renderer = DetectionAnnotationRenderer()
    color_manager = ColorManager()
    regions: list[Any] = []
    h, w, _ = frame.shape
    fake_prev_results = [
        {
            BBOX: [0, 0, w, h],
            CLASS_ID: 0,
            CLASS_NAME: "full_image",
            CONFIDENCE: 0.1,
        }
    ]

    results = ocr_stage.forward(frame, prev_results=fake_prev_results)
    detections = results
    for detection in detections:
        print(detection)

    print("OCR detections:")
    _render_with_renderer(frame, detections, renderer, color_manager, regions)
    plt.imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    plt.title(WINDOW_NAME)
    plt.axis("off")
    plt.show()


if __name__ == "__main__":
    main()
