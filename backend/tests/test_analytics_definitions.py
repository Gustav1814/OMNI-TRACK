"""Pure unit tests for the Phase 3 metric definitions (no DB, no models)."""

from __future__ import annotations

import pytest

from app.analytics import definitions as d

SQUARE = [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]


# --------------------------------------------------------------- membership


def test_foot_point_is_bottom_centre():
    assert d.foot_point((100, 50, 200, 250)) == (150.0, 250.0)
    assert d.normalized_foot_point((100, 50, 200, 250), (400, 500)) == pytest.approx((0.375, 0.5))


@pytest.mark.parametrize(
    "pt,inside",
    [((0.4, 0.4), True), ((0.1, 0.4), False), ((0.7, 0.7), False), ((0.2, 0.4), True), ((0.6, 0.6), True)],
)
def test_point_in_polygon(pt, inside):
    assert d.point_in_polygon(pt, SQUARE) is inside


def test_membership_uses_feet_not_box_centre():
    # Box centre (0.4, 0.1) is outside the square, but the feet (0.4, 0.3) are inside.
    xyxy = (0.35, -0.1, 0.45, 0.3)
    assert d.point_in_polygon(d.normalized_foot_point(xyxy, (1, 1)), SQUARE)


def test_concave_polygon():
    # "L" shape: the notch at top-right is outside.
    poly = [[0, 0], [1, 0], [1, 0.5], [0.5, 0.5], [0.5, 1], [0, 1]]
    assert d.point_in_polygon((0.25, 0.75), poly)
    assert not d.point_in_polygon((0.75, 0.75), poly)


def test_validate_polygon():
    assert d.validate_polygon(SQUARE, "shelf") == SQUARE
    with pytest.raises(ValueError):
        d.validate_polygon([[0, 0], [1, 1]], "crowd")          # too few points
    with pytest.raises(ValueError):
        d.validate_polygon([[0, 0], [0.001, 0], [0, 0.001]], "crowd")  # degenerate
    with pytest.raises(ValueError):
        d.validate_polygon([[0, 0], [1.5, 0], [0, 1]], "crowd")  # out of range
    with pytest.raises(ValueError, match="cross"):
        d.validate_polygon([[0.1, 0.1], [0.6, 0.4], [0.6, 0.1], [0.1, 0.6]], "shelf")  # bow-tie
    concave = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.3], [0.9, 0.9], [0.1, 0.9]]
    assert d.validate_polygon(concave, "shelf") == concave
    assert len(d.validate_polygon([[0.1, 0.5], [0.9, 0.5]], "entry_line")) == 2
    with pytest.raises(ValueError):
        d.validate_polygon(SQUARE, "entry_line")


# ---------------------------------------------------------------- line cross


def test_line_crossing_counts_once_with_jitter():
    a, b = (0.0, 0.5), (1.0, 0.5)
    st = d.LineCrossingState()
    path = [0.40, 0.45, 0.499, 0.501, 0.499, 0.502, 0.56, 0.60]
    results = [st.update((0.5, y), a, b, in_sign=1) for y in path]
    assert sum(1 for r in results if r != 0) == 1
    # Back out again -> one outward crossing.
    out = [st.update((0.5, y), a, b, in_sign=1) for y in (0.505, 0.40)]
    assert sum(out) == -1 or out.count(-1) == 1


def test_line_crossing_outside_segment_ignored():
    a, b = (0.0, 0.5), (0.3, 0.5)
    st = d.LineCrossingState()
    assert [st.update((0.8, y), a, b) for y in (0.4, 0.6)] == [0, 0]


# --------------------------------------------------------------- dwell


def _run_presence(samples, **kw):
    st, events = None, []
    for ts, inside in samples:
        st, upd = d.presence_step(st, ts, inside, **kw)
        events.append(upd)
    return st, events


def test_dwell_start_needs_three_seconds():
    _, ev = _run_presence([(0, True), (1, True), (2.9, True)])
    assert ev[0].entered and not any(e.visit_started for e in ev)
    _, ev = _run_presence([(0, True), (1, True), (3.0, True)])
    assert ev[-1].visit_started


def test_dwell_ends_after_one_second_outside_and_excludes_outside_time():
    samples = [(t / 2, True) for t in range(0, 21)]          # inside 0..10 s
    samples += [(10.5, False), (10.9, False), (11.0, False)]  # out 1 s after last_in
    st, ev = _run_presence(samples)
    assert st is None
    ended = [e.ended for e in ev if e.ended]
    assert len(ended) == 1
    assert ended[0].visit
    assert d.dwell_seconds(ended[0]) == pytest.approx(10.0)


def test_short_blip_out_does_not_split_visit():
    samples = [(0, True), (2, True), (2.5, False), (2.8, True), (5, True), (7, False)]
    st, ev = _run_presence(samples)
    ended = [e.ended for e in ev if e.ended]
    assert len(ended) == 1 and d.dwell_seconds(ended[0]) == pytest.approx(5.0)


def test_pass_through_is_not_a_visit():
    st, ev = _run_presence([(0, True), (1, True), (2.5, False)])
    assert ev[-1].ended is not None and not ev[-1].ended.visit


# ------------------------------------------------------------ queue median


def test_rolling_median_queue_length():
    rm = d.RollingMedian(d.QUEUE_SMOOTHING_S)
    for ts, v in [(0, 3), (0.5, 3), (1.0, 9), (1.5, 3)]:   # one spurious spike
        rm.add(ts, v)
    assert rm.value() == 3
    rm.add(5.0, 1)                                         # old samples drop out
    assert len(rm) == 1 and rm.value() == 1


def test_median_and_percentile_helpers():
    assert d.median_or_none([]) is None
    assert d.median_or_none([4, 1, 9]) == 4
    assert d.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == pytest.approx(9.1)
    assert d.percentile([], 50) is None


# ------------------------------------------------------- crowd hysteresis


def test_crowd_levels_basic():
    assert d.crowd_level_index(0, 20) == 0
    assert d.crowd_level_name(d.crowd_level_index(2, 20)) == "low"       # 10 %
    assert d.crowd_level_name(d.crowd_level_index(6, 20)) == "moderate"  # 30 %
    assert d.crowd_level_name(d.crowd_level_index(12, 20)) == "high"     # 60 %
    assert d.crowd_level_name(d.crowd_level_index(18, 20)) == "critical"  # 90 %
    assert d.crowd_level_index(5, None) is None


def test_crowd_hysteresis_holds_level_on_wobble():
    cap = 20
    lvl = d.crowd_level_index(12, cap)               # high (60 %)
    assert d.CROWD_LEVELS[lvl] == "high"
    lvl = d.crowd_level_index(11, cap, lvl)           # 55 %: within band (5 %)
    assert d.CROWD_LEVELS[lvl] == "high"
    lvl = d.crowd_level_index(10, cap, lvl)           # 50 %: clearly below
    assert d.CROWD_LEVELS[lvl] == "moderate"
    lvl = d.crowd_level_index(12, cap, lvl)           # back up immediately
    assert d.CROWD_LEVELS[lvl] == "high"


def test_crowd_hysteresis_band_is_at_least_one_person():
    cap = 5                                            # one person = 20 %
    lvl = d.crowd_level_index(3, cap)                  # 60 % -> high
    assert d.CROWD_LEVELS[lvl] == "high"
    lvl = d.crowd_level_index(2, cap, lvl)             # 40 %: not < 60 % - 20 %
    assert d.CROWD_LEVELS[lvl] == "high"
    lvl = d.crowd_level_index(1, cap, lvl)             # 20 %: drop
    assert d.CROWD_LEVELS[lvl] in ("low", "moderate")


def test_crowd_level_small_capacity_returns_to_empty():
    cap = 4                                            # 1/cap (25 %) > lowest threshold (10 %)
    lvl = d.crowd_level_index(3, cap)                  # 75 % -> high
    for n in (2, 1, 0, 0):
        lvl = d.crowd_level_index(n, cap, lvl)
    assert d.CROWD_LEVELS[lvl] == "empty"


def test_rolling_median_reports_observed_whole_counts():
    rm = d.RollingMedian(3.0)
    for ts, v in [(0, 2), (0.5, 3)]:
        rm.add(ts, v)
    assert rm.value() == 3                             # never 2.5 people


# ------------------------------------------------------- fire hysteresis


def _hit(conf=0.8):
    return d.fire_hit([((0, 0, 100, 100), conf, "fire")], (640, 480))


def test_fire_hit_filters_conf_and_area():
    assert d.fire_hit([((0, 0, 100, 100), 0.4, "fire")], (640, 480)) is None
    assert d.fire_hit([((0, 0, 5, 5), 0.9, "fire")], (640, 480)) is None
    best = d.fire_hit([((0, 0, 100, 100), 0.6, "smoke"), ((0, 0, 80, 80), 0.9, "fire")], (640, 480))
    assert best[1] == 0.9 and best[2] == "fire"


def test_fire_needs_k_of_n():
    st = d.FireState()
    assert d.fire_step(st, 0, _hit()) is None
    assert d.fire_step(st, 1, None) is None
    assert d.fire_step(st, 2, _hit()) is None
    assert d.fire_step(st, 3, _hit()) == "start"          # 3 of the last 4
    assert st.active and st.incident_seq == 1


def test_fire_single_frames_never_alert():
    st = d.FireState()
    for i in range(20):
        assert d.fire_step(st, i, _hit() if i % 3 == 0 else None) is None


def test_fire_clears_after_30s_and_reopens_within_cooldown():
    st = d.FireState()
    for t in range(3):
        d.fire_step(st, t, _hit())
    assert st.active
    assert d.fire_step(st, 20, None) is None              # 18 s without a hit
    assert d.fire_step(st, 32, None) == "clear"
    assert not st.active
    for t in (100, 101):
        assert d.fire_step(st, t, _hit()) is None
    assert d.fire_step(st, 102, _hit()) == "reopen"       # within 5 min cooldown
    assert st.incident_seq == 1 and st.retriggers == 1
    d.fire_step(st, 140, None)                            # clear again
    for t in (600, 601):
        d.fire_step(st, t, _hit())
    assert d.fire_step(st, 602, _hit()) == "start"        # after cooldown -> new incident
    assert st.incident_seq == 2


# ------------------------------------------------------------- attention


def test_attention_scores_top_is_100():
    s = d.attention_scores({"a": (4, 20.0), "b": (2, 20.0), "c": (0, 50.0), "e": (3, None)})
    assert s == {"a": 100.0, "b": 50.0, "c": 0.0, "e": 0.0}
    assert d.attention_scores({"a": (0, 5.0)}) == {"a": 0.0}
    assert d.attention_scores({}) == {}


# ------------------------------------------------------------- emotion


def test_sentiment_and_dominant():
    assert d.sentiment_from_counts({}) is None
    assert d.sentiment_from_counts({"happy": 3, "sad": 1}) == 0.5
    assert d.sentiment_from_counts({"neutral": 4}) == 0.0
    assert d.sentiment_from_counts({"bogus": 5}) is None
    assert d.dominant_label({"happy": 2, "neutral": 5}) == "neutral"
    assert d.dominant_label({"happy": 0}) is None
    assert d.bucket_start(601) == 600


# ------------------------------------------------------------- vibe


def test_vibe_none_when_nothing_measured():
    assert d.store_vibe() is None


def test_vibe_all_components():
    v = d.store_vibe(occupancy_pct=0.5, arrivals_per_min=5, engaged_rate=0.4, sentiment=0.0)
    # 100 * (0.3*0.5 + 0.25*0.5 + 0.25*0.4 + 0.2*0.5) = 47.5
    assert v["score"] == pytest.approx(47.5)
    assert v["label"] == "Steady"
    assert sum(c["contribution"] for c in v["components"].values()) == pytest.approx(v["score"], abs=0.1)
    assert sum(v["weights_used"].values()) == pytest.approx(1.0)


def test_vibe_renormalises_missing_components_and_caps():
    v = d.store_vibe(occupancy_pct=2.0, arrivals_per_min=30)   # both capped at 1
    assert v["score"] == 100.0 and v["label"] == "Buzzing"
    assert set(v["components"]) == {"occupancy", "flow"}
    assert v["weights_used"]["occupancy"] == pytest.approx(0.30 / 0.55, abs=1e-4)
    v = d.store_vibe(engaged_rate=0.1)
    assert v["score"] == 10.0 and v["label"] == "Quiet"


def test_fire_qualifies_matches_fire_hit_gate():
    assert d.fire_qualifies((0, 0, 100, 100), d.FIRE_MIN_CONF, (640, 480))
    assert not d.fire_qualifies((0, 0, 100, 100), d.FIRE_MIN_CONF - 0.01, (640, 480))   # red signage at 0.44
    assert not d.fire_qualifies((0, 0, 5, 5), 0.95, (640, 480))                          # speck
