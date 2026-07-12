from enum import StrEnum
from typing import cast

from services.visualization.detection_annotation_renderer import DetectionAnnotationRenderer
from services.visualization.detection_obb_annotation_renderer import DetectionObbAnnotationRenderer
from services.visualization.face_landmarks_annotation_renderer import (
    FaceLandmarksAnnotationRenderer,
)
from services.visualization.gait_annotator_renderer import GaitAnnotatorRenderer
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer
from services.visualization.motion_trail_heatmap_renderer import MotionTrailHeatmapRenderer
from services.visualization.pose_annotation_renderer import PoseAnnotationRenderer
from services.visualization.pose_direction_annotation_renderer import (
    PoseDirectionAnnotationRenderer,
)
from services.visualization.pose_eyes_annotation_renderer import BlinkDrowsinessAnnotationRenderer
from services.visualization.segmentation_annotation_renderer import SegmentationAnnotationRenderer
from services.visualization.segpose_annotation_renderer import SegPoseAnnotationRenderer

# ============================================================
# Visualization Types (single contract)
# ============================================================


class VisualizationType(StrEnum):
    DETECTION = "detection"
    SEGMENTATION = "segmentation"
    POSE = "pose"
    POSE_FACE = "pose_face"
    POSE_DIRECTION = "pose_direction"
    OBB = "obb"
    BLINK_DROWSINESS = "blink_drowsiness"
    HEAT_MAP = "heat_map"
    SEG_POSE = "segpose"
    GAIT = "gait"


# ============================================================
# Constants
# ============================================================

KPI_NAME_BLINK_DROWSINESS = "blink_drowsiness"


# ============================================================
# Simple rule tables (easy to scale)
# ============================================================

# Explicit visualization_type string → VisualizationType
VISUALIZATION_ALIASES = {
    "pose": VisualizationType.POSE,
    "pose_face": VisualizationType.POSE_FACE,
    "segmentation": VisualizationType.SEGMENTATION,
    "obb": VisualizationType.OBB,
    "pose_direction": VisualizationType.POSE_DIRECTION,
    "direction": VisualizationType.POSE_DIRECTION,
    "heatmap": VisualizationType.HEAT_MAP,
    "heat_map": VisualizationType.HEAT_MAP,
    "blink": VisualizationType.BLINK_DROWSINESS,
    "blink_drowsiness": VisualizationType.BLINK_DROWSINESS,
    "segpose": VisualizationType.SEG_POSE,
}

# KPI → VisualizationType
KPI_TO_VISUALIZATION = {
    KPI_NAME_BLINK_DROWSINESS: VisualizationType.BLINK_DROWSINESS,
}

# model_id substring → VisualizationType
# NOTE: "gait_" must come before "_seg_" — gait model IDs contain "_seg_" in their
# stage name (e.g. "gait_paddle_seg_v1") and would otherwise match the wrong renderer.
MODEL_ID_RULES = {
    "gait_": VisualizationType.GAIT,
    "_seg_": VisualizationType.SEGMENTATION,
    "_segpose_": VisualizationType.SEG_POSE,
    "_pe_": VisualizationType.POSE,
    "face_landmarks": VisualizationType.POSE_FACE,
    "_obb_": VisualizationType.OBB,
    "heatmapper": VisualizationType.HEAT_MAP,
    "pass_by_traffic": VisualizationType.POSE_DIRECTION,
}


# ============================================================
# Visualization Resolver (ONLY decision point)
# ============================================================


def resolve_visualization_type(
    *,
    model_id: str | None = None,
    kpi_name: str | None = None,
    visualization_type: str | VisualizationType | None = None,
) -> VisualizationType:
    """
    Resolution priority:
    1. Explicit visualization_type
    2. KPI
    3. model_id
    4. Default
    """

    # 1) Explicit override
    if visualization_type:
        if isinstance(visualization_type, VisualizationType):
            return visualization_type

        viz = visualization_type.strip().lower()
        return VISUALIZATION_ALIASES.get(viz, VisualizationType.DETECTION)

    # 2) KPI
    if kpi_name in KPI_TO_VISUALIZATION:
        return KPI_TO_VISUALIZATION[kpi_name]

    # 3) model_id
    if model_id:
        mid = model_id.lower()
        for key, viz_type in MODEL_ID_RULES.items():
            if key in mid:
                return viz_type

    # 4) Default
    return VisualizationType.DETECTION


# ============================================================
# Annotation Renderer Factory
# ============================================================


RENDERER_MAP = {
    VisualizationType.DETECTION: DetectionAnnotationRenderer,
    VisualizationType.SEGMENTATION: SegmentationAnnotationRenderer,
    VisualizationType.POSE: PoseAnnotationRenderer,
    VisualizationType.POSE_DIRECTION: PoseDirectionAnnotationRenderer,
    VisualizationType.POSE_FACE: FaceLandmarksAnnotationRenderer,
    VisualizationType.OBB: DetectionObbAnnotationRenderer,
    VisualizationType.BLINK_DROWSINESS: BlinkDrowsinessAnnotationRenderer,
    VisualizationType.HEAT_MAP: MotionTrailHeatmapRenderer,
    VisualizationType.SEG_POSE: SegPoseAnnotationRenderer,
    VisualizationType.GAIT: GaitAnnotatorRenderer,
}


# ============================================================
# Public API
# ============================================================


class AnnotationManager:
    """
    Simple, scalable renderer resolver.

    Callers provide:
    - model_id (optional)
    - kpi_name (optional)
    - visualization_type (optional)

    They get:
    - exactly one IAnnotationRenderer
    """

    @staticmethod
    def get_renderer(
        *,
        model_id: str,
        kpi_name: str,
        visualization_type: str | VisualizationType | None = None,
    ) -> IAnnotationRenderer:
        viz_type = resolve_visualization_type(
            model_id=model_id,
            kpi_name=kpi_name,
            visualization_type=visualization_type,
        )

        return cast(IAnnotationRenderer, RENDERER_MAP[viz_type]())
