"""
Quick manual runner for Face Landmarks Extraction via gRPC (Stage 2).

This test sends a face image to a gRPC server and receives landmarks.
Ensure the gRPC server is running before executing this script:
    Terminal 1: python host/face_landmarks_remote_server.py
    Terminal 2: python services/model/cfgs/stage2/test/run_general-face_landmarks-mediapipe_grpc_stage2.py

Note: The face.png image is used as a cropped face crop, and landmarks are fetched from the server.
"""

from __future__ import annotations

from typing import cast
from urllib.parse import urlparse
from urllib.request import urlopen

import cv2
import numpy as np

from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    GUID_ID,
    KEYPOINTS,
    MODEL_ID,
    TRACK_ID,
)
from services.managers.color_manager import ColorManager
from services.model.cfgs.stage2.general_face_landmarks_mediapipe_grpc_stage2 import (
    GRPCGeneralFaceLandMarkStage2,
)
from services.visualization.face_landmarks_annotation_renderer import (
    FaceLandmarksAnnotationRenderer,
)

WINDOW_NAME = "Face Landmarks gRPC Stage Demo"
# Face image from S3
IMAGE_URL: str = r"https://ai-public-videos.s3.us-east-2.amazonaws.com/Raw+Videos/face2.png"


def _load_image_from_url(url: str) -> np.ndarray:
    """
    Load an image from a URL.

    Args:
        url: HTTP/HTTPS URL pointing to an image

    Returns:
        Loaded image as numpy array (BGR format)

    Raises:
        FileNotFoundError: If image cannot be downloaded or decoded
    """
    print(f"[INFO] Loading image from URL: {url}")
    try:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            with urlopen(url) as response:
                data = np.asarray(bytearray(response.read()), dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        else:
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}")

        if image is None:
            raise FileNotFoundError(f"Could not decode image from {url}")

        print(f"[OK] Image loaded successfully. Shape: {image.shape}")
        return image

    except Exception as e:
        raise FileNotFoundError(f"Failed to load image from {url}: {e}") from e


def _create_face_detection_from_image(image: np.ndarray, track_id: int = 1) -> list[dict]:
    """
    Create a face detection that covers the entire image.
    This simulates a cropped face image being the full frame.

    Args:
        image: Full image (BGR format)
        track_id: Tracking ID for the detection

    Returns:
        List with a single face detection covering the entire image
    """
    h, w = image.shape[:2]
    detection = {
        BBOX: [0, 0, w, h],  # Full frame as the face bbox
        CLASS_ID: 0,
        CLASS_NAME: "face",
        CONFIDENCE: 0.95,
        TRACK_ID: track_id,
        GUID_ID: f"face_{track_id}",
    }
    return [detection]


def _print_landmarks_info(detections: list[dict]) -> None:
    """
    Print landmarks information from detections.

    Args:
        detections: List of detection dictionaries with landmarks
    """
    print("\n" + "=" * 80)
    print("FACE LANDMARKS RESULTS")
    print("=" * 80)

    for idx, detection in enumerate(detections):
        print(f"\nDetection #{idx + 1}:")
        print(f"  Class: {detection.get(CLASS_NAME, 'N/A')}")
        print(f"  Confidence: {detection.get(CONFIDENCE, 'N/A'):.2f}")
        print(f"  Track ID: {detection.get(TRACK_ID, 'N/A')}")
        print(f"  Model ID: {detection.get(MODEL_ID, 'N/A')}")

        keypoints = detection.get(KEYPOINTS, [])
        if keypoints:
            print(f"  Landmarks: {len(keypoints)} points received")
            print(f"\n  {'Index':<6} {'X':<12} {'Y':<12} {'Confidence':<12} {'Z':<12}")
            print(f"  {'-' * 60}")
            for i, kp in enumerate(keypoints[:10]):  # Show first 10 landmarks
                x, y, conf, z = kp if len(kp) >= 4 else (*kp, 0)
                print(f"  {i:<6} {x:<12.4f} {y:<12.4f} {conf:<12.4f} {z:<12.4f}")
            if len(keypoints) > 10:
                print(f"  ... and {len(keypoints) - 10} more landmarks")
        else:
            print("  Landmarks: None (Server may not have returned results)")

    print("\n" + "=" * 80)


def _test_jpeg_bytes_encoding(image: np.ndarray) -> bytes | None:
    """
    Test JPEG bytes encoding to verify the image can be successfully encoded.
    """
    print("\n[TEST] Testing JPEG bytes encoding...")
    try:
        success, buffer = cv2.imencode(
            ".jpg",
            image,
            [cv2.IMWRITE_JPEG_QUALITY, 90],
        )

        if not success:
            raise ValueError("cv2.imencode failed")

        encoded_bytes = buffer.tobytes()

        print("[OK] Image encoded to JPEG bytes successfully")
        print(f"    Original size: {image.nbytes:,} bytes")
        print(f"    Encoded size: {len(encoded_bytes):,} bytes")
        print(f"    Compression ratio: {image.nbytes / len(encoded_bytes):.1f}x")

        return cast(bytes, encoded_bytes)

    except Exception as e:
        print(f"[ERROR] JPEG bytes encoding failed: {e}")
        return None


def main() -> None:
    """
    Main test function for Face Landmarks gRPC Stage.

    Workflow:
    1. Load face image from URL
    2. Create a fake detection covering the entire image
    3. Initialize GeneralFaceLandMarkGrpc stage
    4. Call forward() to fetch landmarks from gRPC server
    5. Print results
    6. Display image (optional)
    """
    print("=" * 80)
    print("Face Landmarks gRPC Stage Test")
    print("=" * 80)

    # ========== ARRANGE ==========
    try:
        # Load image from URL
        frame = _load_image_from_url(IMAGE_URL)
        h, w = frame.shape[:2]
        print(f"[OK] Frame dimensions: {w}x{h}")

    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return

    # Test JPEG bytes encoding
    _test_jpeg_bytes_encoding(frame)

    # Create face detection (entire image as face crop)
    print("\n[INFO] Creating face detection...")
    fake_face_detections = _create_face_detection_from_image(frame, track_id=1)
    print(f"[OK] Created {len(fake_face_detections)} detection(s)")
    print(f"    Detection: {fake_face_detections[0]}")

    # Initialize gRPC stage
    print("\n[INFO] Initializing GeneralFaceLandMarkGrpc stage...")
    try:
        grpc_stage = GRPCGeneralFaceLandMarkStage2(
            model_path="",  # Not used for gRPC client
            model_id="remote_face_landmarks",
            tag="face",  # Face label for filtering
        )
        print("[OK] gRPC stage initialized")
    except Exception as e:
        print(f"[ERROR] Failed to initialize gRPC stage: {e}")
        print(
            "[HINT] Ensure gRPC server is running at localhost:50051 "
            "(or set GRPC_SERVER env var)"
        )
        return

    # ========== ACT ==========
    print("\n[INFO] Calling forward() to fetch landmarks from gRPC server...")
    print("[HINT] Ensure the gRPC server is running before this step!")
    try:
        results = grpc_stage.forward(frame, prev_results=fake_face_detections)
        print("[OK] Forward pass completed")
    except Exception as e:
        print(f"[ERROR] Forward pass failed: {e}")
        import traceback

        traceback.print_exc()
        return

    # ========== ASSERT/DISPLAY ==========
    # Print landmark results
    _print_landmarks_info(results)

    # Verify landmarks were received
    has_landmarks = any(detection.get(KEYPOINTS) for detection in results)
    if has_landmarks:
        print("[SUCCESS] Landmarks received and processed!")
    else:
        print(
            "[WARNING] No landmarks in results. "
            "Check if gRPC server is running and responding correctly."
        )

    # Optional: Display image with annotated landmarks and bounding box
    print("\n[INFO] Displaying image...")
    annotated = frame.copy()

    renderer = FaceLandmarksAnnotationRenderer()
    color_manager = ColorManager()

    for detection in results:
        annotated = renderer.render(
            frame=annotated,
            detection=detection,
            regions=[],
            color_manager=color_manager,
            confidence_threshold=0.5,
            draw_labels=True,
        )

    # Display using OpenCV
    cv2.imshow(WINDOW_NAME, annotated)
    print("[OK] Image displayed. Press any key to close.")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
