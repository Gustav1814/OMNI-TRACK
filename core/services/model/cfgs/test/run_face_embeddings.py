"""Manual runner for the Face Embeddings Pipeline using OpenCV display."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from constants.detections_constant import BBOX, CLASS_ID, DEFAULT_CLASS_ID, FOLLOWED_TO
from services.common.models.pipe_structure import PipeStructure
from services.embedders.arcface_embedder import ArcFaceEmbedder
from services.managers.color_manager import ColorManager
from services.model.cfgs.model_pipeline import ModelPipeline
from services.model.cfgs.stage1.general_object_detection_detector import GeneralObjectDetectorStage1
from services.model.cfgs.stage2.general_embeddings_stage2 import GeneralEmbeddingsStage2
from services.visualization.detection_annotation_renderer import DetectionAnnotationRenderer
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer

VIDEO_URL_DEFAULT = "https://ai-public-videos.s3.us-east-2.amazonaws.com/Raw+Videos/office_exp/Screencast+from+24-12-2025+18%3A58%3A54.webm"


def _ensure_face_model_path() -> Path:
    weights_path = Path(__file__).resolve().parents[3] / "inferenced_weights" / "face11n.pt"
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Expected YOLOV8N weights at {weights_path}; please download or update the path."
        )
    return weights_path


def _ensure_arcface_model_path() -> Path:
    """Ensure the ArcFace embedding model exists."""
    arcface_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "inferenced_weights"
        / "models"
        / "buffalo_l"
        / "w600k_r50.onnx"
    )
    if not arcface_path.exists():
        raise FileNotFoundError(f"ArcFace model not found at {arcface_path}")
    return arcface_path


def _render_with_renderer(
    frame: np.ndarray,
    detections: Iterable[dict[str, Any]],
    renderer: IAnnotationRenderer,
    color_manager: ColorManager,
    regions: list[Any],
) -> None:
    """Render detections on frame."""
    for detection in detections:
        detection_to_render = dict(detection)
        followed_to = detection.get(FOLLOWED_TO, [])
        if len(followed_to) != 0:
            _render_with_renderer(frame, followed_to, renderer, color_manager, regions)
            continue
        bbox = detection_to_render.get(BBOX)
        if bbox is not None:
            detection_to_render[BBOX] = [int(coord) for coord in bbox]

        class_id_value = detection_to_render.get(CLASS_ID)
        if not isinstance(class_id_value, int):
            detection_to_render[CLASS_ID] = DEFAULT_CLASS_ID

        renderer.render(frame, detection_to_render, regions, color_manager)


def main(video_url: str = VIDEO_URL_DEFAULT) -> None:
    """
    Run the face detection and embeddings pipeline on a video.

    Args:
        video_url: Path or URL to video file
    """
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Running pipeline on device: {device}")

    # ========== LOAD MODELS ==========
    print("\n[MODELS] Loading face detection and embeddings models...")

    try:
        face_model_path = _ensure_face_model_path()
        print(f"  ✓ Face detection model: {face_model_path.name}")
    except FileNotFoundError as e:
        print(f"  ✗ {e}")
        return

    try:
        arcface_path = _ensure_arcface_model_path()
        embedder = ArcFaceEmbedder(model_path=str(arcface_path))
        print(f"  ✓ ArcFace embedder loaded (dim={embedder.embedding_dim})")
    except FileNotFoundError as e:
        print(f"  ✗ {e}")
        return
    # Register a test identity in CacheService for the runner
    from services.core.cache_service import CacheService

    cache = CacheService(threshold=0.5)
    cache.add_identity("Test_Admin", [0.5] * 512)

    # ========== CREATE PIPELINE ==========
    print("\n[PIPELINE] Building model pipeline...")
    pipeline = ModelPipeline(
        model_configs=[
            PipeStructure(
                model=GeneralObjectDetectorStage1(
                    model_path=str(face_model_path),
                    model_id="face_detector",
                    tag=["face"],
                    device=device,
                ),
                model_id="face_detector",
                order=0,
                lead_by="",
            ),
            PipeStructure(
                model=GeneralEmbeddingsStage2(
                    model_path=str(arcface_path),
                    model_id="face_embeddings",
                    tag=["face"],
                    embedder_type="arcface",
                    cache_name="face_embeddings_cache",
                    cache_type="faiss",
                    padding_ratio=0.1,
                ),
                model_id="face_embeddings",
                order=1,
                lead_by="face_detector",
            ),
        ]
    )
    print("  ✓ Pipeline ready")

    # ========== INITIALIZE RENDERER ==========
    renderer = DetectionAnnotationRenderer()
    color_manager = ColorManager()
    regions: list[Any] = []

    # ========== OPEN VIDEO ==========
    print(f"\n[VIDEO] Opening video: {video_url}")
    capture = cv2.VideoCapture(video_url)
    if not capture.isOpened():
        print("  ✗ Failed to open video")
        return

    print("  ✓ Video opened")
    print("  Press 'q' or ESC to exit\n")

    # ========== PROCESS VIDEO ==========
    frame_count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print("Reached end of stream")
                break

            frame_count += 1

            # Run pipeline (Stage 1 + Stage 2)
            detections: list[dict] = pipeline(frame)

            # Render detections
            _render_with_renderer(frame, detections, renderer, color_manager, regions)

            # Display frame
            cv2.imshow("Face Embeddings Pipeline", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

            # Print progress
            if frame_count % 30 == 0:
                print(f"Processed {frame_count} frames")

    finally:
        capture.release()
        cv2.destroyAllWindows()

    print(f"\n✓ Processing complete ({frame_count} frames processed)")


if __name__ == "__main__":
    main()
