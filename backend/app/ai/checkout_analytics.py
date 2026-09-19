"""
OmniTrack AI — Checkout Counter Analytics

One box per till. Everyone inside it is queuing; the time between entering and
leaving is what we can measure.

That interval is NOT service time, and the field is named accordingly. A box
drawn around a queue cannot tell waiting apart from being served — calling it
"service time" would overstate what a single region can know. `time_in_lane` is
wait and service combined.
"""

import time
from typing import List, Dict, Any, Optional

# Same ray-casting test and point scaling the ROI activity already uses, so a
# polygon means the same thing everywhere in the pipeline.
from app.ai.regions import _scaled_points, point_in_polygon
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger


@dataclass
class CheckoutLane:
    """
    One till's counting area.

    `points` wins when present. A rectangle cannot describe a lane on a camera
    where the tills run diagonally — the smallest box around one lane swallows
    its neighbours — so a polygon is the only shape that works on most real
    checkout angles. `bbox` stays for the simple head-on case and as the
    fallback when a polygon has too few points to enclose anything.
    """

    lane_id: str
    camera_id: int
    bbox: List[float]                                    # [x1, y1, x2, y2]
    name: str = ""
    points: Optional[List[Dict[str, float]]] = None      # [{x, y}, ...]


@dataclass
class ServiceEvent:
    """One customer's visit to a lane, from entering the box to leaving it."""
    track_id: int
    lane_id: str
    lane_name: str
    camera_id: int
    enter_time: float
    exit_time: Optional[float] = None
    time_in_lane: float = 0.0


class CheckoutAnalytics:
    """
    Checkout counter analytics engine.
    Tracks queue lengths, service times, and throughput per lane.
    """

    def __init__(self, lanes: Optional[List[CheckoutLane]] = None):
        self.lanes: List[CheckoutLane] = lanes or []
        self.active_queue: Dict[str, Dict[int, float]] = defaultdict(dict)  # lane_id -> {track_id: enter_time}
        self.completed_services: Dict[str, List[ServiceEvent]] = defaultdict(list)
        self.hourly_throughput: Dict[str, List[int]] = defaultdict(list)

    def add_lane(self, lane: CheckoutLane):
        self.lanes.append(lane)

    def set_lanes_for_camera(self, camera_id: int, lanes: List[CheckoutLane]) -> None:
        """
        Replace one camera's lanes. Registering a job twice would otherwise
        append a second copy of every lane and double each queue count.
        """
        self.clear_lanes(camera_id)
        self.lanes.extend(lanes)

    def clear_lanes(self, camera_id: int) -> None:
        """Drop a camera's lanes and any queue state they were holding."""
        doomed = {l.lane_id for l in self.lanes if l.camera_id == camera_id}
        self.lanes = [l for l in self.lanes if l.camera_id != camera_id]
        for lane_id in doomed:
            self.active_queue.pop(lane_id, None)
            self.completed_services.pop(lane_id, None)

    def drain_completed(self) -> List[ServiceEvent]:
        """
        Hand over finished visits and forget them.

        Without this the list grows for the life of the process and every flush
        would re-insert the same rows.
        """
        out: List[ServiceEvent] = []
        for events in self.completed_services.values():
            out.extend(events)
        self.completed_services.clear()
        return out

    def update(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Flexible update entrypoint.
        Supported call shapes:
          - update(tracks)                       (legacy)
          - update(camera_id, tracks)            (pipeline)
          - update(camera_id, tracks, timestamp) (pipeline)

        When a camera_id is passed we only consider lanes belonging to that
        camera. Returns a camera-scoped summary dict.
        """
        # Positionals first, then let explicit keywords override. The previous
        # version branched on `if kwargs:` and read camera/tracks only from
        # keywords — so passing any OTHER keyword (scale) silently discarded the
        # positional arguments and the engine counted an empty frame.
        camera_id: Optional[int] = None
        tracks: List[Dict[str, Any]] = []
        if len(args) == 1:
            tracks = args[0] or []
        elif len(args) >= 2:
            camera_id, tracks = args[0], args[1] or []

        if "camera_id" in kwargs:
            camera_id = kwargs["camera_id"]
        if "tracks" in kwargs or "detections" in kwargs:
            tracks = kwargs.get("tracks") or kwargs.get("detections") or []

        # Regions are drawn against the browser's frame size; the pipeline may
        # decode smaller frames, so the caller passes the factor between them.
        scale = kwargs.get("scale") or (1.0, 1.0)

        now = time.time()
        metrics = []
        lanes = [l for l in self.lanes if camera_id is None or l.camera_id == camera_id]

        for lane in lanes:
            # Count persons in lane bbox
            persons_in_lane = self._get_persons_in_lane(tracks, lane, scale)
            current_ids = {t.get("track_id") for t in persons_in_lane if t.get("track_id") is not None}

            # Track new entries
            for tid in current_ids:
                if tid not in self.active_queue[lane.lane_id]:
                    self.active_queue[lane.lane_id][tid] = now

            # Track exits (service completed)
            exited = set(self.active_queue[lane.lane_id].keys()) - current_ids
            for tid in exited:
                enter = self.active_queue[lane.lane_id].pop(tid)
                svc = ServiceEvent(
                    track_id=tid, lane_id=lane.lane_id,
                    lane_name=lane.name or lane.lane_id,
                    camera_id=lane.camera_id,
                    enter_time=enter, exit_time=now,
                    time_in_lane=now - enter,
                )
                self.completed_services[lane.lane_id].append(svc)

            # Compute metrics
            queue_length = len(self.active_queue[lane.lane_id])
            recent = self.completed_services[lane.lane_id][-50:]
            avg_time = (
                sum(s.time_in_lane for s in recent) / len(recent)
                if recent else 0.0
            )
            # Throughput: customers per hour (estimated)
            if recent and len(recent) >= 2:
                time_span = recent[-1].exit_time - recent[0].enter_time
                throughput = len(recent) / max(time_span / 3600, 0.001)
            else:
                throughput = 0.0

            wait_estimate = avg_time * queue_length

            metrics.append({
                "lane_id": lane.lane_id,
                "lane_name": lane.name or lane.lane_id,
                "camera_id": lane.camera_id,
                "queue_length": queue_length,
                "avg_time_in_lane": round(avg_time, 2),
                "throughput": round(throughput, 2),
                "current_wait_estimate": round(wait_estimate, 2),
                "total_served": len(self.completed_services[lane.lane_id]),
            })

        total_wait = sum(m["current_wait_estimate"] for m in metrics)
        total_queue = sum(m["queue_length"] for m in metrics)
        return {
            "camera_id": camera_id,
            "lanes": metrics,
            "total_queue": total_queue,
            "avg_wait": round(total_wait / len(metrics), 2) if metrics else 0.0,
        }

    def _get_persons_in_lane(
        self, tracks: List[Dict], lane: CheckoutLane, scale=(1.0, 1.0),
    ) -> List[Dict]:
        sx, sy = scale
        poly = _scaled_points(lane.points, sx, sy) if lane.points else []
        use_polygon = len(poly) >= 3

        if not use_polygon:
            lx1, ly1, lx2, ly2 = lane.bbox
            lx1, lx2 = lx1 * sx, lx2 * sx
            ly1, ly2 = ly1 * sy, ly2 * sy

        result = []
        for t in tracks:
            bbox = t.get("bbox") or t.get("box") or []
            if len(bbox) < 4:
                continue
            bx, by, bw, bh = bbox[0], bbox[1], bbox[2], bbox[3]
            cx, cy = bx + bw / 2, by + bh / 2
            inside = (
                point_in_polygon((cx, cy), poly) if use_polygon
                else (lx1 <= cx <= lx2 and ly1 <= cy <= ly2)
            )
            if inside:
                result.append(t)
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Get overall checkout summary."""
        all_services = []
        for lane_svcs in self.completed_services.values():
            all_services.extend(lane_svcs)

        total_served = len(all_services)
        avg_time = (
            sum(s.time_in_lane for s in all_services) / len(all_services)
            if all_services else 0.0
        )
        return {
            "total_lanes": len(self.lanes),
            "total_served": total_served,
            "overall_avg_time_in_lane": round(avg_time, 2),
        }

    def reset(self):
        self.active_queue.clear()
        self.completed_services.clear()


# Back-compat alias — the pipeline imports `CheckoutAnalyzer`.
CheckoutAnalyzer = CheckoutAnalytics
