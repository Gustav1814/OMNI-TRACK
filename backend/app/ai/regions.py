"""
OmniTrack AI — Region assignment
═══════════════════════════════════════════════════════════════

Ported from VisRax (`services/model_service.py` + `utils/geometry.py`).

Runs once per frame, before any KPI. Every detection is stamped with:

  region_name / item_at_current_region  → which region it is in ("global" if none)
  is_region_crossed                     → True when it has crossed a Line region

The KPIs never do geometry themselves; they only read these two keys. That
split is what lets one state machine serve lines, boxes and polygons.

COORDINATES: regions arrive in the frame size the browser drew them against
(`frame_width`/`frame_height` on the job). The pipeline may decode smaller
frames, so a scale factor is applied here rather than storing regions in
pixels that match only one resolution.

BBOX FORMAT: OmniTrack detections carry [x, y, w, h]; VisRax used
[x_min, y_min, x_max, y_max]. The centre helper below accounts for that.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

# ── Constants (mirror VisRax's constants/camera_direction.py) ──────────
UP_TO_DOWN = "up_to_down"
DOWN_TO_UP = "down_to_up"
LEFT_TO_RIGHT = "left_to_right"
RIGHT_TO_LEFT = "right_to_left"

VALID_DIRECTIONS = {UP_TO_DOWN, DOWN_TO_UP, LEFT_TO_RIGHT, RIGHT_TO_LEFT}

GLOBAL = "global"

REGION_TYPE_LINE = "Line"
REGION_TYPE_POLYGON = "polygon"
REGION_TYPE_BOX = "bounding_box"

# Detection keys written by this module.
REGION_NAME = "region_name"
ITEM_AT_CURRENT_REGION_NAME = "item_at_current_region"
IS_REGION_CROSSED = "is_region_crossed"
# Direction of the line a detection just crossed (set only on a crossing).
REGION_DIRECTION = "_region_direction"


# ── Geometry ──────────────────────────────────────────────────────────

def point_in_polygon(point: Tuple[float, float], polygon: Sequence[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon, ported verbatim from VisRax utils/geometry.py."""
    if len(polygon) < 3:
        return False
    x, y = point
    n = len(polygon)
    inside = False

    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    xinters = 0.0
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def point_in_bbox(point: Tuple[float, float], rect: Tuple[float, float, float, float]) -> bool:
    """Axis-aligned containment. rect is (x_min, y_min, x_max, y_max)."""
    x, y = point
    x_min, y_min, x_max, y_max = rect
    return x_min <= x <= x_max and y_min <= y <= y_max


def resolve_detection_center(detection: Dict[str, Any]) -> Tuple[float, float]:
    """
    Anchor point used for every region test.

    Bbox centre by default; mid-hip when a pose model supplied confident
    keypoints (VisRax does the same — a person's hips track their position on
    the floor far better than the centre of a full-body box).
    """
    bbox = detection.get("bbox") or []
    if len(bbox) < 4:
        return 0.0, 0.0

    # OmniTrack stores [x, y, w, h].
    cx = float(bbox[0]) + float(bbox[2]) / 2.0
    cy = float(bbox[1]) + float(bbox[3]) / 2.0

    keypoints = detection.get("keypoints")
    if keypoints and len(keypoints) >= 13:
        left_hip = keypoints[11]
        right_hip = keypoints[12]
        try:
            if float(left_hip[2]) > 0.3 and float(right_hip[2]) > 0.3:
                cx = (float(left_hip[0]) + float(right_hip[0])) / 2.0
                cy = (float(left_hip[1]) + float(right_hip[1])) / 2.0
        except (IndexError, TypeError, ValueError):
            pass

    return cx, cy


# ── Region checks ─────────────────────────────────────────────────────

def _scaled_points(points: Any, sx: float, sy: float) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in points or []:
        try:
            out.append((float(p.get("x", 0)) * sx, float(p.get("y", 0)) * sy))
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def _check_polygon_region(
    detection: Dict[str, Any], region: Dict[str, Any], cx: float, cy: float,
    sx: float, sy: float,
) -> bool:
    points = _scaled_points(region.get("points"), sx, sy)
    if point_in_polygon((cx, cy), points):
        name = str(region.get("name", ""))
        detection[ITEM_AT_CURRENT_REGION_NAME] = name
        detection[REGION_NAME] = name
        return True
    return False


def _check_bbox_region(
    detection: Dict[str, Any], region: Dict[str, Any], cx: float, cy: float,
    sx: float, sy: float,
) -> bool:
    coords = region.get("coordinates") or {}
    if not coords:
        return False
    try:
        rect = (
            float(coords.get("x_min", 0)) * sx,
            float(coords.get("y_min", 0)) * sy,
            float(coords.get("x_max", 0)) * sx,
            float(coords.get("y_max", 0)) * sy,
        )
    except (TypeError, ValueError):
        return False
    if point_in_bbox((cx, cy), rect):
        name = str(region.get("name", ""))
        detection[ITEM_AT_CURRENT_REGION_NAME] = name
        detection[REGION_NAME] = name
        return True
    return False


def _has_crossed_line(
    cx: float, cy: float, line_x: float, line_y: float, direction: str,
) -> bool:
    """
    VisRax's crossing test: is the anchor point past the line's MIDPOINT, along
    the single axis implied by the configured direction?

    Deliberately kept as-is. It means a line reports only the one direction it
    was configured for — an `up_to_down` line counts IN and never OUT; a second
    line is drawn for the opposite direction.
    """
    if direction == RIGHT_TO_LEFT:
        return cx < line_x
    if direction == LEFT_TO_RIGHT:
        return cx > line_x
    if direction == UP_TO_DOWN:
        return cy > line_y
    if direction == DOWN_TO_UP:
        return cy < line_y
    return False


def _check_line_region(
    detection: Dict[str, Any], region: Dict[str, Any], cx: float, cy: float,
    sx: float, sy: float,
) -> bool:
    points = _scaled_points(region.get("line_points"), sx, sy)
    if len(points) < 2:
        return False

    # The "counting line" is the segment's midpoint, per VisRax.
    line_x = (points[0][0] + points[1][0]) / 2.0
    line_y = (points[0][1] + points[1][1]) / 2.0

    direction = str(region.get("object_moving_direction") or "")
    if direction not in VALID_DIRECTIONS:
        logger.debug(
            f"Region '{region.get('name')}' has no valid object_moving_direction "
            f"({direction!r}); skipping line check."
        )
        return False

    # Line counting needs a track id — without one there is no identity to
    # attribute a crossing to, and the KPI would recount every frame.
    if detection.get("track_id") is None:
        return False

    if _has_crossed_line(cx, cy, line_x, line_y, direction):
        detection[IS_REGION_CROSSED] = True
        detection[ITEM_AT_CURRENT_REGION_NAME] = str(region.get("name", ""))
        # Carry the crossed line's direction so the KPI knows which counter to
        # move. VisRax rebuilds this from a region→direction map passed in
        # kwargs; stamping it here is equivalent and keeps the KPI geometry-free.
        detection[REGION_DIRECTION] = direction
        return True

    return False


def _check_region(
    detection: Dict[str, Any], region: Dict[str, Any], cx: float, cy: float,
    sx: float, sy: float,
) -> bool:
    rtype = str(region.get("type", ""))
    if rtype == REGION_TYPE_POLYGON:
        return _check_polygon_region(detection, region, cx, cy, sx, sy)
    if rtype == REGION_TYPE_BOX:
        return _check_bbox_region(detection, region, cx, cy, sx, sy)
    if rtype == REGION_TYPE_LINE:
        return _check_line_region(detection, region, cx, cy, sx, sy)
    return False


# ── Entry point ───────────────────────────────────────────────────────

def assign_regions(
    detections: List[Dict[str, Any]],
    regions: Optional[List[Dict[str, Any]]],
    scale: Tuple[float, float] = (1.0, 1.0),
) -> List[Dict[str, Any]]:
    """
    Stamp every detection with its current region and crossing flag.

    Mutates and returns the same dicts. Detections always come back with the
    three keys set, so KPIs never have to guard for their absence. The first
    matching region wins, which is what lets a frame be attributed to the
    specific line that was crossed.
    """
    sx, sy = scale
    for detection in detections:
        detection[ITEM_AT_CURRENT_REGION_NAME] = GLOBAL
        detection[REGION_NAME] = GLOBAL
        detection[IS_REGION_CROSSED] = False
        detection[REGION_DIRECTION] = None

        if not regions:
            continue

        cx, cy = resolve_detection_center(detection)
        for region in regions:
            try:
                if _check_region(detection, region, cx, cy, sx, sy):
                    break
            except Exception as e:  # a malformed region must not kill the tick
                logger.debug(f"Region check failed for '{region.get('name')}': {e}")

    return detections
