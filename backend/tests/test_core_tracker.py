"""Person tracker lifecycle: tentative -> confirmed -> occluded -> lost, IDs via ByteTrack idx."""

import numpy as np
import pytest

from app.ai.tracker import CONFIRMED, OCCLUDED, PersonTracker, bytetrack_args


def _box(x, y=50.0, w=40.0, h=120.0):
    return [x, y, x + w, y + h]


def test_bytetrack_args_buffer_scales_with_fps():
    assert bytetrack_args(10, buffer_s=3).track_buffer == 30
    assert bytetrack_args(6, buffer_s=3).track_buffer == 18
    assert bytetrack_args(0.1, buffer_s=3).track_buffer >= 1


def test_confirm_after_min_hits_and_det_index_mapping():
    trk = PersonTracker(fps=10, min_hits=3)
    events = []
    for i in range(5):
        xyxy = np.array([_box(100 + 2 * i), _box(300 - 2 * i)], np.float32)
        out = trk.update(xyxy, np.array([0.9, 0.85], np.float32), ts=i * 0.1)
        events += out.events
        # every detection maps to a track and the reported box is the detector's box
        for t in out.tracks:
            assert t.det_index >= 0
            np.testing.assert_allclose(t.xyxy, xyxy[t.det_index], atol=1e-4)
            assert out.det_to_track[t.det_index] == t.track_id
        if i < 2:
            assert out.confirmed == []
    confirmed = [e for e in events if e.type == "track_confirmed"]
    assert len(confirmed) == 2
    assert len(trk.confirmed_tracks()) == 2
    assert all(e.type in {"track_confirmed", "track_updated"} for e in events)


def test_low_conf_box_does_not_start_a_track_but_keeps_one_alive():
    trk = PersonTracker(fps=10, min_hits=1)
    out = trk.update(np.array([_box(100)], np.float32), np.array([0.3], np.float32), ts=0.0)
    assert out.tracks == []  # below new_track_thresh
    for i in range(3):
        out = trk.update(np.array([_box(100 + i)], np.float32), np.array([0.9], np.float32), ts=0.1 * (i + 1))
    tid = out.tracks[0].track_id
    # second-stage association: a 0.3 box continues the existing track
    out = trk.update(np.array([_box(104)], np.float32), np.array([0.3], np.float32), ts=0.5)
    assert [t.track_id for t in out.tracks] == [tid]


def test_occluded_then_lost_after_buffer_and_dicts_pruned():
    fps = 10
    trk = PersonTracker(fps=fps, min_hits=3, buffer_s=1.0)
    for i in range(4):
        out = trk.update(np.array([_box(100 + i)], np.float32), np.array([0.9], np.float32), ts=i / fps)
    tid = out.confirmed[0].track_id
    lost_events = []
    for k in range(1, 30):
        out = trk.update(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), ts=(4 + k) / fps)
        if k == 1:
            assert trk.get_track(tid).state == OCCLUDED
            assert out.confirmed == []
        lost_events += [e for e in out.events if e.type == "track_lost"]
        if lost_events:
            break
    assert [e.track_id for e in lost_events] == [tid]
    assert k <= trk.track_buffer_frames + 2
    assert len(trk) == 0  # per-track state dropped


def test_reappearing_inside_buffer_keeps_id_without_new_confirm_event():
    trk = PersonTracker(fps=10, min_hits=3, buffer_s=3.0)
    for i in range(4):
        out = trk.update(np.array([_box(100 + i)], np.float32), np.array([0.9], np.float32), ts=i * 0.1)
    tid = out.confirmed[0].track_id
    for k in range(5):
        trk.update(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), ts=0.4 + k * 0.1)
    out = trk.update(np.array([_box(105)], np.float32), np.array([0.9], np.float32), ts=1.0)
    assert out.confirmed and out.confirmed[0].track_id == tid
    assert out.confirmed[0].state == CONFIRMED
    assert not [e for e in out.events if e.type == "track_confirmed"]


def test_reset_emits_lost_for_confirmed_only():
    trk = PersonTracker(fps=10, min_hits=3)
    for i in range(4):
        trk.update(np.array([_box(100 + i)], np.float32), np.array([0.9], np.float32), ts=i * 0.1)
    trk.update(np.array([_box(100 + 4), _box(400)], np.float32), np.array([0.9, 0.9], np.float32), ts=0.4)
    ev = trk.reset()
    assert len(ev) == 1 and ev[0].type == "track_lost"
    assert len(trk) == 0


def test_set_fps_rescales_buffer():
    trk = PersonTracker(fps=10, buffer_s=3.0)
    assert trk.track_buffer_frames == 30
    trk.set_fps(4)
    assert trk.track_buffer_frames == 12


@pytest.mark.parametrize("n", [0, 1, 25])
def test_handles_any_detection_count(n):
    trk = PersonTracker(fps=10)
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 600, n)
    xyxy = np.stack([x, x * 0 + 10, x + 30, x * 0 + 120], 1).astype(np.float32) if n else np.zeros((0, 4), np.float32)
    out = trk.update(xyxy, np.full(n, 0.9, np.float32), ts=0.0)
    assert out.det_to_track.shape == (n,)
