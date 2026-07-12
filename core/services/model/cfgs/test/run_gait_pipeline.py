"""
Full gait recognition pipeline: Stage 1 → Stage 2 → Stage 3.

  Stage 1 (GeneralObjectTrackerStage1):
      YOLO + ByteTrack → person bboxes with stable track_ids.

  Stage 2 (PaddleSegSilhouetteStage2):
      Crops each person, runs PPHumanSeg ONNX → binary mask →
      float32 [64×64] silhouette.  Buffers per track_id; fires
      SILHOUETTE_SEQUENCE_READY every SEND_EVERY frames once MIN_FRAMES
      are accumulated.

  Stage 3 (GaitGrpcStage3):
      Sends ready sequences to GaitFeatureService via gRPC.
      Injects GAIT_MATCHED_ID / GAIT_CONFIDENCE / GAIT_DISTANCE into each
      detection dict so the display always shows the latest identity.

Display:
    Bbox overlay — orange while buffering, green when recognised, red on error.
    Label        — tid + buffer depth while buffering; identity + confidence when known.
    Stats bar    — frame index, active tracks, sequences sent, P2 status.

Press 'q' or ESC to quit.

Usage:
    python services/model/cfgs/test/run_gait_pipeline.py
    python services/model/cfgs/test/run_gait_pipeline.py path/to/video.mp4
    python services/model/cfgs/test/run_gait_pipeline.py 0   # webcam

Environment:
    GAIT_GRPC_SERVER   — gRPC server address (default: ai-srv.qbscocloud.net:30812)
    GAIT_GRPC_USERNAME — gRPC username (default: verseye)
    GAIT_GRPC_PASSWORD — gRPC password (default: verseye1@3)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

# ---------------------------------------------------------------------------
# Path — project root is 4 levels up from services/model/cfgs/test/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ---------------------------------------------------------------------------
# Config — must be set before GrpcConfig singleton is created
# ---------------------------------------------------------------------------
VIDEO_SOURCE: str | int = os.getenv(
    "GAIT_VIDEO",
    r"https://ai-public-videos.s3.us-east-2.amazonaws.com/Inferenced+Videos/gallery.mp4",
)
GRPC_SERVER: str = os.getenv("GAIT_GRPC_SERVER", "ai-srv.qbscocloud.net:30812")
GRPC_USERNAME: str = os.getenv("GAIT_GRPC_USERNAME", "verseye")
GRPC_PASSWORD: str = os.getenv("GAIT_GRPC_PASSWORD", "verseye1@3")
GRPC_TIMEOUT: float = 30.0

# Inject "gait_grpc" entry so GrpcConfig can resolve the stage 3 endpoint.
_grpc_raw = os.environ.get("GRPC", "")
if "gait_grpc" not in _grpc_raw:
    _gait_entry = f"gait_grpc,{GRPC_SERVER},{GRPC_USERNAME},{GRPC_PASSWORD}"
    os.environ["GRPC"] = f"{_grpc_raw}|{_gait_entry}" if _grpc_raw else _gait_entry

from configs.grpc_config import GrpcConfig  # noqa: E402

GrpcConfig.reset()

from constants.detections_constant import (  # noqa: E402
    BBOX,
    GAIT_CONFIDENCE,
    GAIT_MATCHED_ID,
    GAIT_P2_ERROR,
    SILHOUETTE_FRAME_COUNT,
    SILHOUETTE_SEQUENCE_READY,
    TRACK_ID,
)
from services.common.gait.silhouette_preprocess import SilMode  # noqa: E402
from services.common.models.pipe_structure import PipeStructure  # noqa: E402
from services.model.cfgs.model_pipeline import ModelPipeline  # noqa: E402
from services.model.cfgs.stage1.general_object_detection_tracker import (  # noqa: E402
    GeneralObjectTrackerStage1,
)
from services.model.cfgs.stage2.paddle_seg_silhoutte import (  # noqa: E402
    PaddleSegSilhouetteStage2,
)
from services.model.cfgs.stage3.gait_grpc_stage3 import GaitGrpcStage3  # noqa: E402

# ---------------------------------------------------------------------------
# Weights directory
# ---------------------------------------------------------------------------
_INFERENCED_WEIGHTS_DIR = Path(__file__).resolve().parents[3] / "inferenced_weights"


def _ensure_yolo_path(filename: str = "gait_detector_v1.pt") -> Path:
    path = _INFERENCED_WEIGHTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"YOLO weights not found: {path}")
    return path


def _ensure_onnx_path() -> Path:
    path = _INFERENCED_WEIGHTS_DIR / "humanseg_model" / "humanseg_192.onnx"
    if not path.exists():
        raise FileNotFoundError(
            f"PPHumanSeg ONNX not found: {path}\n"
            "Convert once with paddle2onnx (see paddle_seg_silhoutte.py docstring)."
        )
    return path


# ---------------------------------------------------------------------------
# Pipeline constants
# ---------------------------------------------------------------------------
STAGE1_ID: str = "gait_detector_v1"
STAGE2_ID: str = "gait_paddle_seg_v1"
STAGE3_ID: str = "gait_grpc_v1"

SIL_MODE: str = SilMode.GAITBASE
MIN_FRAMES: int = 100
MAX_FRAMES: int = 100
SEND_EVERY: int = 50

MAX_DISPLAY_W: int = 480

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _color(det: dict[str, Any]) -> tuple[int, int, int]:
    if det.get(GAIT_P2_ERROR):
        return (0, 0, 200)
    matched = det.get(GAIT_MATCHED_ID, "")
    if matched and matched != "unknown":
        return (0, 200, 0)
    if matched == "unknown":
        return (0, 140, 255)
    return (255, 140, 0)


def _label(det: dict[str, Any]) -> str:
    tid = det.get(TRACK_ID, -1)
    err = det.get(GAIT_P2_ERROR, "")
    if err:
        return f"tid={tid} P2 offline"
    matched = det.get(GAIT_MATCHED_ID, "")
    frames = det.get(SILHOUETTE_FRAME_COUNT, 0)
    if not matched:
        return f"tid={tid} [{frames}/{MIN_FRAMES}]"
    conf_pct = int(det.get(GAIT_CONFIDENCE, 0.0) * 100)
    return f"{matched}  {conf_pct}%  (tid={tid})"


def _draw_detections(frame: np.ndarray, detections: list[dict[str, Any]]) -> None:
    for det in detections:
        bbox = det.get(BBOX)
        if not bbox:
            continue
        x1, y1, x2, y2 = map(int, bbox)
        color = _color(det)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            _label(det),
            (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )


def _overlay_stats(
    canvas: np.ndarray,
    frame_idx: int,
    total_seqs: int,
    active_tracks: int,
    p2_online: bool,
) -> None:
    p2 = "online" if p2_online else "offline"
    info = f"frame={frame_idx}  tracks={active_tracks}" f"  seqs_sent={total_seqs}  P2={p2}"
    cv2.putText(
        canvas,
        info,
        (8, canvas.shape[0] - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (200, 200, 200),
        1,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(video_source: str | int = VIDEO_SOURCE) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    yolo_path = _ensure_yolo_path()
    onnx_path = _ensure_onnx_path()

    print(f"Device      : {device}")
    print(f"YOLO        : {yolo_path.name}")
    print(f"Seg ONNX    : {onnx_path.name}")
    print(f"Source      : {video_source}")
    print(f"gRPC server : {GRPC_SERVER}")
    print("─" * 60)

    # ── Stage instances ──────────────────────────────────────────────────────
    stage1 = GeneralObjectTrackerStage1(
        model_path=str(yolo_path),
        model_id=STAGE1_ID,
        tag=["person"],
        device=device,
    )
    stage2 = PaddleSegSilhouetteStage2(
        model_path=str(onnx_path),
        model_id=STAGE2_ID,
        tag=["person"],
        min_frames=MIN_FRAMES,
        max_frames=MAX_FRAMES,
        send_every=SEND_EVERY,
        sil_mode=SIL_MODE,
    )
    stage3 = GaitGrpcStage3(
        model_id=STAGE3_ID,
        timeout=GRPC_TIMEOUT,
        source_video=Path(str(video_source)).name if isinstance(video_source, str) else "camera",
    )

    # ── Pipeline wiring ──────────────────────────────────────────────────────
    # Stage 2 and Stage 3 both depend on Stage 1's detection list.
    # ModelPipeline passes results_dict[STAGE1_ID] as prev_results to each;
    # both mutate it in-place so enrichments accumulate.
    pipeline = ModelPipeline(
        model_configs=[
            PipeStructure(model_id=STAGE1_ID, model=stage1, lead_by="", order=0),
            PipeStructure(model_id=STAGE2_ID, model=stage2, lead_by=STAGE1_ID, order=1),
            PipeStructure(model_id=STAGE3_ID, model=stage3, lead_by=STAGE1_ID, order=2),
        ]
    )
    print("Pipeline ready (Stage 1 → Stage 2 → Stage 3).\n")

    # ── Open video ───────────────────────────────────────────────────────────
    source: str | int = video_source
    try:
        source = int(video_source)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        pass

    cap = cv2.VideoCapture(source)  # type: ignore[arg-type]
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video source: {video_source}")

    frame_idx = 0
    total_seqs = 0
    p2_ever_online = False

    print("Running — press 'q' or ESC to quit.\n")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("End of stream.")
                break
            frame_idx += 1

            detections: list[dict[str, Any]] = pipeline(frame)

            active_tids: set[int] = set()
            for det in detections:
                tid = det.get(TRACK_ID, -1)
                active_tids.add(tid)

                if det.get(SILHOUETTE_SEQUENCE_READY):
                    total_seqs += 1
                    if not det.get(GAIT_P2_ERROR):
                        p2_ever_online = True

            _draw_detections(frame, detections)
            _overlay_stats(frame, frame_idx, total_seqs, len(active_tids), p2_ever_online)

            dh, dw = frame.shape[:2]
            if dw > MAX_DISPLAY_W:
                frame = cv2.resize(frame, (MAX_DISPLAY_W, int(dh * MAX_DISPLAY_W / dw)))

            cv2.imshow("Gait Pipeline (S1+S2+S3)", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break

    finally:
        cap.release()
        stage3.close()
        cv2.destroyAllWindows()

    print(f"\nDone.  frames={frame_idx}  sequences_sent={total_seqs}")


if __name__ == "__main__":
    src: str | int = sys.argv[1] if len(sys.argv) > 1 else VIDEO_SOURCE
    try:
        src = int(src)
    except (ValueError, TypeError):
        pass
    main(src)
