"""Pure unit tests for ZoneAnalyticsEngine (synthetic tracks, no DB/models)."""

from __future__ import annotations

import pytest

from app.analytics.engine import TrackObs, ZoneAnalyticsEngine

W, H = 1000, 1000
CAM = "1"

SHELF = {"id": 1, "name": "Shelf A", "zone_type": "shelf", "points": [[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]]}
QUEUE = {"id": 2, "name": "Queue", "zone_type": "queue", "points": [[0.4, 0.0], [0.6, 0.0], [0.6, 0.4], [0.4, 0.4]]}
COUNTER = {"id": 3, "name": "Till", "zone_type": "counter", "points": [[0.4, 0.5], [0.6, 0.5], [0.6, 0.7], [0.4, 0.7]]}
CROWD = {"id": 4, "name": "Floor", "zone_type": "crowd", "capacity": 2,
         "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
LINE = {"id": 5, "name": "Door", "zone_type": "entry_line", "points": [[0.7, 0.8], [1.0, 0.8]], "config": {"in_sign": -1}}


def _track(tid, fx, fy, gid=None):
    """Track whose foot point is (fx, fy) in normalized coordinates."""
    x, y = fx * W, fy * H
    return TrackObs(track_id=tid, global_id=gid, xyxy=(x - 20, y - 100, x + 20, y), conf=0.9)


def _drive(engine, steps, dt=0.5, t0=1000.0, cam=CAM):
    """steps: list of (duration_s, {tid: (fx, fy) or None}). Returns all events, final ts."""
    events, ts = [], t0
    for dur, pos in steps:
        n = int(round(dur / dt))
        for _ in range(n):
            tracks = [_track(t, *p) for t, p in pos.items() if p is not None]
            events += engine.on_tracks(cam, ts, (W, H), tracks)
            ts += dt
    return events, ts


def _of(events, etype):
    return [e for e in events if e["type"] == etype]


def test_shelf_visit_and_pass():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [SHELF])
    ev, ts = _drive(eng, [
        (12.0, {1: (0.1, 0.2), 2: None}),       # 1 dwells 12 s on the shelf
        (2.0, {1: (0.8, 0.8), 2: (0.15, 0.15)}),  # 1 leaves; 2 passes through
        (2.0, {1: (0.8, 0.8), 2: (0.8, 0.9)}),
    ])
    visits = _of(ev, "zone_visit")
    passes = _of(ev, "zone_pass")
    assert len(visits) == 1 and visits[0]["track_id"] == 1
    assert visits[0]["duration_s"] == pytest.approx(11.5, abs=0.6)
    assert visits[0]["payload"]["engaged"] is True
    assert len(passes) == 1 and passes[0]["track_id"] == 2
    snap = eng.snapshot(CAM)
    z = snap["zones"][0]
    assert z["visits"] == 1 and z["engaged_visits"] == 1 and z["passers"] == 2


def test_queue_wait_service_and_abandon():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [QUEUE, COUNTER])
    ev, ts = _drive(eng, [
        (8.0, {1: (0.5, 0.2), 2: (0.45, 0.3)}),   # both queue 8 s
        (6.0, {1: (0.5, 0.6), 2: (0.45, 0.3)}),   # 1 at the counter, 2 still queuing
        (2.0, {1: (0.9, 0.9), 2: (0.9, 0.1)}),    # 1 done; 2 walks off (abandons)
        (12.0, {1: (0.9, 0.9), 2: (0.9, 0.1)}),
    ])
    waits = _of(ev, "queue_wait")
    services = _of(ev, "service")
    abandons = _of(ev, "queue_abandon")
    assert len(waits) == 1 and waits[0]["track_id"] == 1
    assert waits[0]["duration_s"] == pytest.approx(8.0, abs=1.0)
    assert waits[0]["zone_id"] == 2 and waits[0]["payload"]["counter_zone_id"] == 3
    assert len(services) == 1 and services[0]["duration_s"] == pytest.approx(5.5, abs=1.0)
    assert len(abandons) == 1 and abandons[0]["track_id"] == 2


def test_queue_length_is_smoothed_median():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [QUEUE, COUNTER])
    _drive(eng, [(4.0, {1: (0.5, 0.1), 2: (0.5, 0.2), 3: (0.5, 0.3)})])
    # One frame where a detection blinks out must not change the reported length.
    eng.on_tracks(CAM, 1004.0, (W, H), [_track(1, 0.5, 0.1)])
    lane = eng.snapshot(CAM)["lanes"][0]
    assert lane["queue_length"] == 3


def test_crowd_level_and_occupancy():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [CROWD])
    ev, _ = _drive(eng, [(6.0, {1: (0.2, 0.2)}), (6.0, {1: (0.2, 0.2), 2: (0.7, 0.7)})])
    z = eng.snapshot(CAM)["zones"][0]
    assert z["count"] == 2 and z["level"] == "critical" and z["pct"] == pytest.approx(1.0)
    levels = _of(ev, "crowd_level")
    assert levels and levels[-1]["payload"]["level"] == "critical"
    samples = _of(ev, "zone_sample")
    assert any(s["zone_id"] is None for s in samples)   # camera occupancy sample
    assert any(s["zone_id"] == 4 for s in samples)


def test_entry_line_counts_inward_once():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [LINE])
    # Walk upward across y=0.8 (from 0.95 to 0.6), standing on the line for a while.
    steps = [(1.0, {1: (0.85, y)}) for y in (0.95, 0.9, 0.801, 0.799, 0.801, 0.75, 0.6)]
    ev, _ = _drive(eng, steps)
    crosses = _of(ev, "line_cross")
    assert len(crosses) == 1
    # in_sign = -1: moving to the negative side of a->b is "in".
    assert crosses[0]["value"] in (1, -1)
    snap = eng.snapshot(CAM)["zones"][0]
    assert snap["entries"] + snap["exits"] == 1


def test_track_lost_finalises_and_track_visit():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [SHELF])
    _, ts = _drive(eng, [(5.0, {7: (0.1, 0.1)})])
    ev = eng.on_track_lost(CAM, ts, 7)
    assert len(_of(ev, "zone_visit")) == 1
    tv = _of(ev, "track_visit")
    assert len(tv) == 1 and tv[0]["duration_s"] == pytest.approx(4.5, abs=0.6)


def test_set_zones_keeps_state_for_unchanged_geometry():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [SHELF])
    _, ts = _drive(eng, [(5.0, {1: (0.1, 0.1)})])
    eng.set_zones(CAM, [{**SHELF, "name": "Renamed"}, QUEUE])   # rename only
    ev, _ = _drive(eng, [(5.0, {1: (0.1, 0.1)}), (2.0, {1: (0.9, 0.9)})], t0=ts)
    visits = _of(ev, "zone_visit")
    assert len(visits) == 1 and visits[0]["duration_s"] == pytest.approx(9.5, abs=0.6)


def test_fire_k_of_n_through_engine():
    eng = ZoneAnalyticsEngine()
    eng.on_tracks(CAM, 0.0, (640, 480), [])
    box = [((0, 0, 100, 100), 0.85, "fire")]
    out = []
    for t in range(3):
        out += eng.on_fire(CAM, float(t), box)
    assert [e["payload"]["action"] for e in out] == ["start"]
    assert eng.snapshot(CAM)["fire"]["active"] is True
    out = eng.on_fire(CAM, 40.0, [])
    assert [e["payload"]["action"] for e in out] == ["clear"]


def test_faces_aggregate_into_buckets():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [SHELF])
    eng.on_tracks(CAM, 600.0, (W, H), [_track(1, 0.1, 0.2)])
    ev = eng.on_faces(CAM, 601.0, [((90, 110, 110, 130), "happy", 0.9), ((500, 500, 520, 520), "sad", 0.8),
                                   ((1, 1, 2, 2), "not-a-label", 0.9)])
    ev += eng.on_camera_stopped(CAM, 700.0)
    buckets = [e for e in _of(ev, "emotion_bucket")]
    cam_level = [b for b in buckets if b["zone_id"] is None]
    assert cam_level and cam_level[-1]["payload"]["counts"] == {"happy": 1, "sad": 1}
    assert cam_level[-1]["value"] == 0.0


def test_no_tracks_means_no_fake_events():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [SHELF, QUEUE, COUNTER])
    ev, _ = _drive(eng, [(20.0, {})])
    assert not [e for e in ev if e["type"] not in ("zone_sample", "crowd_level", "vibe_sample")]
    assert all(s["value"] == 0 for s in _of(ev, "zone_sample"))


# ---------------------------------------------------------------- heatmap

def _heat_total(events, eng, cam=CAM):
    total = sum(e["value"] for e in _of(events, "heatmap_bucket"))
    live = eng.heatmap_open_bucket(cam)
    return total + (sum(v for _, v in live["cells"]) if live else 0.0)


def test_heatmap_conserves_person_seconds_across_buckets():
    from app.analytics.engine import HEATMAP_GRID
    eng = ZoneAnalyticsEngine()
    events, _ = _drive(eng, [(130.0, {1: (0.5, 0.5)})], t0=1200.0)
    buckets = _of(events, "heatmap_bucket")
    assert [b["ts"] for b in buckets] == [1200.0, 1260.0]       # closed minutes only
    gw, gh = HEATMAP_GRID
    idx = int(0.5 * gh) * gw + int(0.5 * gw)
    assert all(b["payload"]["cells"][0][0] == idx and len(b["payload"]["cells"]) == 1 for b in buckets)
    assert all(b["payload"]["tracks"] == 1 for b in buckets)
    # first frame has no interval; every later 0.5 s frame adds 0.5 person-seconds
    assert _heat_total(events, eng) == pytest.approx(130.0 - 0.5)


def test_heatmap_caps_frame_gaps():
    eng = ZoneAnalyticsEngine()
    eng.on_tracks(CAM, 1200.0, (W, H), [_track(1, 0.2, 0.2)])
    eng.on_tracks(CAM, 1210.0, (W, H), [_track(1, 0.2, 0.2)])  # 10 s stall
    assert _heat_total([], eng) == pytest.approx(1.0)


def test_heatmap_two_people_and_stop_flush():
    eng = ZoneAnalyticsEngine()
    events, ts = _drive(eng, [(10.0, {1: (0.1, 0.1), 2: (0.9, 0.9)})], t0=1200.0)
    assert not _of(events, "heatmap_bucket")
    stopped = eng.on_camera_stopped(CAM, ts)
    (b,) = _of(stopped, "heatmap_bucket")
    assert b["payload"]["tracks"] == 2 and len(b["payload"]["cells"]) == 2
    assert b["value"] == pytest.approx(2 * (10.0 - 0.5))
    assert eng.heatmap_open_bucket(CAM) is None


def test_heatmap_empty_frames_emit_nothing():
    eng = ZoneAnalyticsEngine()
    events, ts = _drive(eng, [(120.0, {})], t0=1200.0)
    assert not _of(events + eng.on_camera_stopped(CAM, ts), "heatmap_bucket")


def test_vibe_skips_occupancy_before_warmup():
    """A session's first frame has no confirmed tracks yet; it must not be stored as an empty store."""
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [CROWD])
    ev, _ = _drive(eng, [(2.0, {}), (70.0, {1: (0.2, 0.2)})])
    vibes = _of(ev, "vibe_sample")
    assert vibes, "a vibe sample is due after the warm-up"
    assert all(v["ts"] - 1000.0 >= 10.0 for v in vibes)
    occ = vibes[0]["payload"]["components"]["occupancy"]
    assert occ.get("value", occ.get("normalized", 0)) > 0, occ


DWELL = {"id": 6, "name": "Aisle", "zone_type": "crowd", "capacity": 50, "config": {"dwell_alert_seconds": 10},
         "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}


def _dwell_opens(events):
    return [e for e in _of(events, "alert")
            if e["payload"]["alert_type"] == "dwell_overrun" and e["payload"]["action"] == "open"]


def test_dwell_alert_carries_subject_box_for_evidence():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [DWELL])
    ev, _ = _drive(eng, [(12.0, {1: (0.5, 0.5)})])
    opens = _dwell_opens(ev)
    assert len(opens) == 1
    assert opens[0]["bbox_xyxy"] == pytest.approx([480.0, 400.0, 520.0, 500.0])
    assert opens[0]["frame_wh"] == [W, H]


def test_dwell_alert_not_repeated_when_same_person_changes_track_id():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [DWELL])
    ev, _ = _drive(eng, [
        (12.0, {1: (0.5, 0.5)}),                 # track 1 overruns
        (12.0, {2: (0.5, 0.5)}),                 # ID switch, same identity, still inside
    ])
    # Without a global identity the second track is a separate visit.
    assert len(_dwell_opens(ev)) == 2

    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [DWELL])
    seq = []
    ts = 1000.0
    for tid, n in ((1, 24), (2, 24)):
        for _ in range(n):
            seq += eng.on_tracks(CAM, ts, (W, H), [_track(tid, 0.5, 0.5, gid="g1")])
            ts += 0.5
    assert len(_dwell_opens(seq)) == 1
    # A different person in the same zone still gets their own alert.
    for _ in range(24):
        seq += eng.on_tracks(CAM, ts, (W, H), [_track(3, 0.3, 0.3, gid="g2")])
        ts += 0.5
    assert len(_dwell_opens(seq)) == 2


def test_dwell_same_person_may_alert_again_after_leaving_long_enough():
    from app.analytics.engine import DWELL_SAME_PERSON_GAP_S

    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [DWELL])
    ev, ts = [], 1000.0
    for _ in range(24):
        ev += eng.on_tracks(CAM, ts, (W, H), [_track(1, 0.5, 0.5, gid="g1")])
        ts += 0.5
    eng.on_track_lost(CAM, ts, 1)
    ts += DWELL_SAME_PERSON_GAP_S + 1
    for _ in range(24):
        ev += eng.on_tracks(CAM, ts, (W, H), [_track(2, 0.5, 0.5, gid="g1")])
        ts += 0.5
    assert len(_dwell_opens(ev)) == 2


def test_flow_counts_people_not_track_ids():
    """ID switches, the same person on a second camera and sub-second flicker tracks add no arrivals."""
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [CROWD])
    eng.set_zones("2", [])
    plan = [  # (seconds, cam 1 tracks, cam 2 tracks); a track is (tid, gid)
        (20.0, [(1, "g1")], []),
        (20.0, [(2, "g1")], [(1, "g1")]),          # ID switch on cam 1; g1 also seen on cam 2
        (0.5, [(9, None)], []),                    # flicker: one detection blip
        (40.0, [(3, "g2")], [(1, "g1")]),
    ]
    ev, ts = [], 1000.0
    for dur, c1, c2 in plan:
        for _ in range(int(round(dur / 0.5))):
            ev += eng.on_tracks(CAM, ts, (W, H), [_track(t, 0.2, 0.2, gid=g) for t, g in c1])
            ev += eng.on_tracks("2", ts, (W, H), [_track(t, 0.6, 0.6, gid=g) for t, g in c2])
            ts += 0.5
    vibes = _of(ev, "vibe_sample")
    assert vibes
    assert vibes[-1]["payload"]["inputs"]["arrivals"] == 2


def test_flow_without_global_ids_counts_each_track():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [CROWD])
    ev, _ = _drive(eng, [(20.0, {1: (0.2, 0.2)}), (50.0, {1: None, 2: (0.3, 0.3)})])
    assert _of(ev, "vibe_sample")[-1]["payload"]["inputs"]["arrivals"] == 2


RESTRICTED = {"id": 7, "name": "Stockroom", "zone_type": "restricted",
              "points": [[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]]}


def _intrusions(events, action="open"):
    return [e for e in _of(events, "alert")
            if e["payload"]["alert_type"] == "intrusion" and e["payload"]["action"] == action]


def test_restricted_zone_raises_one_intrusion_with_evidence_box_and_closes_on_exit():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [RESTRICTED])
    ev, _ = _drive(eng, [
        (6.0, {1: (0.1, 0.2)}),                  # inside 6 s: one alert, not one per frame
        (8.0, {1: (0.8, 0.8)}),                  # leaves
    ])
    opens = _intrusions(ev)
    assert len(opens) == 1 and opens[0]["track_id"] == 1
    assert opens[0]["bbox_xyxy"] and opens[0]["frame_wh"] == [W, H]
    assert opens[0]["payload"]["threshold_s"] == pytest.approx(2.0)
    closes = _intrusions(ev, "close")
    assert len(closes) == 1 and closes[0]["payload"]["open_event_uid"] == opens[0]["event_uid"]
    assert not [e for e in _of(ev, "alert") if e["payload"]["alert_type"] == "dwell_overrun"]


def test_restricted_zone_ignores_brief_edge_contact():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [RESTRICTED])
    ev, _ = _drive(eng, [
        (1.0, {1: (0.1, 0.2)}),                  # 1 s clip of the zone edge
        (8.0, {1: (0.8, 0.8)}),
    ])
    assert _intrusions(ev) == []


def test_restricted_zone_same_person_new_track_id_alerts_once():
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [RESTRICTED])
    t1 = [_track(1, 0.1, 0.2, gid=42)]
    t2 = [_track(2, 0.1, 0.2, gid=42)]
    ev, ts = [], 1000.0
    for i in range(20):
        ev += eng.on_tracks(CAM, ts, (W, H), t1 if i < 10 else t2)
        ts += 0.5
    assert len(_intrusions(ev)) == 1
