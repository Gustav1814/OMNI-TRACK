"""Manual runner for the NumberPlateDetector stage using OpenCV display."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np
import torch

from constants.detections_constant import BBOX, CLASS_ID, DEFAULT_CLASS_ID, FOLLOWED_TO
from services.common.models.pipe_structure import PipeStructure
from services.managers.color_manager import ColorManager
from services.model.cfgs.model_pipeline import ModelPipeline
from services.model.cfgs.stage1.general_pe_tracker import GeneralPeTrackerStage1
from services.model.cfgs.stage2.HAR_Transformer.HAR_transformer_stage2 import HAR_Stage2
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer
from services.visualization.pose_annotation_renderer import PoseAnnotationRenderer

# VIDEO_URL_DEFAULT = "https://ai-public-videos.s3.us-east-2.amazonaws.com/Raw+Videos/input.mp4"
VIDEO_URL_DEFAULT = "https://ai-public-videos.s3.us-east-2.amazonaws.com/Raw+Videos/salute.mp4"


def _ensure_weights_path() -> Path:
    weights_path = (
        Path(__file__).resolve().parents[3] / "inferenced_weights" / "har_transformer.pth"
    )
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Expected license-plate weights at {weights_path}; please download or update the path."
        )
    return weights_path


def _render_with_renderer(
    frame: np.ndarray,
    detections: Iterable[dict],
    renderer: IAnnotationRenderer,
    color_manager: ColorManager,
    regions: list,
) -> None:
    for detection in detections:
        detection_to_render = dict(detection)

        followed_to = detection.get(FOLLOWED_TO, [])
        if len(followed_to) != 0:
            _render_with_renderer(frame, followed_to, renderer, color_manager, regions)
            continue

        # Ensure bbox is always present
        bbox = detection_to_render.get(BBOX)
        if bbox is not None:
            detection_to_render[BBOX] = [int(coord) for coord in bbox]
        else:
            detection_to_render[BBOX] = [0, 0, 0, 0]

        # Ensure class_id is always an int
        class_id_value = detection_to_render.get(CLASS_ID)
        if not isinstance(class_id_value, int):
            detection_to_render[CLASS_ID] = DEFAULT_CLASS_ID

        # Ensure class_name exists
        if "class_name" not in detection_to_render:
            detection_to_render["class_name"] = "unknown"

        renderer.render(frame, detection_to_render, regions, color_manager)


def main(video_url: str = VIDEO_URL_DEFAULT) -> None:
    transformer_weights_path = _ensure_weights_path()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Running pipeline on device: {device}")
    pipeline = ModelPipeline(
        model_configs=[
            PipeStructure(
                model=GeneralPeTrackerStage1(
                    model_path="yolov8n-pose",
                    model_id="yolov8n-pose",
                    tag=["person"],
                ),
                model_id="yolov8n-pose",
                order=0,
                lead_by="",
            ),
            PipeStructure(
                model=HAR_Stage2(
                    model_path=str(transformer_weights_path),
                    model_id="HAR_Model_Stage2",
                    tag=["drop", "clapping", "salute", "coughing", "back pain"],
                    activity_threshold=0.2,
                ),
                model_id="har_transformer",
                order=1,
                lead_by="yolov8n-pose",
            ),
        ]
    )

    renderer = PoseAnnotationRenderer()
    color_manager = ColorManager()
    regions: list = []

    capture = cv2.VideoCapture(video_url)
    if not capture.isOpened():
        raise RuntimeError(
            f"OpenCV could not open the video at {video_url}. "
            "Check your network connection or try downloading the file locally."
        )

    print("Press 'q' or ESC to exit the preview window.")

    try:
        frame_counter = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                print("Reached end of stream or encountered a read error.")
                break

            frame_counter += 1

            # Run pipeline on current frame
            detections: list[dict] = pipeline(frame)

            # print(f"Frame {frame_counter} detections: {detections}")

            # Render detections
            _render_with_renderer(frame, detections, renderer, color_manager, regions)

            cv2.imshow("Number Plate Detector", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
