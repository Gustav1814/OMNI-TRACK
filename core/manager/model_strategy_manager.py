from typing import Any

from constants.kpi_names_constant import KPI_NAME_BLINK_DROWSINESS
from services.loaders.detection_data_loader import DetectionDataLoader
from services.loaders.idata_loader import IDataLoader
from services.loaders.pose_data_loader import PoseDataLoader
from services.loaders.pose_data_loader_with_activity import PoseDataLoaderWithActivity
from services.loaders.segmentation_data_loader import SegmentationDataLoader
from services.loaders.segpose_data_loader import SegPoseDataLoader
from services.visualization.detection_annotation_renderer import (
    DetectionAnnotationRenderer,
)
from services.visualization.detection_obb_annotation_renderer import (
    DetectionObbAnnotationRenderer,
)
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer
from services.visualization.motion_trail_heatmap_renderer import MotionTrailHeatmapRenderer
from services.visualization.pose_annotation_renderer import PoseAnnotationRenderer
from services.visualization.pose_direction_annotation_renderer import (
    PoseDirectionAnnotationRenderer,
)
from services.visualization.pose_eyes_annotation_renderer import BlinkDrowsinessAnnotationRenderer
from services.visualization.segmentation_annotation_renderer import (
    SegmentationAnnotationRenderer,
)
from services.visualization.segpose_annotation_renderer import SegPoseAnnotationRenderer


class ModelStrategyFactory:
    @staticmethod
    def get_data_loader(model_id: str, **kwargs: Any) -> IDataLoader:
        if "activity" in ",".join(kwargs.keys()):
            return PoseDataLoaderWithActivity(**kwargs)
        if "_pe_" in model_id or "blink" in model_id or "drowse" in model_id:
            return PoseDataLoader()
        if "_segpose_" in model_id:
            return SegPoseDataLoader()
        if "_seg_" in model_id:
            return SegmentationDataLoader()
        return DetectionDataLoader()

    @staticmethod
    def get_annotation_renderers(
        model_id: str, kpi_name: str | None = None
    ) -> dict[str, IAnnotationRenderer]:
        # KPI override first
        if kpi_name == KPI_NAME_BLINK_DROWSINESS:
            return {"primary_annotator": BlinkDrowsinessAnnotationRenderer()}

        if "_heatmapper_" in model_id:
            return {
                "secondary_annotator": MotionTrailHeatmapRenderer(),
                "primary_annotator": DetectionAnnotationRenderer(),
            }

        if "_pe_" in model_id:
            return {"primary_annotator": PoseAnnotationRenderer()}

        if "_segpose_" in model_id:
            return {"primary_annotator": SegPoseAnnotationRenderer()}

        if "_seg_" in model_id:
            return {"primary_annotator": SegmentationAnnotationRenderer()}

        if "_obb_" in model_id:
            return {"primary_annotator": DetectionObbAnnotationRenderer()}

        if "pass_by_traffic" in model_id:
            return {"primary_annotator": PoseDirectionAnnotationRenderer()}

        return {"primary_annotator": DetectionAnnotationRenderer()}

    # @staticmethod
    # def get_annotation_renderer(
    #     model_id: str, kpi_name: str | None = None
    # ) -> IAnnotationRenderer:
    #     if "_heatmapper_" in model_id:
    #         return MotionTrailHeatmapRenderer()
    #     # Force pose renderer for blink/drowsiness KPI
    #     if kpi_name == KPI_NAME_BLINK_DROWSINESS:
    #         return BlinkDrowsinessAnnotationRenderer()
    #     if "_pe_" in model_id:
    #         return PoseAnnotationRenderer()
    #     if "_seg_" in model_id:
    #         return SegmentationAnnotationRenderer()
    #     if "_obb_" in model_id:
    #         return DetectionObbAnnotationRenderer()
    #     if "pass_by_traffic" in model_id:
    #         return PoseDirectionAnnotationRenderer()
    #     return DetectionAnnotationRenderer()
