# services/trackers/yolo_trackers.py
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from constants.detections_constant import (
    BBOX,
    CENTRE,
    CLASS_ID,
    CLASS_NAME,
    CONFIDENCE,
    DETECT_TRACK_ID,
    MODEL_ID,
)
from services.trackers.base_tracker import BaseTracker

# Matches ultralytics `get_cfg(cfg=...)` stubs (`str | Path | dict | SimpleNamespace`);
# `check_yaml` is typed as returning `str | list`, so callers cast at the boundary.
_UltralyticsCfgSource = str | Path | dict[str, Any] | SimpleNamespace


def _parse_raw_det_tensor_list(raw_dets: list, classes: list | None) -> list:
    """Convert raw detection dicts/arrays into a flat list for torch tensor creation."""
    det_tensor_list = []
    for d in raw_dets:
        if isinstance(d, dict):
            bbox_raw = d.get(BBOX, d.get("bbox", [0, 0, 0, 0]))
            bbox = (
                bbox_raw
                if isinstance(bbox_raw, list | tuple) and len(bbox_raw) >= 4
                else [0, 0, 0, 0]
            )
            _cid = d.get(CLASS_ID, d.get("class_id", -1))
            cls_id = int(_cid) if _cid is not None else -1
            _conf = d.get(CONFIDENCE, d.get("confidence", 1.0))
            conf_val = float(_conf) if _conf is not None else 1.0
        else:
            bbox = d[:4]
            conf_val = float(d[4]) if len(d) > 4 else 1.0
            cls_id = int(d[5]) if len(d) > 5 else -1

        if classes is not None and cls_id not in classes:
            continue
        det_tensor_list.append(
            [
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
                conf_val,
                float(cls_id),
            ]
        )
    return det_tensor_list


def _build_tracked_detections(
    tracker_output: Any, tag: list, model_id: str, model_names: dict[int, str] | None = None
) -> list[dict[str, Any]]:
    """Convert raw tracker output rows into structured detection dicts."""
    tracked_detections = []
    for tr in tracker_output:
        if len(tr) < 7:
            continue
        x1, y1, x2, y2, track_id, conf_val, cls_id = tr[:7]

        cls_name = str(int(cls_id))
        if model_names is not None and int(cls_id) in model_names:
            cls_name = model_names[int(cls_id)]

        if not (cls_name.lower() in tag or "all" in tag):
            continue

        det_dict: dict[str, Any] = {
            BBOX: [int(x1), int(y1), int(x2), int(y2)],
            CONFIDENCE: float(conf_val),
            CLASS_ID: int(cls_id),
            CLASS_NAME: cls_name.lower(),
            MODEL_ID: model_id,
            DETECT_TRACK_ID: int(track_id),
        }
        det_dict[CENTRE] = [
            int((det_dict[BBOX][0] + det_dict[BBOX][2]) / 2),
            int((det_dict[BBOX][1] + det_dict[BBOX][3]) / 2),
        ]
        tracked_detections.append(det_dict)
    return tracked_detections


class ByteTracker(BaseTracker):
    """
    ByteTrack implementation using YOLO's built-in tracker.
    ByteTrack is fast and works well for real-time applications.
    """

    def __init__(self):
        super().__init__(tracker_name="bytetrack")
        self._internal_tracker = None

    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Track using ByteTrack algorithm.

        Args:
            frame: Input frame
            model: YOLO model object (can be None if detections are provided in kwargs)
            device: Device to run on
            persist: Persist tracks across frames
            conf: Confidence threshold
            **kwargs: Additional parameters (model_id, tag, detections, classes)

        Returns:
            List of detections with tracking IDs
        """
        model_id = kwargs.get("model_id", "unknown")
        tag = kwargs.get("tag", ["all"])
        classes = kwargs.get("classes", None)

        if hasattr(model, "track") and model is not None:
            track_args = {
                "device": device,
                "verbose": False,
                "tracker": kwargs.get("tracker", "bytetrack.yaml"),
                "persist": persist,
                "conf": conf,
            }
            if classes is not None:
                track_args["classes"] = classes
            for extra in ("iou", "max_det"):
                if extra in kwargs:
                    track_args[extra] = kwargs[extra]

            results = model.track(frame, **track_args)
            detections = []
            if results and len(results) > 0:
                result = results[0]
                detections = self._extract_detections_from_result(
                    result=result, model_id=model_id, tag=tag
                )
            return detections
        else:
            return self._track_with_internal(
                frame=frame,
                persist=persist,
                classes=classes,
                model_id=model_id,
                tag=tag,
                raw_dets=kwargs.get("detections", []),
            )

    def _track_with_internal(
        self,
        frame: np.ndarray,
        persist: bool,
        classes: list | None,
        model_id: str,
        tag: list,
        raw_dets: list,
    ) -> list[dict[str, Any]]:
        """Run ByteTracker's internal tracker on raw detections (no YOLO model)."""
        if not raw_dets:
            return []

        if self._internal_tracker is None:
            from ultralytics.cfg import get_cfg
            from ultralytics.trackers.byte_tracker import BYTETracker
            from ultralytics.utils.checks import check_yaml

            args = get_cfg(cast(_UltralyticsCfgSource, check_yaml("bytetrack.yaml")))
            self._internal_tracker = BYTETracker(args=args, frame_rate=30)

        if not persist:
            self._internal_tracker.reset()

        import torch
        from ultralytics.engine.results import Boxes

        det_tensor_list = _parse_raw_det_tensor_list(raw_dets, classes)
        if not det_tensor_list:
            return []

        det_tensor = torch.tensor(det_tensor_list, device="cpu", dtype=torch.float32)
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        boxes_obj = Boxes(det_tensor, orig_shape=(fh, fw))

        try:
            tracker_output = self._internal_tracker.update(boxes_obj, frame)
        except Exception as e:
            print(f"Internal Tracker iteration failed: {e}")
            return raw_dets

        return _build_tracked_detections(tracker_output, tag, model_id)


class BoTSORTTracker(BaseTracker):
    """
    BoT-SORT implementation using YOLO's built-in tracker.
    BoT-SORT is more robust but slightly slower than ByteTrack.
    """

    def __init__(self):
        super().__init__(tracker_name="botsort")
        self._internal_tracker = None

    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",  # condition for cuda or cpu
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Track using BoT-SORT algorithm.

        Args:
            frame: Input frame
            model: YOLO model object (can be None if detections are provided in kwargs)
            device: Device to run on
            persist: Persist tracks across frames
            conf: Confidence threshold
            **kwargs: Additional parameters (model_id, tag, detections, classes)

        Returns:
            List of detections with tracking IDs
        """
        model_id = kwargs.get("model_id", "unknown")
        tag = kwargs.get("tag", ["all"])
        classes = kwargs.get("classes", None)

        if hasattr(model, "track") and model is not None:
            track_args = {
                "device": device,
                "verbose": False,
                "tracker": kwargs.get("tracker", "botsort.yaml"),
                "persist": persist,
                "conf": conf,
            }
            if classes is not None:
                track_args["classes"] = classes
            for extra in ("iou", "max_det"):
                if extra in kwargs:
                    track_args[extra] = kwargs[extra]

            results = model.track(frame, **track_args)
            detections = []
            if results and len(results) > 0:
                result = results[0]
                detections = self._extract_detections_from_result(
                    result=result, model_id=model_id, tag=tag
                )
            return detections
        else:
            return self._track_with_internal(
                frame=frame,
                persist=persist,
                classes=classes,
                model_id=model_id,
                tag=tag,
                raw_dets=kwargs.get("detections", []),
            )

    def _track_with_internal(
        self,
        frame: np.ndarray,
        persist: bool,
        classes: list | None,
        model_id: str,
        tag: list,
        raw_dets: list,
    ) -> list[dict[str, Any]]:
        """Run BoT-SORT's internal tracker on raw detections (no YOLO model)."""
        if not raw_dets:
            return []

        if self._internal_tracker is None:
            from ultralytics.cfg import get_cfg
            from ultralytics.trackers.bot_sort import BOTSORT
            from ultralytics.utils.checks import check_yaml

            args = get_cfg(cast(_UltralyticsCfgSource, check_yaml("botsort.yaml")))
            self._internal_tracker = BOTSORT(args=args, frame_rate=30)

        if not persist:
            self._internal_tracker.reset()

        import torch
        from ultralytics.engine.results import Boxes

        det_tensor_list = _parse_raw_det_tensor_list(raw_dets, classes)
        if not det_tensor_list:
            return []

        det_tensor = torch.tensor(det_tensor_list, device="cpu", dtype=torch.float32)
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        boxes_obj = Boxes(det_tensor, orig_shape=(fh, fw))

        try:
            tracker_output = self._internal_tracker.update(boxes_obj, frame)
        except Exception as e:
            print(f"Internal Tracker iteration failed: {e}")
            return raw_dets

        return _build_tracked_detections(tracker_output, tag, model_id)


class CustomTracker(BaseTracker):
    """
    Custom tracker implementation.
    Use this as a template for implementing your own tracking algorithm.
    """

    def __init__(self, tracker_config: dict[str, Any] | None = None):
        """
        Initialize custom tracker with optional configuration.

        Args:
            tracker_config: Dictionary with custom tracker parameters
        """
        super().__init__(tracker_name="custom")
        self.config = tracker_config or {}
        # Initialize your custom tracker here

    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Track using custom algorithm.

        Args:
            frame: Input frame
            model: YOLO model object
            device: Device to run on
            persist: Persist tracks across frames (unused in this template)
            conf: Confidence threshold
            **kwargs: Additional parameters (model_id, tag)

        Returns:
            List of detections with tracking IDs
        """
        model_id = kwargs.get("model_id", "unknown")
        tag = kwargs.get("tag", ["all"])
        _ = persist

        if hasattr(model, "track") and model is not None:
            results = model(frame, conf=conf, device=device, verbose=False)
            detections = []
            if results and len(results) > 0:
                result = results[0]
                detections = self._extract_detections_from_result(
                    result=result, model_id=model_id, tag=tag
                )
            return detections
        else:
            return cast(list[dict[str, Any]], kwargs.get("detections", []))

    def reset(self) -> None:
        """Reset custom tracker state."""
        # Implement state reset logic for your custom tracker
        pass


class BoxmotTracker(BaseTracker):
    """
    Generic tracker wrapper for boxmot trackers.
    """

    def __init__(self, tracker_type: str):
        super().__init__(tracker_name=tracker_type)
        self.tracker_type = tracker_type
        self._internal_tracker: Any = None
        self._fallback_tracker: ByteTracker | None = None

    def _get_detections(
        self,
        model: Any,
        frame: np.ndarray,
        device: str,
        conf: float,
        **kwargs: Any,
    ) -> np.ndarray:
        """Extract and filter detections from model or raw input."""
        classes = kwargs.get("classes")
        if model is not None:
            try:
                pred_results = model.predict(frame, device=device, verbose=False, conf=conf)
            except Exception as e:
                print(f"Prediction failed inside BoxmotTracker: {e}")
                return np.empty((0, 6))

            if not pred_results:
                return np.empty((0, 6))
            pred_result = pred_results[0]
            if pred_result.boxes is not None and len(pred_result.boxes) > 0:
                dets = pred_result.boxes.data.cpu().numpy()
            else:
                dets = np.empty((0, 6))
        else:
            raw_dets = kwargs.get("detections", [])
            dets_list = []
            for d in raw_dets:
                if isinstance(d, dict):
                    bbox = d.get(BBOX, d.get("bbox", [0, 0, 0, 0]))
                    _cid = d.get(CLASS_ID, d.get("class_id", -1))
                    _conf = d.get(CONFIDENCE, d.get("confidence", 1.0))
                else:
                    bbox = d[:4]
                    _conf = d[4] if len(d) > 4 else 1.0
                    _cid = d[5] if len(d) > 5 else -1
                dets_list.append([bbox[0], bbox[1], bbox[2], bbox[3], _conf, _cid])
            dets = np.array(dets_list) if dets_list else np.empty((0, 6))

        if classes is not None and len(dets) > 0:
            mask = np.isin(dets[:, 5], classes)
            dets = dets[mask]

        return dets

    def _init_internal_tracker(self, device: str) -> bool:
        """Initialize the boxmot tracker instance, returning True if successful."""
        try:
            from boxmot.trackers.tracker_zoo import create_tracker

            mapped_type = "strongsort" if self.tracker_type == "deepsort" else self.tracker_type

            reid_weights = None
            if mapped_type in ("strongsort", "hybridsort"):
                reid_weights = "osnet_x0_25_msmt17.pt"

            self._internal_tracker = create_tracker(
                tracker_type=mapped_type,
                device=device,
                reid_weights=reid_weights,
            )
            return True
        except Exception as e:
            print(
                f"Warning: Failed to load/initialize boxmot tracker '{self.tracker_type}'. "
                f"Falling back to ByteTracker. Error: {e}"
            )
            self._fallback_tracker = ByteTracker()
            return False

    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if self._fallback_tracker is not None:
            return self._fallback_tracker.track(
                frame=frame,
                model=model,
                device=device,
                persist=persist,
                conf=conf,
                **kwargs,
            )

        dets = self._get_detections(model, frame, device, conf, **kwargs)
        if len(dets) == 0:
            return []

        if self._internal_tracker is None:
            if not self._init_internal_tracker(device):
                return self.track(
                    frame=frame,
                    model=model,
                    device=device,
                    persist=persist,
                    conf=conf,
                    **kwargs,
                )

        internal_tracker = self._internal_tracker
        assert internal_tracker is not None

        if not persist:
            internal_tracker.reset()

        # Update boxmot tracker
        try:
            tracker_output = internal_tracker.update(dets, frame)
        except Exception as e:
            print(f"boxmot tracker update failed: {e}")
            return []

        # Convert tracker output to detections format
        model_id = kwargs.get("model_id", "unknown")
        tag = kwargs.get("tag", ["all"])
        model_names = getattr(model, "names", None)
        return _build_tracked_detections(
            tracker_output=tracker_output,
            tag=tag,
            model_id=model_id,
            model_names=model_names,
        )

    def reset(self) -> None:
        if self._fallback_tracker is not None:
            self._fallback_tracker.reset()
        elif self._internal_tracker is not None:
            self._internal_tracker.reset()


class OCSORTTracker(BoxmotTracker):
    def __init__(self):
        super().__init__("ocsort")


class HybridSORTTracker(BoxmotTracker):
    def __init__(self):
        super().__init__("hybridsort")


class DeepSORTTracker(BoxmotTracker):
    def __init__(self):
        super().__init__("deepsort")


class BoostTrackTracker(BoxmotTracker):
    def __init__(self):
        super().__init__("boosttrack")
