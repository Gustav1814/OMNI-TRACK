"""
Pure analytics definitions (Remediation Plan, Phase 3).

Every number OmniTrack shows about zones is computed by one of the functions
or small state machines in this module. They take plain Python values (no
database, no models, no I/O) so each definition can be unit-tested in
isolation (backend/tests/test_analytics_*.py).

Coordinates: zone geometry is stored normalised to the frame (0..1 on both
axes, origin top-left), so a resolution change never breaks a zone. Track
boxes arrive in pixels and are normalised with the frame size before any test.

Definitions implemented here
----------------------------
* Zone membership: a person is "in" a zone when the FOOT POINT of their box
  (bottom-centre) lies inside the polygon. Box overlap is never used.
* Crowd level: the zone count is median-smoothed over 3 s; the level comes
  from the zone capacity at 10/30/60/90 % with hysteresis (a level is left
  only once the occupancy falls a full band below its threshold).
* Dwell: a presence starts when the foot point enters the polygon; it becomes a
  VISIT once the person has been inside for 3 s, and it ENDS after 1 s outside
  (short excursions do not split a visit). Dwell = last-inside - first-inside.
  A visit is ENGAGED when its dwell is over 10 s.
* Attention score: engaged_visits x median_dwell, scaled so the top zone = 100.
* Queue length: median number of tracks in the queue polygon over 2 s.
* Fire: an alert needs 3 hits in the last 5 inferences (hit = a box with
  confidence >= 0.5 and a minimum area); it clears after 30 s with no hit.
* Store vibe: documented weighted mean of measured, normalised components.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Polygon = Sequence[Point]

# ---------------------------------------------------------------------------
# Parameters (documented defaults; every value is a definition, not a tweak)
# ---------------------------------------------------------------------------

CROWD_SMOOTHING_S = 3.0          # median window for zone counts
CROWD_LEVEL_THRESHOLDS = (0.10, 0.30, 0.60, 0.90)
CROWD_LEVELS = ("empty", "low", "moderate", "high", "critical")
CROWD_HYSTERESIS = 0.05          # min band (fraction of capacity) to fall back a level

DWELL_START_S = 3.0              # inside this long => visit starts
DWELL_END_S = 1.0                # outside this long => visit ends
ENGAGED_DWELL_S = 10.0           # dwell above this => engaged visit

QUEUE_SMOOTHING_S = 2.0          # median window for queue length
QUEUE_MIN_PRESENCE_S = 3.0       # a queue presence shorter than this is a walk-through
QUEUE_TO_COUNTER_GRACE_S = 10.0  # left the queue but may still reach the counter
SERVICE_MAX_S = 1800.0           # presences at a counter longer than 30 min are staff

FIRE_WINDOW = 5                  # last N inferences considered
FIRE_HITS_REQUIRED = 3           # k of N
FIRE_MIN_CONF = 0.5
FIRE_MIN_AREA_FRAC = 0.0005      # of the frame area (0.05 %), when the frame size is known
FIRE_MIN_AREA_PX = 400.0         # fallback when the frame size is unknown (20 x 20 px)
FIRE_CLEAR_S = 30.0              # no hit for this long => alert clears
FIRE_COOLDOWN_S = 300.0          # a re-trigger inside this window re-opens the same incident

LINE_EPS = 0.01                  # normalised distance a foot point must clear the line by

EMOTION_BUCKET_S = 300           # aggregate faces per zone per 5 min

# Emotion labels from the shipped classifier (HSEmotion enet_b0_8, AffectNet
# order). Only labels a real classifier produced are aggregated.
EMOTION_VALENCE: Dict[str, float] = {
    "happy": 1.0,
    "surprise": 0.0,
    "neutral": 0.0,
    "sad": -1.0,
    "angry": -1.0,
    "fear": -1.0,
    "disgust": -1.0,
    "contempt": -1.0,
}

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def foot_point(xyxy: Sequence[float]) -> Point:
    """Bottom-centre of a box in its own coordinates."""
    x1, y1, x2, y2 = (float(v) for v in xyxy[:4])
    return ((x1 + x2) / 2.0, max(y1, y2))


def normalize_point(pt: Point, frame_wh: Tuple[int, int]) -> Point:
    w, h = frame_wh
    if not w or not h:
        raise ValueError("frame size must be non-zero")
    return (pt[0] / float(w), pt[1] / float(h))


def normalized_foot_point(xyxy: Sequence[float], frame_wh: Tuple[int, int]) -> Point:
    return normalize_point(foot_point(xyxy), frame_wh)


def point_in_polygon(pt: Point, polygon: Polygon) -> bool:
    """Even-odd ray casting. Points exactly on an edge count as inside."""
    n = len(polygon)
    if n < 3:
        return False
    x, y = pt
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        # On-edge test (collinear and within the segment's bounding box).
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if (
            abs(cross) <= 1e-12
            and min(xi, xj) - 1e-12 <= x <= max(xi, xj) + 1e-12
            and min(yi, yj) - 1e-12 <= y <= max(yi, yj) + 1e-12
        ):
            return True
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def polygon_area(polygon: Polygon) -> float:
    """Shoelace area (absolute) in polygon units."""
    n = len(polygon)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def validate_polygon(points: Sequence[Sequence[float]], zone_type: str) -> List[List[float]]:
    """Validate normalised zone geometry and return it as [[x, y], ...].

    `entry_line` needs exactly 2 points; every other type needs a polygon of
    3+ points with non-zero area. All coordinates must be within [0, 1].
    """
    pts: List[List[float]] = []
    for p in points or []:
        if len(p) != 2:
            raise ValueError("each point must be [x, y]")
        x, y = float(p[0]), float(p[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("coordinates must be finite numbers")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("coordinates must be normalised to [0, 1]")
        pts.append([round(x, 5), round(y, 5)])
    if zone_type == "entry_line":
        if len(pts) != 2:
            raise ValueError("an entry_line needs exactly 2 points")
        if math.dist(pts[0], pts[1]) < 0.01:
            raise ValueError("entry_line is too short")
        return pts
    if len(pts) < 3:
        raise ValueError("a zone polygon needs at least 3 points")
    if len(pts) > 64:
        raise ValueError("a zone polygon may have at most 64 points")
    if _self_intersects(pts):
        raise ValueError("zone polygon edges cross each other; draw the corners in order around the area")
    if polygon_area(pts) < 1e-4:
        raise ValueError("zone polygon has (almost) no area")
    return pts


def _segments_cross(p1, p2, p3, p4) -> bool:
    """True when segments p1-p2 and p3-p4 properly intersect (touching counts)."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on_seg(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and on_seg(p1, p2, p3)) or (o2 == 0 and on_seg(p1, p2, p4))
            or (o3 == 0 and on_seg(p3, p4, p1)) or (o4 == 0 and on_seg(p3, p4, p2)))


def _self_intersects(pts: Sequence[Sequence[float]]) -> bool:
    """Check every pair of non-adjacent polygon edges (n <= 64, so O(n^2) is fine)."""
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex
            if _segments_cross(*edges[i], *edges[j]):
                return True
    return False


def signed_line_distance(pt: Point, a: Point, b: Point) -> float:
    """Signed perpendicular distance of pt from the infinite line a->b.

    Positive = left of a->b in image coordinates (y down) means the cross
    product (b-a) x (pt-a) is positive.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0
    return (dx * (pt[1] - a[1]) - dy * (pt[0] - a[0])) / length


def projection_t(pt: Point, a: Point, b: Point) -> float:
    """Position of pt's projection along segment a->b (0 at a, 1 at b)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0.0
    return ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / denom


class LineCrossingState:
    """Counts crossings of one track over one entry line, without jitter.

    The track's side is only (re)confirmed when the foot point is at least
    LINE_EPS away from the line, so a person standing on the line cannot
    generate in/out/in/out. A crossing is counted when the confirmed side flips
    and the crossing point lies within the drawn segment.

    `in_sign` is +1 when "inside the store" is the positive side of a->b.
    """

    __slots__ = ("side", "last_pt")

    def __init__(self) -> None:
        self.side: int = 0
        self.last_pt: Optional[Point] = None

    def update(self, pt: Point, a: Point, b: Point, in_sign: int = 1, eps: float = LINE_EPS) -> int:
        """Return +1 for an inward crossing, -1 for outward, 0 otherwise."""
        d = signed_line_distance(pt, a, b)
        result = 0
        if abs(d) >= eps:
            side = 1 if d > 0 else -1
            if self.side and side != self.side:
                # Where along the segment did the path cross? Use the midpoint
                # between the last confirmed point and this one.
                ref = self.last_pt if self.last_pt is not None else pt
                mid = ((ref[0] + pt[0]) / 2.0, (ref[1] + pt[1]) / 2.0)
                t = projection_t(mid, a, b)
                if -0.05 <= t <= 1.05:
                    result = 1 if side == in_sign else -1
            self.side = side
            self.last_pt = pt
        return result


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------


class RollingMedian:
    """Median of (ts, value) samples within the last `window_s` seconds."""

    __slots__ = ("window_s", "_samples")

    def __init__(self, window_s: float) -> None:
        self.window_s = float(window_s)
        self._samples: Deque[Tuple[float, float]] = deque()

    def add(self, ts: float, value: float) -> float:
        self._samples.append((ts, float(value)))
        cutoff = ts - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        return self.value()

    def value(self) -> float:
        # median_high: always a value that was actually observed (a whole
        # number of people, never "2.5"); ties round up, the safe side for
        # crowd and queue alerts.
        if not self._samples:
            return 0.0
        return float(statistics.median_high(v for _, v in self._samples))

    def max(self) -> float:
        return max((v for _, v in self._samples), default=0.0)

    def __len__(self) -> int:
        return len(self._samples)


# ---------------------------------------------------------------------------
# Crowd levels with hysteresis
# ---------------------------------------------------------------------------


def crowd_level_index(
    count: float,
    capacity: Optional[int],
    prev_index: Optional[int] = None,
    thresholds: Sequence[float] = CROWD_LEVEL_THRESHOLDS,
    hysteresis: float = CROWD_HYSTERESIS,
) -> Optional[int]:
    """Level index into CROWD_LEVELS for `count` people in a zone of `capacity`.

    Going UP a level needs occupancy >= that level's threshold. Going DOWN
    needs occupancy < threshold - band, where band = max(hysteresis,
    1/capacity) so a one-person wobble across a threshold never flickers the
    level. The band is capped at half the threshold so every lower level stays
    reachable (with a small capacity, 1/capacity can exceed the lowest
    threshold, which would otherwise pin an empty zone at "low").
    Returns None when the zone has no capacity (no level defined).
    """
    if not capacity or capacity <= 0:
        return None
    pct = float(count) / float(capacity)
    raw = 0
    for i, th in enumerate(thresholds):
        if pct >= th:
            raw = i + 1
    if prev_index is None or raw >= prev_index:
        return raw
    band = max(hysteresis, 1.0 / float(capacity))
    level = prev_index
    # Step down only through thresholds we are clearly below.
    while level > 0 and pct < thresholds[level - 1] - min(band, thresholds[level - 1] / 2.0):
        level -= 1
    return max(level, raw)


def crowd_level_name(index: Optional[int]) -> Optional[str]:
    return None if index is None else CROWD_LEVELS[index]


# ---------------------------------------------------------------------------
# Dwell / presence state machine
# ---------------------------------------------------------------------------


@dataclass
class PresenceState:
    """Presence of one track in one zone.

    first_in: time the foot point first entered (start of the presence)
    last_in:  last time it was observed inside
    visit:    True once inside for >= start_s (a counted visit)
    """

    first_in: float
    last_in: float
    visit: bool = False


@dataclass(frozen=True)
class PresenceUpdate:
    """Result of feeding one observation to `presence_step`."""

    entered: bool = False                 # a new presence started
    visit_started: bool = False           # presence just became a visit
    ended: Optional[PresenceState] = None  # presence that just ended (finalised)


def presence_step(
    state: Optional[PresenceState],
    ts: float,
    inside: bool,
    start_s: float = DWELL_START_S,
    end_s: float = DWELL_END_S,
) -> Tuple[Optional[PresenceState], PresenceUpdate]:
    """Advance a presence by one observation. Pure: returns the new state.

    * outside -> inside: a presence starts (passer-by).
    * inside for >= start_s since first_in: the presence becomes a visit.
    * outside for >= end_s since last_in: the presence ends; dwell is
      last_in - first_in (time outside is not counted).
    """
    if inside:
        if state is None:
            new = PresenceState(first_in=ts, last_in=ts, visit=start_s <= 0)
            return new, PresenceUpdate(entered=True, visit_started=new.visit)
        state.last_in = ts
        if not state.visit and ts - state.first_in >= start_s:
            state.visit = True
            return state, PresenceUpdate(visit_started=True)
        return state, PresenceUpdate()
    if state is None:
        return None, PresenceUpdate()
    if ts - state.last_in >= end_s:
        return None, PresenceUpdate(ended=state)
    return state, PresenceUpdate()


def dwell_seconds(state: PresenceState) -> float:
    return max(0.0, state.last_in - state.first_in)


# Dwell overrun: optional per-zone `config.dwell_alert_seconds` (not entry lines).
DWELL_ALERT_MIN_S = 5.0
DWELL_ALERT_MAX_S = 86400.0


def dwell_alert_threshold(config: Optional[dict]) -> Optional[float]:
    """The zone's dwell-overrun threshold in seconds, or None when disabled/invalid."""
    raw = (config or {}).get("dwell_alert_seconds")
    if raw in (None, "", 0):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not (DWELL_ALERT_MIN_S <= val <= DWELL_ALERT_MAX_S):
        return None
    return val


# ---------------------------------------------------------------------------
# Zone metrics (used by routers on persisted events, and by tests)
# ---------------------------------------------------------------------------


def median_or_none(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return float(statistics.median(vals)) if vals else None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile (q in 0..100); None when empty."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * (q / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def attention_scores(zones: Dict[object, Tuple[int, Optional[float]]]) -> Dict[object, float]:
    """Attention score per zone: engaged_visits x median_dwell, top zone = 100.

    `zones` maps zone key -> (engaged_visits, median_dwell_s). Zones with no
    engaged visits score 0. If no zone has attention, every score is 0.
    """
    raw = {
        k: float(engaged) * float(med or 0.0)
        for k, (engaged, med) in zones.items()
    }
    top = max(raw.values(), default=0.0)
    if top <= 0:
        return {k: 0.0 for k in raw}
    return {k: round(v / top * 100.0, 1) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Fire k-of-n with auto-clear
# ---------------------------------------------------------------------------


def fire_min_area(frame_wh: Optional[Tuple[int, int]] = None,
                  min_area_frac: float = FIRE_MIN_AREA_FRAC,
                  min_area_px: float = FIRE_MIN_AREA_PX) -> float:
    if frame_wh and frame_wh[0] and frame_wh[1]:
        return min_area_frac * float(frame_wh[0]) * float(frame_wh[1])
    return min_area_px


def fire_qualifies(xyxy: Sequence[float], conf: float, frame_wh: Optional[Tuple[int, int]] = None,
                   min_conf: float = FIRE_MIN_CONF, min_area_frac: float = FIRE_MIN_AREA_FRAC,
                   min_area_px: float = FIRE_MIN_AREA_PX) -> bool:
    """True when one detection is confident and large enough to count towards an alert."""
    x1, y1, x2, y2 = (float(v) for v in xyxy[:4])
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return float(conf) >= min_conf and area >= fire_min_area(frame_wh, min_area_frac, min_area_px)


def fire_hit(
    detections: Iterable[Tuple[Sequence[float], float, str]],
    frame_wh: Optional[Tuple[int, int]] = None,
    min_conf: float = FIRE_MIN_CONF,
    min_area_frac: float = FIRE_MIN_AREA_FRAC,
    min_area_px: float = FIRE_MIN_AREA_PX,
) -> Optional[Tuple[Tuple[float, float, float, float], float, str]]:
    """Best qualifying detection of one inference, or None.

    A detection qualifies when confidence >= min_conf and its box area is at
    least min_area_frac of the frame (or min_area_px when the frame size is
    unknown).
    """
    best = None
    for xyxy, conf, cls in detections:
        if not fire_qualifies(xyxy, conf, frame_wh, min_conf, min_area_frac, min_area_px):
            continue
        if best is None or float(conf) > best[1]:
            best = (tuple(float(v) for v in xyxy[:4]), float(conf), str(cls))
    return best


@dataclass
class FireState:
    """Per-camera fire alert state (k-of-n raise, timed clear, cooldown)."""

    window: Deque[bool] = field(default_factory=lambda: deque(maxlen=FIRE_WINDOW))
    active: bool = False
    incident_seq: int = 0
    started_at: Optional[float] = None
    last_hit_at: Optional[float] = None
    cleared_at: Optional[float] = None
    peak_conf: float = 0.0
    hits: int = 0
    retriggers: int = 0
    class_name: Optional[str] = None
    bbox_xyxy: Optional[Tuple[float, float, float, float]] = None


def fire_step(
    state: FireState,
    ts: float,
    hit: Optional[Tuple[Tuple[float, float, float, float], float, str]],
    k: int = FIRE_HITS_REQUIRED,
    clear_s: float = FIRE_CLEAR_S,
    cooldown_s: float = FIRE_COOLDOWN_S,
) -> Optional[str]:
    """Feed one inference result. Returns "start", "reopen", "clear" or None.

    * raise: >= k hits in the last FIRE_WINDOW inferences while not active.
      If the previous incident cleared less than cooldown_s ago the same
      incident is re-opened ("reopen") instead of a new alert ("start").
    * clear: active and no hit for clear_s seconds.
    """
    state.window.append(hit is not None)
    if hit is not None:
        xyxy, conf, cls = hit
        if state.active:
            state.last_hit_at = ts
            state.hits += 1
            if conf >= state.peak_conf:
                state.peak_conf = conf
                state.class_name = cls
                state.bbox_xyxy = xyxy
            return None
        if sum(state.window) >= k:
            reopen = state.cleared_at is not None and ts - state.cleared_at < cooldown_s
            state.active = True
            state.last_hit_at = ts
            if reopen:
                state.retriggers += 1
                state.cleared_at = None
                if conf >= state.peak_conf:
                    state.peak_conf, state.class_name, state.bbox_xyxy = conf, cls, xyxy
                state.hits += 1
                return "reopen"
            state.incident_seq += 1
            state.started_at = ts
            state.cleared_at = None
            state.peak_conf = conf
            state.class_name = cls
            state.bbox_xyxy = xyxy
            state.hits = sum(state.window)
            state.retriggers = 0
            return "start"
        return None
    return fire_check_clear(state, ts, clear_s)


def fire_check_clear(state: FireState, ts: float, clear_s: float = FIRE_CLEAR_S) -> Optional[str]:
    """Clear an active alert once clear_s has passed without a hit."""
    if state.active and state.last_hit_at is not None and ts - state.last_hit_at >= clear_s:
        state.active = False
        state.cleared_at = ts
        state.window.clear()
        return "clear"
    return None


def xyxy_to_xywh(xyxy: Sequence[float]) -> List[float]:
    x1, y1, x2, y2 = (float(v) for v in xyxy[:4])
    return [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)]


# ---------------------------------------------------------------------------
# Emotion aggregation
# ---------------------------------------------------------------------------


def bucket_start(ts: float, bucket_s: int = EMOTION_BUCKET_S) -> float:
    return math.floor(ts / bucket_s) * bucket_s


def sentiment_from_counts(counts: Dict[str, int]) -> Optional[float]:
    """Mean valence of classified faces in [-1, 1]; None with no faces.

    happy = +1; neutral and surprise = 0; sad, angry, fear, disgust and
    contempt = -1. Labels outside EMOTION_VALENCE are ignored.
    """
    total = 0
    acc = 0.0
    for label, n in counts.items():
        if label in EMOTION_VALENCE and n > 0:
            total += n
            acc += EMOTION_VALENCE[label] * n
    if total == 0:
        return None
    return round(acc / total, 3)


def dominant_label(counts: Dict[str, int]) -> Optional[str]:
    valid = {k: v for k, v in counts.items() if v > 0}
    if not valid:
        return None
    return max(sorted(valid), key=lambda k: valid[k])


# ---------------------------------------------------------------------------
# Store vibe
# ---------------------------------------------------------------------------

# Weighted mean of measured components, each normalised to 0..1. A component
# that was not measured (e.g. no crowd zone with a capacity, no emotion model)
# is left out and the remaining weights are renormalised. With no measured
# component there is no score.
VIBE_WEIGHTS: Dict[str, float] = {
    "occupancy": 0.30,    # occupancy / total capacity of crowd zones (capped at 1)
    "flow": 0.25,         # arrivals per minute / VIBE_FLOW_REF_PER_MIN (capped at 1)
    "engagement": 0.25,   # engaged visits / visits in shelf zones (last 15 min)
    "sentiment": 0.20,    # (mean valence + 1) / 2, only with a real emotion model
}
VIBE_FLOW_REF_PER_MIN = 10.0
VIBE_LABELS = ((20.0, "Quiet"), (40.0, "Calm"), (60.0, "Steady"), (80.0, "Energetic"), (101.0, "Buzzing"))


def vibe_label(score: float) -> str:
    for upper, label in VIBE_LABELS:
        if score < upper:
            return label
    return VIBE_LABELS[-1][1]


def store_vibe(
    occupancy_pct: Optional[float] = None,
    arrivals_per_min: Optional[float] = None,
    engaged_rate: Optional[float] = None,
    sentiment: Optional[float] = None,
    weights: Dict[str, float] = VIBE_WEIGHTS,
    flow_ref: float = VIBE_FLOW_REF_PER_MIN,
) -> Optional[dict]:
    """Store vibe 0..100 from measured signals only, with its components.

    Returns None when nothing was measured. Otherwise
    {"score", "label", "components": {name: {"raw", "normalized", "weight",
    "contribution"}}, "weights_used"} where contributions sum to the score.
    """
    norm: Dict[str, Tuple[float, float]] = {}
    if occupancy_pct is not None:
        norm["occupancy"] = (occupancy_pct, min(max(occupancy_pct, 0.0), 1.0))
    if arrivals_per_min is not None:
        norm["flow"] = (arrivals_per_min, min(max(arrivals_per_min / flow_ref, 0.0), 1.0))
    if engaged_rate is not None:
        norm["engagement"] = (engaged_rate, min(max(engaged_rate, 0.0), 1.0))
    if sentiment is not None:
        norm["sentiment"] = (sentiment, min(max((sentiment + 1.0) / 2.0, 0.0), 1.0))
    if not norm:
        return None
    wsum = sum(weights[k] for k in norm)
    components = {}
    score = 0.0
    for k, (raw, n) in norm.items():
        w = weights[k] / wsum
        contrib = 100.0 * w * n
        score += contrib
        components[k] = {
            "raw": round(float(raw), 4),
            "normalized": round(n, 4),
            "weight": round(w, 4),
            "contribution": round(contrib, 2),
        }
    score = round(score, 1)
    return {
        "score": score,
        "label": vibe_label(score),
        "components": components,
        "weights_used": {k: round(weights[k] / wsum, 4) for k in norm},
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def count_in_window(sorted_ts: List[float], start: float, end: float) -> int:
    """Number of timestamps in [start, end] from an ascending list."""
    return bisect.bisect_right(sorted_ts, end) - bisect.bisect_left(sorted_ts, start)
