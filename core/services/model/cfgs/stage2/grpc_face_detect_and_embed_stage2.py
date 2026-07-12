from __future__ import annotations

import time
from typing import Any

import cv2
import grpc
from google.protobuf.empty_pb2 import Empty

import compiler_generated.face_detection_pb2 as pb2
import compiler_generated.face_detection_pb2_grpc as pb2_grpc
from configs.grpc_config import GrpcConfig
from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    FOLLOWED_TO,
    GUID_ID,
    MODEL_ID,
    TRACK_ID,
)
from services.model.cfgs.ibase_stage import BaseStage

_32MB = 32 * 1024 * 1024


class GRPCFaceDetectAndEmbedStage2(BaseStage):
    """
    Stage 2 — gRPC client for face detection and embedding.

    For each person detection received from Stage 1:
      1. Crops the person region from the frame.
      2. Sends the crop to the face-detection gRPC service.
      3. The service detects faces, aligns to 112x112, retrieves 512-d embeddings.
      4. Returns enriched detection dicts preserving all Stage 1 keys.

    Output keys added to each detection dict:
        CLASS_NAME   — always "face"
        CLASS_ID     — always 1
        CONFIDENCE   — face detection confidence score
        BBOX         — face bounding box [x1, y1, x2, y2] in crop coordinates
        MODEL_ID     — this stage's model_id
        GUID_ID      — track_id as string (satisfies framework validation)
        "landmarks"  — list of (x, y) tuples for 5 facial keypoints
        "embedding"  — list of 512 floats (L2-normalized ArcFace embedding)
        "face_track_id" — int track_id returned by the face-detection service
    """

    def __init__(
        self,
        model_id: str,
        tag: list[str] | str,
        model_path: str | None = None,  # noqa: ARG002
        device: str | None = None,  # noqa: ARG002
        is_global_reid: bool = False,
    ):
        super().__init__(model_id)

        self.tag = tag
        self.is_remote = True
        self.remote_key_name = "face_detect_and_embed_grpc"
        self._is_global_reid = is_global_reid

        grpc_cfg = GrpcConfig()
        endpoint = grpc_cfg.get(self.remote_key_name)

        self.pb2 = pb2
        self.stub: pb2_grpc.FaceDetectionStub = pb2_grpc.FaceDetectionStub(
            grpc.insecure_channel(
                endpoint["address"],
                options=[
                    ("grpc.max_send_message_length", _32MB),
                    ("grpc.max_receive_message_length", _32MB),
                ],
            )
        )
        self.metadata = grpc_cfg.get_metadata(self.remote_key_name)
        self.health_check()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        response = self.stub.HealthCheck(Empty(), metadata=self.metadata)
        if response.status != "healthy":
            raise ConnectionError("Face Detection service is not healthy")
        return True

    # ------------------------------------------------------------------
    # BaseStage interface
    # ------------------------------------------------------------------

    def forward(
        self,
        image: Any,
        prev_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        For each person detection in prev_results:
        - Crops the person region using its bounding box.
        - Sends the crop to the face-detection gRPC service.
        - Merges the face detection result with the original det dict so all
          framework-required keys (guid_id, track_id, etc.) are preserved.
        """
        if not prev_results:
            return []

        pb_prev = self._build_pb_prev(prev_results)

        for det in prev_results:
            x1, y1, x2, y2 = map(int, det[BBOX])
            cropped_person = image[y1:y2, x1:x2]

            if cropped_person.size == 0:
                det[FOLLOWED_TO] = []
                continue

            _, buffer = cv2.imencode(".jpg", cropped_person)
            ch, cw = cropped_person.shape[:2]

            request = self.pb2.DetectionRequest(
                frame=buffer.tobytes(),
                height=ch,
                width=cw,
                channels=3,
                timestamp=int(time.time() * 1000),
                camera_id="camera-01",
                prev_results=pb_prev,
            )

            try:
                response = self.stub.Forward(request, metadata=self.metadata)
            except grpc.RpcError as e:
                print(f"[GRPCFaceDetectAndEmbedStage2] gRPC error: {e.code()} — {e.details()}")
                det[FOLLOWED_TO] = []
                continue

            det[FOLLOWED_TO] = [
                {
                    CLASS_NAME: obj.class_name,
                    CLASS_ID: 1,
                    CONFIDENCE: obj.confidence,
                    BBOX: [int(obj.x1) + x1, int(obj.y1) + y1, int(obj.x2) + x1, int(obj.y2) + y1],
                    MODEL_ID: self.model_id,
                    GUID_ID: str(det.get(TRACK_ID, "")),
                    "landmarks": [(int(lm.x) + x1, int(lm.y) + y1) for lm in obj.landmarks],
                    "embedding": list(obj.embedding),
                    "face_track_id": obj.track_id,
                }
                for obj in response.objects
            ]

        return prev_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_pb_prev(self, prev_results: list[dict[str, Any]]) -> list:
        pb_prev = []
        for det in prev_results:
            x1, y1, x2, y2 = map(int, det[BBOX])
            pb_prev.append(
                self.pb2.PreviousDetection(
                    bbox=[x1, y1, x2, y2],
                    confidence=float(det.get(CONFIDENCE, 0.0)),
                    class_name=str(det.get(CLASS_NAME, "")),
                    class_id=int(det.get(CLASS_ID, 0)),
                    model_id=str(det.get(MODEL_ID, "")),
                    track_id=int(det.get(TRACK_ID, 0)),
                    attributes={
                        k: str(v)
                        for k, v in det.items()
                        if k not in (BBOX, CONFIDENCE, CLASS_NAME, CLASS_ID, MODEL_ID, TRACK_ID)
                        and isinstance(v, str | int | float | bool)
                    },
                )
            )
        return pb_prev

    # ------------------------------------------------------------------
    # BaseStage required property
    # ------------------------------------------------------------------

    @property
    def names(self) -> dict[int, str]:
        return {1: "face"}
