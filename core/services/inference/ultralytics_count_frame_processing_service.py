# services/frame_processing_service.py
import asyncio
import os
import tempfile
import urllib.request
from datetime import UTC, datetime
from typing import Any, cast

import cv2
import numpy as np

from constants.camera_direction import LEFT_TO_RIGHT, RIGHT_TO_LEFT
from constants.detections_constant import (
    ALL,
    DEFAULT_SNAPSHOT_TAGS,
    FOLLOWED_TO,
    PERSON,
    REGION_NAME,
)
from constants.region_names import PRESERVED_DATA
from core.container import container
from dtos.common.enums.region_type import RegionType
from dtos.request.request_register_use_case import Region
from services.common.models.yolo_models import KPIResult, YoloCountItem
from services.core.interfaces.ilogger_service import ILoggerService
from services.inferenced_services.detection_plugins import (
    regions_for_annotation_pipeline,
    run_detection_plugins,
)
from services.inferenced_services.interfaces.iframe_processing_service import (
    IFrameProcessingService,
)
from services.interfaces.imodel_service import IModelService
from services.managers.activity_matching_manager import ActivityMatching
from services.managers.annotation_manager import AnnotationManager
from services.managers.color_manager import ColorManager
from services.managers.kpi_manager import KPIManager
from services.managers.model_strategy_manager import ModelStrategyFactory
from services.visualization.interface.iannotation_renderer import IAnnotationRenderer
from utils.data_format_converters import encode_frame_to_base64


class UltralyticsCountFrameProcessingService(IFrameProcessingService):
    @staticmethod
    def _download_http_url_to_temp_file(url: str) -> str:
        """Fetch a remote video to a temp path so OpenCV reads from disk (HTTPS streaming is often unreliable)."""
        path_only = url.split("?", 1)[0].lower()
        suffix = ".mp4"
        for ext in (".mp4", ".webm", ".avi", ".mov", ".mkv", ".mpeg", ".mpg"):
            if path_only.endswith(ext):
                suffix = ext
                break
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ais-handler/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(path, "wb") as out:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
        except Exception:
            if os.path.isfile(path):
                os.unlink(path)
            raise
        return path

    def __init__(self, model_id: str | None = None):
        self.logger: ILoggerService = container.resolve(ILoggerService)
        self.kpi_manager: KPIManager = container.resolve(KPIManager)
        self.model_id = model_id
        self.camera_direction = ""
        self.color_manager = ColorManager()  # <--- ColorManager is created here
        self._last_kpi_result: KPIResult | None = None  # For stateful KPIs like attendance

    @staticmethod
    def _parse_tags(tag_string: str) -> list[str]:
        """Parse comma-separated tag string into list of tags"""
        if not tag_string:
            return []

        tags = [tag.strip().lower() for tag in tag_string.split(",") if tag.strip()]
        return tags if tags else DEFAULT_SNAPSHOT_TAGS

    async def detect_camera_direction(self, video_path: str, sample_frames: int = 30) -> str:
        """
        Analyze initial frames to determine camera movement direction
        """
        cap = cv2.VideoCapture(video_path)
        movements: list[float] = []
        prev_gray = None

        print("Detecting camera direction...")

        frame_count = 0
        while frame_count < sample_frames:
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                h, w = gray.shape[:2]
                flow_placeholder = np.zeros((h, w, 2), dtype=np.float32)
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray,
                    gray,
                    flow_placeholder,
                    pyr_scale=0.5,
                    levels=3,
                    winsize=15,
                    iterations=3,
                    poly_n=5,
                    poly_sigma=1.2,
                    flags=0,
                )
                avg_flow_x = float(np.mean(flow[:, :, 0]))
                movements.append(avg_flow_x)

            prev_gray = gray
            frame_count += 1

        cap.release()

        avg_movement = np.mean(movements)

        if avg_movement > 0.5:
            self.camera_direction = RIGHT_TO_LEFT
            print(f"Camera Direction: RIGHT to LEFT (avg movement: {avg_movement:.2f})")
        elif avg_movement < -0.5:
            self.camera_direction = LEFT_TO_RIGHT
            print(f"Camera Direction: LEFT to RIGHT (avg movement: {avg_movement:.2f})")
        else:
            self.camera_direction = RIGHT_TO_LEFT
            print("Camera Direction: Unclear, defaulting to RIGHT to LEFT")
        return self.camera_direction

    async def process_frame_complete(
        self,
        frame: np.ndarray,
        regions: list[Region],
        model_service: IModelService,
        kpi_name: str,
        draw_annotations: bool = True,
        return_base64: bool = False,
        tag_at_reid: str | None = None,
        _is_global_reid: bool = False,
        tag: str = "person",
        snapshots_at_tag: str = "wro vng_placed",
        **kwargs: Any,
    ) -> YoloCountItem:
        """If return_base64 is True (e.g. /verify), populate frame_base64; background jobs keep ndarray only."""
        try:
            regions_draw = regions_for_annotation_pipeline(regions)
            tags_to_count = self._parse_tags(tag)
            tags_to_snapshot = self._parse_tags(snapshots_at_tag)
            tags_to_reid = self._parse_tags(tag_at_reid) if tag_at_reid else []
            data: dict[str, Any] = {}
            if "activity" in kpi_name:
                data["enable_activity_matching"] = True
                data["activities_to_detect"] = list(ActivityMatching.ACTIVITIES.keys())
            # Get appropriate data loader based on model
            data_loader = ModelStrategyFactory.get_data_loader(model_service.model_id, **data)

            # Use tracking for attendance KPI to get track_id

            if model_service.model is None:
                raise RuntimeError("Model not initialized")
            results = model_service.model(frame)
            detections = data_loader.load(results, frame)
            detections = run_detection_plugins(detections, regions, frame)

            current_time = kwargs.get("current_time", datetime.now(UTC))
            enhanced_detections = await self.check_detection_within_regions(
                detections, model_service, regions_draw
            )
            # TODO : need to commit the line below after debugging
            # print("\n Detected :",enhanced_detections,"\n")

            # ==============================
            # ANNOTATION RENDERING
            # ==============================

            base_frame = frame.copy()

            if draw_annotations:
                # Draw regions once (hide* mask regions are not drawn)
                for reg in regions_draw:
                    self.draw_region(base_frame, reg, self.color_manager)

                # Decide which annotator to use based on optional visualization_type + KPI
                visualization_type = kwargs.get("visualization_type")
                annotator = AnnotationManager.get_renderer(
                    model_id=model_service.model_id,
                    visualization_type=visualization_type,
                    kpi_name=kpi_name,
                )

                from services.visualization.master_annotation_renderer import (
                    MasterAnnotationRenderer,
                )

                master_renderer = MasterAnnotationRenderer()
                current_timestamp = datetime.now(UTC)

                annotated_frame = await self.draw_model_detections(
                    base_frame,
                    annotator,
                    enhanced_detections,
                    kwargs,
                    regions_draw,
                )

                annotated_frame = master_renderer.draw_timestamp_on_frame(
                    annotated_frame,
                    current_timestamp,
                )
            else:
                annotated_frame = frame.copy()

            # ==============================
            # KPI CALCULATION
            # ==============================

            preserved_data = kwargs.get(PRESERVED_DATA)
            if preserved_data is None and hasattr(self, "_last_kpi_result"):
                kwargs[PRESERVED_DATA] = self._last_kpi_result

            kpi_results = await self.kpi_manager.calculate_kpi(
                kpi_name,
                frame,
                enhanced_detections,
                tags=tags_to_count,
                snapshot_tags=tags_to_snapshot,
                tags_reid=tags_to_reid,
                **{**kwargs, "current_time": current_time},
            )

            if kpi_results:
                self._last_kpi_result = kpi_results

            # ==============================
            # FRAME ASSIGNMENT
            # ==============================

            result = YoloCountItem(
                detection=enhanced_detections,
                kpi_results=kpi_results,
                total_detections=len(enhanced_detections),
                regions_count=len(regions_draw) if regions_draw else 0,
                timestamp=datetime.now(UTC).isoformat(),
                processing_success=True,
            )

            # Single annotated frame (no secondary/overlay concept)
            result.annotated_frame = annotated_frame

            # ==============================
            # BASE64 HANDLING (DISPLAY)
            # ==============================

            if return_base64:
                result.frame_base64 = encode_frame_to_base64(annotated_frame)

            # TODO : Delete the line below after use
            # bboxes_1 = [d["bbox"] for d in result.detection]
            # region_names = [d["region_name"] for d in result.detection]
            #
            # print("\n result:",bboxes_1," Region name:",region_names,"\n")

            return result

        except Exception as e:
            await self.logger.error(f"Frame processing failed: {str(e)}")
            return YoloCountItem(
                regions_count=len(regions_for_annotation_pipeline(regions or [])),
                timestamp=datetime.now(UTC).isoformat(),
                processing_success=False,
                error_message=str(e),
                detection=[],
                kpi_results=None,
                total_detections=0,
            )

    async def check_detection_within_regions(
        self, detections: list[dict[str, Any]], model_service: IModelService, regions: list[Region]
    ) -> list[dict[str, Any]] | list[dict[str, bool | str]]:
        # Check detections against regions
        enhanced_detections = []

        if regions:
            # If regions are defined, use the async service
            enhanced_detections = await model_service.check_detections_in_regions(
                detections, regions
            )
        else:
            # Otherwise, loop through each detection and add default region info
            for d in detections:
                if d.get(FOLLOWED_TO, None) is not None:
                    d[FOLLOWED_TO] = await self.check_detection_within_regions(
                        d[FOLLOWED_TO], model_service, regions
                    )

                detection_with_region = {**d, REGION_NAME: "global"}
                enhanced_detection = await model_service.check_detections_in_regions(
                    [detection_with_region], regions
                )
                enhanced_detections.append(enhanced_detection[-1])

        return enhanced_detections

    async def draw_model_detections(
        self,
        annotated_frame: np.ndarray,
        annotation_renderer: IAnnotationRenderer,
        enhanced_detections: list[dict[str, Any]] | list[dict[str, bool | str]],
        kwargs: dict[str, Any],
        regions: list[Region],
    ) -> np.ndarray:
        for detection in enhanced_detections:
            followed_to = detection.get(FOLLOWED_TO)
            if isinstance(followed_to, list):
                annotated_frame = await self.draw_model_detections(
                    annotated_frame,
                    annotation_renderer,
                    cast("list[dict[str, Any]] | list[dict[str, bool | str]]", followed_to),
                    kwargs,
                    regions,
                )
                return annotated_frame
            annotated_frame = annotation_renderer.render(
                annotated_frame, detection, regions, self.color_manager, **kwargs
            )
        return annotated_frame

    @staticmethod
    def _draw_summary_text(
        frame: np.ndarray, _detections: list[dict[str, Any]], _regions: list[Region]
    ) -> None:
        """Draw summary information on the frame"""
        # in_region_count = sum(1 for d in detections if d.get(ITEM_AT_CURRENT_REGION_NAME, False))
        # total_count = len(detections)

        summary_lines: list[str] = [
            # f"Total Detections: {total_count}",
            # f"Poses Detected: {sum(1 for d in detections if KEYPOINTS in d and len(d[KEYPOINTS]) > 0)}",
            # f"In Region: {in_region_count}",
            # f"Regions: {len(regions)}",
        ]

        text_height = 25 * len(summary_lines)
        cv2.rectangle(frame, (10, 10), (300, text_height + 20), (0, 0, 0), -1)

        for i, line in enumerate(summary_lines):
            y_pos = 30 + i * 25
            cv2.putText(frame, line, (20, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    async def process_video_frame(
        self,
        url: str,
        regions: list[Region],
        model_service: IModelService,
        kpi_name: str,
        single_frame: bool = True,  # noqa: ARG002
        tag: str = PERSON,
        snapshots_at_tag: str = ALL,
        tag_at_reid: str | None = "",
        draw_annotations: bool = True,
        return_base64: bool = False,
        **kwargs: Any,
    ) -> YoloCountItem | None:
        """Grab one frame from url and run process_frame_complete; use return_base64=True for /verify only."""
        self.color_manager.clear_tracked_colors()  # Reset colors for new video
        tmp_path: str | None = None
        cap = cv2.VideoCapture(url)

        try:
            opened = cap.isOpened()
            ret, frame = cap.read() if opened else (False, None)

            if url.lower().startswith(("http://", "https://")) and (not opened or not ret):
                cap.release()
                tmp_path = await asyncio.to_thread(self._download_http_url_to_temp_file, url)
                cap = cv2.VideoCapture(tmp_path)
                ret, frame = cap.read()

            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            regions = await model_service.update_payload_for_line(
                url, frame_height, frame_width, regions
            )

            if not ret:
                raise Exception(f"Could not capture frame from URL: {url}")
            if frame is None:
                raise Exception(f"Could not capture frame from URL: {url}")

            result = await self.process_frame_complete(
                frame=frame,
                regions=regions,
                draw_annotations=draw_annotations,
                return_base64=return_base64,
                tag_at_reid=tag_at_reid,
                tag=tag,
                snapshots_at_tag=snapshots_at_tag,
                model_service=model_service,
                kpi_name=kpi_name,
                **kwargs,
            )

            return result

        finally:
            cap.release()
            if tmp_path and os.path.isfile(tmp_path):
                os.unlink(tmp_path)

    @staticmethod
    def _validate_polygon_region(i: int, region: Region, errors: list[str]) -> None:
        if region.points is None:
            errors.append(f"Region {i}: Polygon missing 'points' field")
        elif len(region.points) < 3:
            errors.append(f"Region {i}: Polygon must have at least 3 points")

    @staticmethod
    def _validate_bounding_box_region(i: int, region: Region, errors: list[str]) -> None:
        if region.coordinates is None:
            errors.append(f"Region {i}: Bounding box missing 'coordinates' field")
            return
        coords = region.coordinates
        if coords.x_min >= coords.x_max:
            errors.append(f"Region {i}: x_min must be less than x_max")
        if coords.y_min >= coords.y_max:
            errors.append(f"Region {i}: y_min must be less than y_max")

    @staticmethod
    def _validate_line_points_iter(
        i: int, lp: list[Any], errors: list[str], warnings: list[str]
    ) -> None:
        for j, point in enumerate(lp):
            if point is None:
                errors.append(f"Region {i}, Point {j}: Must be a dictionary")
            elif point.x is None or point.y is None:
                errors.append(f"Region {i}, Point {j}: Invalid point structure (needs x, y)")
            else:
                try:
                    x = float(point.x)
                    y = float(point.y)
                    if x < 0 or y < 0:
                        warnings.append(f"Region {i}, Point {j}: Negative coordinates detected")
                except (TypeError, ValueError):
                    errors.append(f"Region {i}, Point {j}: Coordinates must be numeric values")

    @staticmethod
    def _warn_identical_line_endpoints(
        i: int, lp: list[Any], errors: list[str], warnings: list[str]
    ) -> None:
        if len(lp) == 2 and not errors:
            p1 = lp[0]
            p2 = lp[1]
            if p1.x == p2.x and p1.y == p2.y:
                warnings.append(f"Region {i}: Line has identical start and end points")

    @staticmethod
    def _validate_line_region(
        i: int, region: Region, errors: list[str], warnings: list[str]
    ) -> None:
        lp = region.line_points
        if lp is None:
            errors.append(f"Region {i}: Line missing 'line_points' field")
            return
        if not isinstance(lp, list):
            errors.append(f"Region {i}: Line points must be a list")
            return
        if len(lp) < 2:
            errors.append(f"Region {i}: Line must have at least 2 points")
            return
        if len(lp) > 2:
            warnings.append(
                f"Region {i}: Line has more than 2 points; only first and last will be used"
            )
            return

        UltralyticsCountFrameProcessingService._validate_line_points_iter(i, lp, errors, warnings)
        UltralyticsCountFrameProcessingService._warn_identical_line_endpoints(
            i, lp, errors, warnings
        )

    def validate_regions(self, regions: list[Region]) -> dict[str, Any]:
        """Validate region definitions"""
        errors: list[str] = []
        warnings: list[str] = []

        if not regions:
            return {"valid": True, "errors": [], "warnings": ["No regions provided"]}

        for i, region in enumerate(regions):
            if region.type is None:
                errors.append(f"Region {i}: Missing 'type' field")
                continue

            if region.name is None:
                warnings.append(f"Region {i}: Missing 'name' field")

            region_type = region.type

            if region_type == RegionType.POLYGON.value:
                self._validate_polygon_region(i, region, errors)
            elif region_type == RegionType.BOUNDING_BOX.value:
                self._validate_bounding_box_region(i, region, errors)
            elif region_type == RegionType.Line.value:
                self._validate_line_region(i, region, errors, warnings)
            else:
                errors.append(f"Region {i}: Unsupported region type '{region_type}'")

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    async def reannotate_frame(
        self,
        raw_frame: np.ndarray,
        detections: list[dict[str, Any]],
        regions: list[Region],
        visualization_type: str,
        kpi_name: str | None = None,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> np.ndarray | None:
        """
        Re-annotate a raw frame using the given visualization_type.
        Used by stream frames API when visualization_type is provided.
        """
        if raw_frame is None or detections is None:
            return None
        try:
            annotator = AnnotationManager.get_renderer(
                model_id=model_id or "",
                visualization_type=visualization_type,
                kpi_name=kpi_name or "",
            )
            frame = raw_frame.copy()
            regions_draw = regions_for_annotation_pipeline(list(regions or []))
            for reg in regions_draw:
                self.draw_region(frame, reg, self.color_manager)
            annotated = await self.draw_model_detections(
                frame,
                annotator,
                detections,
                kwargs,
                regions_draw,
            )
            from services.visualization.master_annotation_renderer import MasterAnnotationRenderer

            master_renderer = MasterAnnotationRenderer()
            return master_renderer.draw_timestamp_on_frame(annotated, datetime.now(UTC))
        except Exception as e:
            await self.logger.error(f"Re-annotation failed: {e}")
            return None
