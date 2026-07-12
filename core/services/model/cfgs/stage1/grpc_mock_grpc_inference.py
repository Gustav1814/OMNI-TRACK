from typing import Any

import cv2

# 👇 ADD GRPC CLIENT HERE (this is the correct place)
import grpc
import torch
from google.protobuf.empty_pb2 import Empty

import compiler_generated.simple_object_detector_pb2 as pb2
import compiler_generated.simple_object_detector_pb2_grpc as pb2_grpc
from configs.grpc_config import GrpcConfig
from constants.detections_constant import (
    BBOX,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    MASK,
    MODEL_ID,
)
from services.model.cfgs.ibase_stage import BaseStage


class GRPCMockInferenceStage1(BaseStage):
    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        is_global_reid: bool = False,
    ):
        super().__init__(model_id)

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.tag = tag
        self.is_remote = True
        self.remote_key_name = "mock_model_key_name"
        self._model_path = model_path  # Not used in this mock, but kept for consistency
        self._is_global_reid = is_global_reid

        grpc_cfg = GrpcConfig()
        endpoint = grpc_cfg.get(self.remote_key_name)

        self.pb2 = pb2
        self.stub: pb2_grpc.ObjectDetectionStub = pb2_grpc.ObjectDetectionStub(
            grpc.insecure_channel(
                endpoint["address"],
                options=[
                    ("grpc.max_send_message_length", 20 * 1024 * 1024),
                    ("grpc.max_receive_message_length", 20 * 1024 * 1024),
                ],
            )
        )

        self.metadata = grpc_cfg.get_metadata(self.remote_key_name)
        self.health_check()

    def health_check(self) -> bool:
        response = self.stub.HealthCheck(Empty(), metadata=self.metadata)
        if response.status != "healthy":
            raise ConnectionError("GRPCMockInferenceStage1 Model is not healthy")

        return True

    def forward(
        self, image: Any, prev_results: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        # Encode image
        success, encoded = cv2.imencode(".jpg", image)
        if not success:
            return []

        request = pb2.DetectionRequest(
            frame=encoded.tobytes(),
            height=image.shape[0],
            width=image.shape[1],
            channels=image.shape[2],
            frame_id="frame-001",
            timestamp=int(__import__("time").time() * 1000),
            camera_id="camera-01",
            prev_results=prev_results if prev_results is not None else [],
        )

        response = self.stub.Forward(request, metadata=self.metadata)

        detections: list[dict[str, Any]] = []

        for obj in response.objects:
            cls_name = obj.class_name.lower()

            if cls_name not in self.tag and "all" not in self.tag:
                continue

            detection = {
                BBOX: [int(obj.x1), int(obj.y1), int(obj.x2), int(obj.y2)],
                CONFIDENCE: float(obj.confidence),
                CLASS_NAME: obj.class_name,
                CLASS_ID: -1,  # gRPC doesn't send class_id
                MODEL_ID: self.model_id,
                MASK: None,  # not supported in your proto
            }

            self._ensure_guid(detection)
            detections.append(detection)

        return detections

    @property
    def names(self) -> dict[int, str]:
        response = self.stub.GetNames(Empty(), metadata=self.metadata)
        names = {}
        for id, name in response.names.items():
            names[id] = name
        return names
