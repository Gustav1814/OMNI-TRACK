"""Re-ID quality gate, identity gallery, Hungarian assignment and topology gating (no model needed)."""

import numpy as np
import pytest

from app.ai.reid import EMBEDDING_DIM, Identity, IdentityGallery, QualityConfig, box_iou_matrix, crop_quality, l2n
from app.services.track_coordinator import (
    CoordinatorConfig,
    Topology,
    TrackCoordinator,
    parse_links,
)

RNG = np.random.default_rng(7)


def person(seed: int) -> np.ndarray:
    return l2n(np.random.default_rng(seed).normal(size=EMBEDDING_DIM).astype(np.float32))


def noisy(v: np.ndarray, s: float = 0.25) -> np.ndarray:
    return l2n(v + RNG.normal(scale=s / np.sqrt(EMBEDDING_DIM), size=EMBEDDING_DIM).astype(np.float32))


# ── quality gate ──────────────────────────────────────────────────


def test_quality_gate_reasons():
    cfg = QualityConfig()
    W, H = 1920, 1080
    assert crop_quality([100, 100, 160, 300], 0.9, (W, H), 0.0, cfg)[:2] == (True, "ok")
    assert crop_quality([100, 100, 160, 300], 0.5, (W, H), 0.0, cfg)[1] == "low_conf"
    assert crop_quality([100, 100, 140, 180], 0.9, (W, H), 0.0, cfg)[1] == "too_small"
    assert crop_quality([100, 100, 250, 300], 0.9, (W, H), 0.0, cfg)[1] == "aspect"
    assert crop_quality([0, 100, 60, 300], 0.9, (W, H), 0.0, cfg)[1] == "edge"
    assert crop_quality([100, 100, 160, 300], 0.9, (W, H), 0.5, cfg)[1] == "occluded"


def test_quality_gate_height_is_resolution_aware():
    cfg = QualityConfig(min_height_px=128, min_height_frac=0.25)
    assert cfg.min_height(1080) == 128
    assert cfg.min_height(288) == pytest.approx(72)
    assert crop_quality([100, 100, 130, 180], 0.9, (384, 288), 0.0, cfg)[0]  # 80 px tall on a 288p camera


def test_box_iou_matrix():
    a = np.array([[0, 0, 10, 10]], np.float32)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], np.float32)
    np.testing.assert_allclose(box_iou_matrix(a, b)[0], [1.0, 1 / 3, 0.0], atol=1e-5)


# ── identity / gallery ────────────────────────────────────────────


def test_identity_views_are_bounded_and_diverse():
    base = person(1)
    ident = Identity("PERSON-00001")
    for _ in range(40):
        ident.add_view(noisy(base, 2.0), max_views=8)
    assert len(ident.views) == 8
    assert not ident.add_view(ident.views[0], max_views=8)  # exact duplicate rejected
    assert ident.similarity(base) > 0.3


def test_gallery_ids_counter_and_eviction():
    g = IdentityGallery(max_identities=10, max_views=4)
    g.seed_counter(["PERSON-00041", "BAD", "PERSON-x"])
    gids = [g.create(person(i), ts=float(i), camera_id=1).global_id for i in range(12)]
    assert gids[0] == "PERSON-00042"
    assert g.evict(protected=[gids[0]]) == 2
    assert gids[0] in g and gids[1] not in g and gids[2] not in g
    hits = g.search(person(5), top_k=3)
    assert hits[0]["global_id"] == gids[5] and hits[0]["similarity"] == pytest.approx(1.0, abs=1e-4)


# ── topology ──────────────────────────────────────────────────────


def test_topology_default_and_configured():
    t = Topology(default_max_transit_s=300, reentry_max_s=1800)
    assert t.allowed(1, 2, 10) and not t.allowed(1, 2, 400) and not t.allowed(1, 2, -1)
    assert t.allowed(1, 1, 1000)
    t = Topology(parse_links([{"from": 1, "to": 2, "min_s": 5, "max_s": 60, "bidirectional": True}]), 300, 1800)
    assert t.allowed(1, 2, 30) and t.allowed(2, 1, 30)
    assert not t.allowed(1, 2, 2)  # faster than physically possible
    assert not t.allowed(1, 2, 90)
    assert not t.allowed(1, 3, 30)  # no link once a topology exists
    t = Topology(parse_links([{"from": 1, "to": 2, "max_s": 60, "overlapping": True}]), 300, 1800)
    assert t.overlapping(2, 1) and t.allowed(1, 2, -5)
    with pytest.raises(ValueError):
        parse_links([{"from": 1, "to": 2, "min_s": 10, "max_s": 5}])


# ── coordinator ───────────────────────────────────────────────────


def _feed(co, cam, tid, vec, t0, n=5, dt=0.2, noise=0.25):
    for k in range(n):
        ts = t0 + k * dt
        co.observe(cam, ts, [(tid, [0, 0, 50, 150], 0.9, (25.0, 150.0))], {tid: (0.9, noisy(vec, noise))})
    return t0 + (n - 1) * dt


def _cfg(**kw):
    base = dict(t_match=0.5, t_update=0.6, top_k=5, min_crops=2, max_wait_s=3.0)
    base.update(kw)
    return CoordinatorConfig(**base)


def test_new_identity_then_reentry_matches_same_id():
    co = TrackCoordinator(_cfg())
    a = person(10)
    ts = _feed(co, 1, 1, a, 0.0)
    ev = co.step(ts)
    assert len(ev) == 1 and ev[0]["reason"] == "new"
    gid = ev[0]["global_id"]
    co.on_track_lost(1, 1, ts + 3)
    ts = _feed(co, 2, 7, a, ts + 20)
    ev = co.step(ts)
    assert ev[0]["global_id"] == gid and ev[0]["reason"] == "match"


def test_async_embeddings_join_live_tracklets_and_skip_ended_ones():
    co = TrackCoordinator(_cfg())
    a = person(11)
    for k in range(5):  # tracks observed without crops (embedding still computing)
        co.observe(1, k * 0.2, [(1, [0, 0, 50, 150], 0.9, (25.0, 150.0)), (2, [60, 0, 50, 150], 0.9, (85.0, 150.0))], {})
    co.on_track_lost(1, 2, 1.0)
    added = sum(co.add_embeddings(1, 0.2 * k, {1: (0.9, noisy(a, 0.25)), 2: (0.9, noisy(a, 0.25))}) for k in range(5))
    assert added == 5  # track 2 ended before its crops arrived
    ev = co.step(1.0)
    assert len(ev) == 1 and ev[0]["track_id"] == 1 and ev[0]["reason"] == "new"


def test_hungarian_resolves_two_people_jointly():
    co = TrackCoordinator(_cfg())
    a, b = person(1), person(2)
    _feed(co, 1, 1, a, 0.0)
    ts = _feed(co, 1, 2, b, 0.0)
    gids = {e["track_id"]: e["global_id"] for e in co.step(ts)}
    co.on_track_lost(1, 1, ts + 1)
    co.on_track_lost(1, 2, ts + 1)
    _feed(co, 2, 11, b, ts + 30)
    ts2 = _feed(co, 2, 12, a, ts + 30)
    out = {e["track_id"]: e["global_id"] for e in co.step(ts2)}
    assert out == {11: gids[2], 12: gids[1]}


def test_visible_identity_is_never_given_to_a_second_track():
    co = TrackCoordinator(_cfg())
    a = person(3)
    ts = _feed(co, 1, 1, a, 0.0)
    gid = co.step(ts)[0]["global_id"]
    # same appearance on another camera while track 1 is still visible -> must be a new ID
    for k in range(5):
        t = ts + 0.2 * (k + 1)
        co.observe(1, t, [(1, [0, 0, 50, 150], 0.9, (25.0, 150.0))], {})
        co.observe(2, t, [(5, [0, 0, 50, 150], 0.9, (25.0, 150.0))], {5: (0.9, noisy(a))})
    ev = co.step(ts + 1.0)
    assert ev[0]["global_id"] != gid


def test_fragmented_local_track_takes_over_identity():
    co = TrackCoordinator(_cfg())
    a = person(4)
    ts = _feed(co, 1, 1, a, 0.0)
    gid = co.step(ts)[0]["global_id"]
    # track 1 is occluded (still in ByteTrack's buffer, not lost) and track 2 starts on the same person
    ts2 = _feed(co, 1, 2, a, ts + 1.0)
    ev = co.step(ts2)
    assert ev[0]["global_id"] == gid
    assert co.global_id(1, 1) is None and co.global_id(1, 2) == gid


def test_topology_blocks_impossible_transit():
    co = TrackCoordinator(_cfg())
    co.set_topology(parse_links([{"from": 1, "to": 2, "min_s": 30, "max_s": 120}]))
    a = person(5)
    ts = _feed(co, 1, 1, a, 0.0)
    gid = co.step(ts)[0]["global_id"]
    co.on_track_lost(1, 1, ts)
    ts2 = _feed(co, 2, 3, a, ts + 5)  # 5 s later: faster than min_s=30
    assert co.step(ts2)[0]["global_id"] != gid
    assert any(e["to_camera"] == 2 for e in co.expected_arrivals())


def _handoff_scene(bonus):
    """X leaves camera 1 (linked 1->2), Y leaves camera 2; a newcomer on 2 looks slightly more like Y."""
    co = TrackCoordinator(_cfg(handoff_bonus=bonus))
    co.set_topology(parse_links([{"from": 1, "to": 2, "min_s": 5, "max_s": 120}]))
    x, y = person(20), person(21)
    _feed(co, 1, 1, x, 0.0, noise=0.05)
    ts = _feed(co, 2, 2, y, 0.0, noise=0.05)
    gids = {e["track_id"]: e["global_id"] for e in co.step(ts)}
    co.on_track_lost(1, 1, ts)
    co.on_track_lost(2, 2, ts)
    blend = l2n(0.5 * x + 0.55 * y)
    ts2 = _feed(co, 2, 9, blend, ts + 20, noise=0.02)
    return co, gids, co.step(ts2)[0]


def test_expected_arrival_is_matched_first():
    co, gids, ev = _handoff_scene(bonus=0.1)
    assert ev["global_id"] == gids[1] and ev["reason"] == "handoff"
    assert co.stats["handoffs"] == 1
    assert not any(e["global_id"] == gids[1] for e in co.expected_arrivals())  # consumed


def test_without_handoff_priority_the_closer_identity_wins():
    _, gids, ev = _handoff_scene(bonus=0.0)
    assert ev["global_id"] == gids[2] and ev["reason"] == "match"


def test_handoff_priority_never_lowers_t_match():
    co = TrackCoordinator(_cfg(t_match=0.9, handoff_bonus=0.5))
    co.set_topology(parse_links([{"from": 1, "to": 2, "min_s": 5, "max_s": 120}]))
    x = person(22)
    ts = _feed(co, 1, 1, x, 0.0, noise=0.05)
    gid = co.step(ts)[0]["global_id"]
    co.on_track_lost(1, 1, ts)
    ts2 = _feed(co, 2, 3, person(23), ts + 20, noise=0.05)  # a stranger inside the handoff window
    assert co.step(ts2)[0]["global_id"] != gid


def test_below_t_match_gets_new_id_and_update_needs_t_update():
    co = TrackCoordinator(_cfg(t_match=0.9, t_update=0.95))
    a = person(6)
    ts = _feed(co, 1, 1, a, 0.0, noise=0.05)
    gid = co.step(ts)[0]["global_id"]
    co.on_track_lost(1, 1, ts)
    ts2 = _feed(co, 1, 2, person(60), ts + 2)
    assert co.step(ts2)[0]["global_id"] != gid
    assert co.stats["created"] == 2


def test_tracklet_waits_for_min_crops_then_max_wait():
    co = TrackCoordinator(_cfg(min_crops=2, max_wait_s=3.0))
    a = person(8)
    co.observe(1, 0.0, [(1, [0, 0, 50, 150], 0.9, (0, 0))], {1: (0.9, a)})
    assert co.step(0.5) == []  # 1 crop
    co.observe(1, 0.5, [(1, [0, 0, 50, 150], 0.9, (0, 0))], {1: (0.9, a)})
    assert co.step(1.0) == []  # 2 crops but max_wait not reached
    assert len(co.step(3.5)) == 1


def test_lost_before_ready_is_forced_and_vectors_queued():
    co = TrackCoordinator(_cfg())
    a = person(9)
    co.observe(1, 0.0, [(1, [0, 0, 50, 150], 0.9, (0, 0))], {1: (0.9, a)})
    co.observe(1, 0.2, [(1, [0, 0, 50, 150], 0.9, (0, 0))], {1: (0.9, noisy(a))})
    ev = co.on_track_lost(1, 1, 0.5)
    assert [e["type"] for e in ev] == ["reid_assigned", "reid_track_ended"]
    vecs = co.drain_vectors()
    assert len(vecs) >= 1 and len(vecs[0]["embedding"]) == EMBEDDING_DIM
    assert co.drain_vectors() == []
    assert co.snapshot()["tracklets"] == 0


def test_warm_start_restores_ids_and_counter():
    co = TrackCoordinator(_cfg())
    rows = [
        {"global_id": "PERSON-00007", "vector": person(1).tolist(), "timestamp": 100.0, "camera_id": 1},
        {"global_id": "PERSON-00007", "vector": noisy(person(1)).tolist(), "timestamp": 110.0, "camera_id": 2},
        {"global_id": "PERSON-00003", "vector": person(2).tolist(), "timestamp": 90.0, "camera_id": 1},
        {"global_id": "PERSON-00009", "vector": [0.0] * 3, "timestamp": 90.0, "camera_id": 1},  # bad dim: skipped
    ]
    out = co.warm(rows)
    assert out == {"rows": 3, "identities": 2, "counter": 7}
    ident = co.gallery.get("PERSON-00007")
    assert ident.restored and ident.last_camera_id == 2 and len(ident.views) == 2
    ts = _feed(co, 1, 1, person(1), 150.0)
    assert co.step(ts)[0]["global_id"] == "PERSON-00007"


def test_fusion_on_calibrated_overlapping_cameras():
    co = TrackCoordinator(_cfg(fusion_min_sim=0.3))
    co.set_topology(parse_links([{"from": 1, "to": 2, "max_s": 60, "overlapping": True, "bidirectional": True}]))
    H = np.eye(3).tolist()
    co.set_calibration(1, H)
    co.set_calibration(2, H)
    a = person(11)
    ts = _feed(co, 1, 1, a, 0.0)
    gid = co.step(ts)[0]["global_id"]
    for k in range(8):  # both cameras see the person at the same floor point for 1.6 s
        t = ts + 0.2 * (k + 1)
        co.observe(1, t, [(1, [0, 0, 50, 150], 0.9, (1.0, 1.0))], {})
        co.observe(2, t, [(4, [0, 0, 50, 150], 0.9, (1.2, 1.0))], {4: (0.9, noisy(a, 1.0))})
    ev = co.step(ts + 1.6)
    assert ev[0]["global_id"] == gid and ev[0]["reason"] == "fusion"
    with pytest.raises(ValueError):
        co.set_calibration(3, [[0, 0, 0], [0, 0, 0], [0, 0, 0]])


# ── floor calibration from clicked points ─────────────────────────────────────

def _pts(pairs):
    from app.routers.reid import CalibrationPoint
    return [CalibrationPoint(image=list(i), floor=list(f)) for i, f in pairs]


def test_floor_fit_recovers_known_plane_and_reports_error():
    from app.routers.reid import fit_floor_homography
    from app.services.track_coordinator import floor_point

    w, h = 640, 480
    H_true = np.array([[0.01, 0.002, -1.0], [0.0005, 0.02, -2.0], [0.0, 0.0008, 1.0]])
    img = [(0.1, 0.6), (0.9, 0.62), (0.85, 0.95), (0.15, 0.97), (0.5, 0.8)]
    pairs = [(p, floor_point(H_true, (p[0] * w, p[1] * h))) for p in img]
    H, rmse = fit_floor_homography(_pts(pairs), [w, h])
    assert rmse is not None and rmse < 1e-6
    for p, f in pairs:
        got = floor_point(np.asarray(H), (p[0] * w, p[1] * h))
        assert np.allclose(got, f, atol=1e-6)
    # Exactly four points: an exact fit, so no error is claimed.
    assert fit_floor_homography(_pts(pairs[:4]), [w, h])[1] is None


@pytest.mark.parametrize("pairs, msg", [
    ([((0.1, 0.5), (0, 0)), ((0.3, 0.5), (1, 0)), ((0.6, 0.5), (2, 0)), ((0.9, 0.5), (3, 0))], "line"),
    ([((0.1, 0.5), (0, 0)), ((0.1, 0.5), (0, 0.1)), ((0.6, 0.9), (2, 2)), ((0.9, 0.6), (3, 0))], "same spot"),
])
def test_floor_fit_rejects_degenerate_points(pairs, msg):
    from app.routers.reid import fit_floor_homography
    with pytest.raises(ValueError, match=msg):
        fit_floor_homography(_pts(pairs), [640, 480])
