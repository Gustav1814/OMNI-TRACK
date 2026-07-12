from __future__ import annotations

from typing import Any

import cv2
import grpc
import numpy as np
import torch
from google.protobuf.empty_pb2 import Empty

import compiler_generated.face_landmarks_pb2 as pb2
import compiler_generated.face_landmarks_pb2_grpc as pb2_grpc
from configs.grpc_config import GrpcConfig
from constants.detections_constant import (
    BBOX,
    CLASS_NAME,
    KEYPOINTS,
    MODEL_ID,
    TRACK_ID,
)
from services.model.cfgs.ibase_stage import BaseStage


class GRPCGeneralFaceLandMarkStage2(BaseStage):
    """
    Stage 2 - Face Landmarks Extraction via gRPC
    Sends cropped face images to a remote server and receives landmarks.
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: str,
        device: str | None = None,  # noqa: ARG002
        is_global_reid: bool = False,
    ):
        super().__init__(model_id)

        self.face_label = tag.lower() if isinstance(tag, str) else "face"
        self.channel: grpc.Channel | None = None
        self.stub: pb2_grpc.VisionServiceStub | None = None
        self._model_path = model_path
        self._is_global_reid = is_global_reid
        self.remote_key_name = "face_landmarks_key_name"

        self._initialize_grpc_client()

    def _initialize_grpc_client(self) -> None:
        """
        Initialize gRPC channel and stub.
        """
        try:
            grpc_cfg = GrpcConfig()
            endpoint = grpc_cfg.get(self.remote_key_name)

            self.channel = grpc.insecure_channel(
                endpoint["address"],
                options=[
                    ("grpc.max_send_message_length", 20 * 1024 * 1024),
                    ("grpc.max_receive_message_length", 20 * 1024 * 1024),
                ],
            )
            self.stub = pb2_grpc.VisionServiceStub(self.channel)
            self.metadata = grpc_cfg.get_metadata(self.remote_key_name)

            self.health_check()

        except Exception as e:
            raise ConnectionError(f"Failed to initialize gRPC client: {e}") from e

    def health_check(self) -> bool:
        """
        Check if gRPC server is healthy.
        """
        try:
            if self.stub is None:
                raise ConnectionError("gRPC stub not initialized")

            response = self.stub.HealthCheck(Empty(), metadata=self.metadata)
            if response.status != "healthy":
                raise ConnectionError(f"gRPC server not healthy: {response.status}")
            return True

        except Exception as e:
            raise ConnectionError(f"Health check failed: {e}") from e

    def _crop_face(
        self, image: np.ndarray, bbox: list[int] | tuple[int, int, int, int]
    ) -> np.ndarray | None:
        x1, y1, x2, y2 = map(int, bbox)
        h, w = image.shape[:2]

        x1 = max(0, min(w - 1, x1))
        x2 = max(x1 + 1, min(w, x2))
        y1 = max(0, min(h - 1, y1))
        y2 = max(y1 + 1, min(h, y2))

        if x2 <= x1 or y2 <= y1:
            return None

        face_crop = image[y1:y2, x1:x2]
        return face_crop if face_crop.size > 0 else None

    def _encode_face_as_jpeg(self, face_crop: np.ndarray, quality: int = 90) -> bytes | None:
        try:
            success, buffer = cv2.imencode(
                ".jpg",
                face_crop,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            return buffer.tobytes() if success else None
        except Exception:
            return None

    def _send_to_grpc_server(self, image_bytes: bytes, track_id: int) -> dict[str, Any] | None:
        if self.stub is None:
            return None

        try:
            image_data = pb2.ImageData(image=image_bytes)
            request = pb2.VisionRequest(
                image=image_data,
                track_id=track_id,
            )

            response = self.stub.ProcessVision(request, metadata=self.metadata)

            return {
                "success": response.success,
                "message": response.message,
                "track_id": response.track_id,
                "landmarks": [
                    {
                        "x": float(lm.x),
                        "y": float(lm.y),
                        "z": float(lm.z),
                        "confidence": float(lm.confidence),
                    }
                    for lm in response.landmarks
                ],
            }

        except grpc.RpcError:
            return None
        except Exception:
            return None

    def _landmarks_to_keypoints(
        self, landmarks: list[dict[str, float]], x_offset: float = 0.0, y_offset: float = 0.0
    ) -> list[list[float]]:
        keypoints = []
        for lm in landmarks:
            conf = lm.get("confidence", 1.0)
            if conf == 0.0:
                conf = 1.0
            keypoints.append(
                [
                    lm.get("x", 0.0) + x_offset,
                    lm.get("y", 0.0) + y_offset,
                    conf,
                    lm.get("z", 0.0),
                ]
            )
        return keypoints

    @torch.inference_mode()
    def forward(
        self,
        image: np.ndarray,
        prev_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not prev_results:
            return []

        for detection in prev_results:
            self._ensure_guid(detection)

            if detection.get(CLASS_NAME, "").lower() != self.face_label:
                continue

            track_id = detection.get(TRACK_ID)

            bbox = detection.get(BBOX)
            if bbox is None:
                continue

            face_crop = self._crop_face(image, bbox)
            if face_crop is None:
                continue

            image_bytes = self._encode_face_as_jpeg(face_crop)
            if image_bytes is None:
                continue

            grpc_track_id = int(track_id) if track_id is not None else -1

            response = self._send_to_grpc_server(image_bytes, grpc_track_id)
            if response is None:
                continue

            if response.get("success") and response.get("landmarks"):
                x_offset = float(bbox[0])
                y_offset = float(bbox[1])
                keypoints = self._landmarks_to_keypoints(
                    response["landmarks"], x_offset=x_offset, y_offset=y_offset
                )
                detection[KEYPOINTS] = keypoints
                detection[MODEL_ID] = self.model_id

        #print("landmarks_GRPC_2: ", prev_results)
        return prev_results

    @property
    def __del__(self) -> None:
        if self.channel is not None:
            self.channel.close()

    @property
    def names(self) -> dict[int, str]:
        try:
            if self.stub is None:
                return {}
            response = self.stub.GetNames(Empty(), metadata=self.metadata)
            return dict(response.names.items())
        except Exception:
            return {}
