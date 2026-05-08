"""
OmniTrack AI — Checkout Counter Analytics
Queue monitoring: lane-based counting, service time, throughput (customers/hr).
"""

import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger


@dataclass
class CheckoutLane:
    lane_id: str
    camera_id: int
    bbox: List[float]  # [x1, y1, x2, y2]
    name: str = ""


@dataclass
class ServiceEvent:
    track_id: int
    lane_id: str
    enter_time: float
    exit_time: Optional[float] = None
    service_time: float = 0.0


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
        camera_id: Optional[int] = None
        tracks: List[Dict[str, Any]] = []
        if kwargs:
            camera_id = kwargs.get("camera_id")
            tracks = kwargs.get("tracks") or kwargs.get("detections") or []
        elif len(args) == 1:
            tracks = args[0] or []
        elif len(args) >= 2:
            camera_id, tracks = args[0], args[1] or []

        now = time.time()
        metrics = []
        lanes = [l for l in self.lanes if camera_id is None or l.camera_id == camera_id]

        for lane in lanes:
            # Count persons in lane bbox
            persons_in_lane = self._get_persons_in_lane(tracks, lane)
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
                    enter_time=enter, exit_time=now,
                    service_time=now - enter,
                )
                self.completed_services[lane.lane_id].append(svc)

            # Compute metrics
            queue_length = len(self.active_queue[lane.lane_id])
            recent = self.completed_services[lane.lane_id][-50:]
            avg_service = (
                sum(s.service_time for s in recent) / len(recent)
                if recent else 0.0
            )
            # Throughput: customers per hour (estimated)
            if recent and len(recent) >= 2:
                time_span = recent[-1].exit_time - recent[0].enter_time
                throughput = len(recent) / max(time_span / 3600, 0.001)
            else:
                throughput = 0.0

            wait_estimate = avg_service * queue_length

            metrics.append({
                "lane_id": lane.lane_id,
                "lane_name": lane.name or lane.lane_id,
                "camera_id": lane.camera_id,
                "queue_length": queue_length,
                "avg_service_time": round(avg_service, 2),
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

    def _get_persons_in_lane(self, tracks: List[Dict], lane: CheckoutLane) -> List[Dict]:
        lx1, ly1, lx2, ly2 = lane.bbox
        result = []
        for t in tracks:
            bbox = t.get("bbox") or t.get("box") or []
            if len(bbox) < 4:
                continue
            bx, by, bw, bh = bbox[0], bbox[1], bbox[2], bbox[3]
            cx, cy = bx + bw / 2, by + bh / 2
            if lx1 <= cx <= lx2 and ly1 <= cy <= ly2:
                result.append(t)
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Get overall checkout summary."""
        all_services = []
        for lane_svcs in self.completed_services.values():
            all_services.extend(lane_svcs)

        total_served = len(all_services)
        avg_time = (
            sum(s.service_time for s in all_services) / len(all_services)
            if all_services else 0.0
        )
        return {
            "total_lanes": len(self.lanes),
            "total_served": total_served,
            "overall_avg_service_time": round(avg_time, 2),
        }

    def reset(self):
        self.active_queue.clear()
        self.completed_services.clear()


# Back-compat alias — the pipeline imports `CheckoutAnalyzer`.
CheckoutAnalyzer = CheckoutAnalytics
