"""
OmniTrack AI — Item / Product Detection & Shelf Activity Analytics
═══════════════════════════════════════════════════════════════════

Two cooperating components:

1. ItemDetector
   - Runs a YOLO model (COCO by default) and returns generic-object
     detections (bottle, cup, book, handbag, ...). If a custom product
     model is configured, it loads that instead — same interface, better
     class names (per-SKU).

2. ShelfActivityDetector
   - Per shelf zone, maintains a slowly-updated reference image.
   - When a tracked person overlaps the zone, the zone region is being
     "touched". When the person leaves, we diff the zone against the
     reference and call it a "pick" (less stuff) or "put_back" (more
     stuff) based on how the foreground edge density has changed.
   - Emits structured events that the frontend renders as a live feed.

Design notes:
  - No pixel-exact accuracy is needed; the goal is engagement signal.
  - All heavy work happens off the asyncio loop (called via to_thread
    in the pipeline).
  - If OpenCV is not available (mock mode) the module degrades silently.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

try:
    import cv2  # noqa: F401
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False
    logger.warning("OpenCV not installed — shelf activity diffing disabled")

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

from app.utils.paths import resolve_model_path


# ───────────────────────────────────────────────────────────────────
# COCO retail-relevant classes (subset of the 80 COCO classes that
# actually show up on store shelves / in customer hands).
# ───────────────────────────────────────────────────────────────────

DEFAULT_RETAIL_COCO_CLASSES: List[int] = [
    24,  # backpack
    26,  # handbag
    28,  # suitcase
    39,  # bottle
    40,  # wine glass
    41,  # cup
    42,  # fork
    43,  # knife
    44,  # spoon
    45,  # bowl
    46,  # banana
    47,  # apple
    48,  # sandwich
    49,  # orange
    50,  # broccoli
    51,  # carrot
    52,  # hot dog
    53,  # pizza
    54,  # donut
    55,  # cake
    64,  # mouse
    65,  # remote
    66,  # keyboard
    67,  # cell phone
    73,  # book
    76,  # scissors
    77,  # teddy bear
    79,  # toothbrush
]


@dataclass
class ItemDetection:
    """One detected non-person item in a frame."""
    class_id: int
    class_name: str
    bbox: Tuple[float, float, float, float]  # (x, y, w, h)
    confidence: float
    camera_id: int


@dataclass
class ShelfEvent:
    """A pick / put_back event scoped to a shelf zone."""
    event_type: str            # "pick" | "put_back"
    zone_id: str
    zone_name: str
    camera_id: int
    track_id: Optional[int]
    confidence: float
    timestamp: float
    delta: float               # Edge-density change magnitude (0..1+)


# ═══════════════════════════════════════════════════════════════════
# 1. ItemDetector — generic object / custom product detector
# ═══════════════════════════════════════════════════════════════════

class ItemDetector:
    """
    YOLO-based detector for non-person items. Defaults to COCO classes
    relevant to retail; can be swapped with a custom product model by
    passing `custom_model_path`.

    Usage:
        det = ItemDetector(model_path="yolov8n.pt")
        items = det.detect(frame, camera_id=1)
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        custom_model_path: Optional[str] = None,
        confidence: float = 0.35,
        nms_threshold: float = 0.45,
        coco_classes: Optional[List[int]] = None,
    ):
        self.confidence = confidence
        self.nms_threshold = nms_threshold
        self.coco_classes = coco_classes or DEFAULT_RETAIL_COCO_CLASSES
        self._model = None
        self._is_custom = bool(custom_model_path)
        # Custom product model takes precedence over COCO.
        self._model_path = custom_model_path or model_path
        self._load()

    def _load(self) -> None:
        if not ULTRALYTICS_AVAILABLE:
            logger.warning("Ultralytics not available — ItemDetector mock mode")
            return
        try:
            resolved = resolve_model_path(self._model_path) or self._model_path
            self._model = YOLO(resolved)
            self._model_path = resolved
            tag = "CUSTOM" if self._is_custom else "COCO"
            logger.info(
                f"[ItemDetector] Loaded {tag} model: {resolved} "
                f"({len(self._model.names)} classes)"
            )
        except Exception as e:
            logger.error(f"[ItemDetector] Failed to load {self._model_path}: {e}")
            self._model = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def detect(self, frame: np.ndarray, camera_id: int = 0) -> List[ItemDetection]:
        if self._model is None or frame is None:
            return []
        try:
            kwargs = dict(
                source=frame,
                conf=self.confidence,
                iou=self.nms_threshold,
                verbose=False,
            )
            # Only filter to retail subset when running stock COCO.
            if not self._is_custom:
                kwargs["classes"] = self.coco_classes
            results = self._model.predict(**kwargs)
        except Exception as e:
            logger.debug(f"[ItemDetector] predict error cam {camera_id}: {e}")
            return []

        out: List[ItemDetection] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                try:
                    xyxy = box.xyxy[0].cpu().numpy()
                    cid = int(box.cls[0])
                    cname = (self._model.names or {}).get(cid, f"class_{cid}")
                    out.append(ItemDetection(
                        class_id=cid,
                        class_name=str(cname),
                        bbox=(
                            float(xyxy[0]),
                            float(xyxy[1]),
                            float(xyxy[2] - xyxy[0]),
                            float(xyxy[3] - xyxy[1]),
                        ),
                        confidence=float(box.conf[0]),
                        camera_id=camera_id,
                    ))
                except Exception:
                    continue
        return out


# ═══════════════════════════════════════════════════════════════════
# 2. ShelfActivityDetector — pick / put-back via background diff
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _ZoneState:
    """Per-zone reference image + occupancy state."""
    zone_id: str
    zone_name: str
    camera_id: int
    bbox: Tuple[int, int, int, int]              # (x1,y1,x2,y2) in native coords
    reference: Optional[np.ndarray] = None       # Edge map of "shelf-at-rest"
    last_ref_update: float = 0.0
    person_inside: bool = False
    last_track_id: Optional[int] = None
    person_entered_at: float = 0.0


class ShelfActivityDetector:
    """
    Pick / put-back event detector.

    For each shelf zone:
      - When NO person overlaps the zone, we slowly refresh a reference
        edge-map of the shelf contents (~every 5s of empty time).
      - When a person enters the zone, we mark it as "occupied" and
        remember the track_id.
      - When the person exits, we compare the current zone edges with
        the stored reference. Edge-density delta tells us whether stuff
        was picked up (edges went down) or put back (edges went up).

    This is intentionally pixel-cheap (Canny edges on a downscaled
    grayscale crop). Robust against minor lighting changes; not robust
    against the camera moving — fine for fixed CCTV.
    """

    REF_REFRESH_S = 5.0          # Refresh reference every N seconds when zone is empty
    DELTA_PICK_THRESHOLD = 0.05  # Min relative edge change to count as event
    EVENT_HISTORY_LIMIT = 200    # Cap in-memory events

    def __init__(self):
        self._zones: Dict[str, _ZoneState] = {}
        self._events: Deque[ShelfEvent] = deque(maxlen=self.EVENT_HISTORY_LIMIT)
        self._active_items_by_cam: Dict[int, List[ItemDetection]] = {}

    # ── Zone registry ────────────────────────────────────────────────

    def sync_zones(self, zones: List[Any]) -> None:
        """
        Mirror the ShelfAnalytics zone list. Drops removed zones, adds
        new ones, preserves reference frames for existing zones.
        Accepts ShelfZoneConfig-shaped objects (zone_id, zone_name,
        bbox, camera_id).
        """
        seen = set()
        for z in zones:
            zid = getattr(z, "zone_id", None)
            if not zid:
                continue
            seen.add(zid)
            existing = self._zones.get(zid)
            new_bbox = tuple(int(v) for v in getattr(z, "bbox", (0, 0, 0, 0)))
            if existing is None:
                self._zones[zid] = _ZoneState(
                    zone_id=zid,
                    zone_name=getattr(z, "zone_name", zid),
                    camera_id=int(getattr(z, "camera_id", 0)),
                    bbox=new_bbox,
                )
            else:
                # Bbox edited — drop old reference so it re-learns
                if existing.bbox != new_bbox:
                    existing.bbox = new_bbox
                    existing.reference = None
                existing.zone_name = getattr(z, "zone_name", existing.zone_name)
        # Drop zones the user deleted
        for zid in list(self._zones.keys()):
            if zid not in seen:
                self._zones.pop(zid, None)

    # ── Update step (called per pipeline tick) ──────────────────────

    def update(
        self,
        camera_id: int,
        frame: np.ndarray,
        person_engagements: List[Dict[str, Any]],
        items: Optional[List[ItemDetection]] = None,
    ) -> List[ShelfEvent]:
        """
        Run one tick. Returns the list of NEW events emitted this tick.

        Args:
            camera_id: source camera
            frame: BGR np.ndarray of the current frame (full resolution)
            person_engagements: ShelfAnalytics output for this camera
                (entries shaped {track_id, zone_id, ...})
            items: optional list of ItemDetections for this camera

        Notes:
            - Falls back to a no-op if OpenCV is unavailable or frame is
              None.
        """
        if items is not None:
            self._active_items_by_cam[camera_id] = list(items)

        if not OPENCV_AVAILABLE or frame is None:
            return []

        import cv2

        new_events: List[ShelfEvent] = []
        # Build zone_id -> currently-engaged track_id (this camera only).
        engaged: Dict[str, int] = {}
        for e in person_engagements or []:
            zid = e.get("zone_id")
            tid = e.get("track_id")
            if zid and tid is not None:
                # Last writer wins — fine, we just need any track_id.
                engaged[zid] = int(tid)

        h_frame, w_frame = frame.shape[:2]
        now = time.time()

        for zid, state in self._zones.items():
            if state.camera_id != camera_id:
                continue

            x1, y1, x2, y2 = state.bbox
            # Clamp to frame bounds.
            x1 = max(0, min(int(x1), w_frame - 1))
            y1 = max(0, min(int(y1), h_frame - 1))
            x2 = max(x1 + 1, min(int(x2), w_frame))
            y2 = max(y1 + 1, min(int(y2), h_frame))
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            edges = self._edges(crop)

            is_engaged = zid in engaged
            track_id = engaged.get(zid)

            # --- Empty zone: refresh reference periodically ---
            if not is_engaged and not state.person_inside:
                if (
                    state.reference is None
                    or (now - state.last_ref_update) > self.REF_REFRESH_S
                ):
                    state.reference = edges
                    state.last_ref_update = now
                continue

            # --- Person just entered ---
            if is_engaged and not state.person_inside:
                state.person_inside = True
                state.last_track_id = track_id
                state.person_entered_at = now
                continue

            # --- Person just exited: compute pick / put_back ---
            if not is_engaged and state.person_inside:
                state.person_inside = False
                if state.reference is None:
                    # Couldn't establish a baseline — nothing to compare.
                    continue
                ref_density = float(state.reference.mean()) / 255.0
                cur_density = float(edges.mean()) / 255.0
                # Relative change (positive = more stuff visible now).
                if ref_density < 1e-3:
                    continue
                delta = (cur_density - ref_density) / ref_density
                if abs(delta) < self.DELTA_PICK_THRESHOLD:
                    # Person was there but didn't visibly disturb the shelf.
                    continue
                kind = "put_back" if delta > 0 else "pick"
                ev = ShelfEvent(
                    event_type=kind,
                    zone_id=zid,
                    zone_name=state.zone_name,
                    camera_id=camera_id,
                    track_id=state.last_track_id,
                    confidence=min(1.0, abs(delta) / 0.5),
                    timestamp=now,
                    delta=round(delta, 3),
                )
                self._events.append(ev)
                new_events.append(ev)
                # Force a fresh reference next time the zone is empty so
                # subsequent pick/put-back events compare against the new
                # shelf state, not the original one.
                state.reference = None
                state.last_ref_update = 0.0

        return new_events

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _edges(crop: np.ndarray) -> np.ndarray:
        """Downscaled Canny edge map — cheap, lighting-tolerant proxy
        for shelf 'fullness'."""
        import cv2
        small = cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        return cv2.Canny(gray, 60, 160)

    # ── API surface ─────────────────────────────────────────────────

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        evs = list(self._events)[-limit:]
        evs.reverse()  # newest first
        return [
            {
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "zone_name": e.zone_name,
                "camera_id": e.camera_id,
                "track_id": e.track_id,
                "confidence": round(e.confidence, 3),
                "timestamp": e.timestamp,
                "delta": e.delta,
            }
            for e in evs
        ]

    def items_for_camera(self, camera_id: int) -> List[Dict[str, Any]]:
        items = self._active_items_by_cam.get(camera_id, [])
        return [
            {
                "class_id": it.class_id,
                "class_name": it.class_name,
                "bbox": list(it.bbox),
                "confidence": round(it.confidence, 3),
                "camera_id": it.camera_id,
            }
            for it in items
        ]

    def all_items(self) -> Dict[int, List[Dict[str, Any]]]:
        return {cam: self.items_for_camera(cam) for cam in self._active_items_by_cam.keys()}
