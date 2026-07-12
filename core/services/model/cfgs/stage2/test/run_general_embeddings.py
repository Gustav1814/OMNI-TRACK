"""Quick manual runner for the General Embeddings stage using image input."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import cv2
import matplotlib.pyplot as plt
import numpy as np

from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    DEFAULT_CLASS_ID,
    GUID_ID,
    MODEL_ID,
)
from services.embedders.arcface_embedder import ArcFaceEmbedder
from services.managers.color_manager import ColorManager
from services.model.cfgs.stage2.general_embeddings_stage2 import GeneralEmbeddingsStage2
from services.visualization.detection_annotation_renderer import DetectionAnnotationRenderer
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer

WINDOW_NAME = "General Embeddings Stage Demo"
# Set this to a path if you want to use a specific image
IMAGE_PATH: str | None = (
    r"https://ai-public-videos.s3.us-east-2.amazonaws.com/Raw+Videos/angry2.png"
)


def _ensure_arcface_model() -> Path:
    """Ensure the ArcFace ONNX model exists."""
    model_path = (
        Path(__file__).resolve().parents[4]
        / "inferenced_weights"
        / "models"
        / "buffalo_l"
        / "w600k_r50.onnx"
    )
    if not model_path.exists():
        raise FileNotFoundError(
            f"ArcFace model not found at {model_path}. "
            "Please ensure the model checkpoint is available."
        )
    return model_path


def _load_source(image_path: str | None) -> np.ndarray:
    """
    Load an image from a file path or URL.

    Args:
        image_path: Path to image file or URL, or None to raise error

    Returns:
        Loaded image as numpy array
    """
    if not image_path:
        raise ValueError("IMAGE_PATH must be provided")

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
    return image


def _create_fake_detections(frame_height: int, frame_width: int) -> list[dict]:
    """
    Create fake Stage 1 detections for testing embeddings.
    In a real scenario, these would come from a face/object detection model (Stage 1).

    Args:
        frame_height: Height of the frame
        frame_width: Width of the frame

    Returns:
        List of fake detection dictionaries
    """
    detections = []

    # Face detection (top-left area)
    # x1, y1 = 0, 0
    # x2, y2 = 250, 350
    # detections.append({
    #     BBOX: [x1, y1, x2, y2],
    #     CLASS_ID: 0,
    #     CLASS_NAME: "face",
    #     CONFIDENCE: 0.95,
    #     GUID_ID: "det_face_001",
    #     MODEL_ID: "yolov8",
    # })

    # Another face detection (if frame is large enough)
    if frame_width > 600 and frame_height > 400:
        x1, y1 = 320, 50
        x2, y2 = 480, 260
        detections.append(
            {
                BBOX: [x1, y1, x2, y2],
                CLASS_ID: 0,
                CLASS_NAME: "face",
                CONFIDENCE: 0.92,
                GUID_ID: "det_face_002",
                MODEL_ID: "yolov8",
            }
        )

    # Person detection (full body) - if space available
    # if frame_height > 450:
    #     x1, y1 = 100, 200
    #     x2, y2 = 400, frame_height - 50
    #     detections.append({
    #         BBOX: [x1, y1, x2, y2],
    #         CLASS_ID: 1,
    #         CLASS_NAME: "person",
    #         CONFIDENCE: 0.88,
    #         GUID_ID: "det_person_001",
    #         MODEL_ID: "yolov8",
    #     })

    return detections


def _render_with_renderer(
    frame: np.ndarray,
    detections: Iterable[dict],
    renderer: IAnnotationRenderer,
    color_manager: ColorManager,
    regions: list,
) -> None:
    """Render detections on the frame using the provided renderer."""
    for detection in detections:
        detection_to_render = dict(detection)

        bbox = detection_to_render.get(BBOX)
        if bbox is not None:
            detection_to_render[BBOX] = [int(coord) for coord in bbox]

        class_id_value = detection_to_render.get(CLASS_ID)
        if not isinstance(class_id_value, int):
            detection_to_render[CLASS_ID] = DEFAULT_CLASS_ID

        renderer.render(frame, detection_to_render, regions, color_manager)


def _print_embedding_stats(results: list[dict]) -> None:
    """Print statistics about the embeddings generated."""
    embedded_count = sum(1 for det in results if "embedding_id" in det)

    print("\nEmbedding Statistics:")
    print(f"  Total detections: {len(results)}")
    print(f"  Embedded detections: {embedded_count}")

    for i, detection in enumerate(results):
        if "embedding_id" in detection:
            print(
                f"\n  Detection {i+1}:"
                f"\n    Class: {detection.get(CLASS_NAME)}"
                f"\n    Confidence: {detection.get(CONFIDENCE, 0.0):.3f}"
                f"\n    Embedding ID: {detection.get('embedding_id')}"
                f"\n    Embedding Dim: {detection.get('embedding_dim')}"
                f"\n    Embedder: {detection.get('embedding_metadata', {}).get('embedder', 'unknown')}"
            )


def main(
    image_path: str | None = IMAGE_PATH,
    cache_name: str = "demo_cache",
    cache_type: str = "faiss",
    tags_to_embed: list[str] | None = None,
) -> None:
    """
    Main function to run embeddings on an image.

    Args:
        image_path: Path to the image file or URL
        cache_name: Name of the vector cache to use
        cache_type: Type of cache backend ('faiss', 'weaviate', etc.)
        tags_to_embed: List of class names to generate embeddings for (default: ['face', 'person'])
    """
    if tags_to_embed is None:
        tags_to_embed = ["face", "person"]

    # ========== ARRANGE ==========
    print("General Embeddings Stage Demo")
    print("=" * 60)

    # Load image
    print(f"\nLoading image from: {image_path}")
    frame = _load_source(image_path)
    h, w, _ = frame.shape
    print(f"Frame size: {w}x{h}")

    # ArcFace weights are required — GeneralEmbeddingsStage2 loads ArcFace internally
    print("\nResolving ArcFace weights...")
    try:
        model_path = _ensure_arcface_model()
        model_path_str = str(model_path)
        preview = ArcFaceEmbedder(model_path=model_path_str)
        print(f"✓ ArcFace weights OK: {preview.model_id} (dim={preview.embedding_dim})")
    except FileNotFoundError as e:
        print(f"✗ Model not found: {e}")
        print("  GeneralEmbeddingsStage2 requires ArcFace ONNX weights; cannot run demo.")
        return

    # Initialize embeddings stage
    print("\nInitializing GeneralEmbeddingsStage2...")
    print(f"  Cache: {cache_name} ({cache_type})")
    print(f"  Tags: {tags_to_embed}")

    embeddings_stage = GeneralEmbeddingsStage2(
        model_path=model_path_str,
        model_id="general_embeddings_demo",
        tag=tags_to_embed,
        cache_name=cache_name,
        cache_type=cache_type,
        padding_ratio=0.1,
    )
    print("✓ Stage2 initialized")

    # Initialize renderer and color manager for visualization
    renderer = DetectionAnnotationRenderer()
    color_manager = ColorManager()
    regions: list = []

    # Create fake Stage 1 detections
    print("\nCreating fake Stage 1 detections...")
    fake_detections = _create_fake_detections(h, w)
    for det in fake_detections:
        print(f"  - {det.get(CLASS_NAME)}: bbox={det.get(BBOX)}, conf={det.get(CONFIDENCE):.2f}")

    # ========== ACT ==========
    print("\nGenerating embeddings...")
    results = embeddings_stage(image=frame, prev_results=fake_detections)

    # ========== DISPLAY RESULTS ==========
    _print_embedding_stats(results)

    cache_svc = embeddings_stage.cache_service
    print("\nCache / identity service:")
    print(f"  Backend type: {cache_svc.backend_type}")
    print(f"  Similarity threshold: {cache_svc.threshold}")

    # Render detections on frame
    print("\nRendering detections on frame...")
    _render_with_renderer(frame, results, renderer, color_manager, regions)

    # Display frame using matplotlib
    print("\nDisplaying result...")
    plt.figure(figsize=(12, 8))
    plt.imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    plt.title(WINDOW_NAME)
    plt.axis("off")
    plt.tight_layout()
    plt.show()

    print("\n" + "=" * 60)
    print("Demo completed successfully!")


if __name__ == "__main__":
    main()
