import json
import os
import time
from datetime import UTC, datetime
from typing import Any, Protocol

import cv2
import numpy as np

from config.BaseConfig import config
from constants.detections_constant import (
    BBOX,
    CLASS_NAME,
    CONFIDENCE,
    GUID_ID,
    ITEM_AT_CURRENT_REGION_NAME,
    SHARED_PATH,
    TRACK_ID,
)
from constants.region_names import FRAME_COUNT, JOB_ID
from services.common.models.yolo_models import KPIResult
from utils.services.hash_service import (
    DeduplicationStrategy,
    HashAlgorithm,
    HashServiceFactory,
    PayloadHashService,
)


class IKPI(Protocol):
    async def compute(
        self, frame: np.ndarray, detections: list[dict[str, Any]], **kwargs: Any
    ) -> KPIResult:
        ...


class BaseKPI:
    dedup_seconds = 1
    recent_snapshots: dict[str, float] = {}
    nats_subject = config.NATS.NATS_DETECTIONS_SUBJECT  # Simple direct assignment
    _hash_service: PayloadHashService | None = None
    _hash_service_name = "kpi_nats_dedup"

    @classmethod
    def _get_hash_service(cls) -> PayloadHashService:
        """Get or create the payload deduplication service for this KPI."""
        if cls._hash_service is None:
            cls._hash_service = HashServiceFactory.get_instance(cls._hash_service_name)

            if cls._hash_service is None:
                from os import getenv

                buffer_size_env = getenv("AIS_NATS_PAYLOAD_BUFFER_SIZE", "100")
                try:
                    buffer_size = int(buffer_size_env)
                except ValueError:
                    buffer_size = 100

                algorithm_env = getenv("AIS_NATS_HASH_ALGORITHM", "sha256").upper()
                try:
                    algorithm = HashAlgorithm[algorithm_env]
                except KeyError:
                    algorithm = HashAlgorithm.SHA256

                cls._hash_service = HashServiceFactory.create_custom(
                    name=cls._hash_service_name,
                    buffer_size=buffer_size,
                    algorithm=algorithm,
                    strategy=DeduplicationStrategy.SKIP,
                )

        return cls._hash_service

    @classmethod
    def _should_publish(cls, payload: bytes) -> bool:
        """
        Returns True if the payload is new (not a duplicate) and should be published.
        The hash service records the payload so subsequent identical payloads are skipped.
        """
        should_publish, _ = cls._get_hash_service().process_payload(payload)
        return should_publish

    @classmethod
    def _should_save_snapshot(cls, key: str) -> bool:
        now = time.time()
        last_time = cls.recent_snapshots.get(key, 0)
        if now - last_time < cls.dedup_seconds:
            return False
        cls.recent_snapshots[key] = now
        return True

    @staticmethod
    def save_snapshot_to_disk(
        frame: np.ndarray,
        detection: dict[str, Any],
        snapshot_tags: list[str],
        **kwargs: Any,
    ) -> str:
        try:
            cls_name = detection.get(CLASS_NAME, "-1").lower()
            frame_count = kwargs.get("frame_count", 0)
            if "all" in snapshot_tags or cls_name in snapshot_tags:
                kpi_name = kwargs.get("kpi_name")
                loc_id = kwargs.get("loc_id")
                cam_id = kwargs.get("cam_id")
                shared_path = kwargs.get(SHARED_PATH)
                if not all([kpi_name, loc_id, cam_id, shared_path]):
                    print("Missing required kwargs to save image.")
                    return ""
                assert shared_path is not None
                snapshot_dir = str(shared_path)
                track_id = detection.get(TRACK_ID, -1)
                dedup_key = f"{kpi_name}_{track_id}" if track_id != -1 else f"{kpi_name}_{cls_name}"
                if not BaseKPI._should_save_snapshot(dedup_key):
                    return ""
                x1, y1, x2, y2 = map(int, detection.get(BBOX, [0, 0, 0, 0]))
                crop = frame[y1:y2, x1:x2]
                if not crop.size:
                    print("Empty crop detected.")
                    return ""
                dt = datetime.now(UTC)
                filename = f"kpis~{kpi_name}~{cam_id}~{loc_id}~{cls_name}~{dt:%Y_%m_%d}~{dt:%H_%M_%S}~{frame_count}~{track_id}.png"
                os.makedirs(snapshot_dir, exist_ok=True)
                file_path = os.path.join(snapshot_dir, filename)
                cv2.imwrite(file_path, crop)
                return file_path
        except Exception as e:
            print(f"save_snapshot_to_disk ERROR: {e}")
        return ""

    @staticmethod
    def make_payload_for_nats(
        detection: dict[str, Any],
        frame_width: int | None = None,
        frame_height: int | None = None,
        **kwargs: Any,
    ) -> bytes:
        try:
            payload = {
                GUID_ID: str(detection.get(GUID_ID, "")),
                CLASS_NAME: detection.get(CLASS_NAME, ""),
                CONFIDENCE: float(detection.get(CONFIDENCE, 0.0)),
                TRACK_ID: str(detection.get(TRACK_ID, "")),
                "Regions": [detection.get(ITEM_AT_CURRENT_REGION_NAME, "global")],
                FRAME_COUNT: int(kwargs.get(FRAME_COUNT, 0)),
                JOB_ID: str(kwargs.get(JOB_ID, "")),
            }
            if frame_width and frame_height:
                payload["FrameResolution"] = {"Width": frame_width, "Height": frame_height}

            payload_bytes = json.dumps(payload).encode()

            # Automatically select mode based on subject
            return payload_bytes

        except Exception as e:
            print(f"make_payload_for_nats ERROR: {e}")
            return b""
