"""Finite sources (stop at EOF vs opt-in loop with a new epoch) and the evidence frame ring's bounds."""

import time

import cv2
import numpy as np
import pytest

from app.services.stream_manager import CameraStream, FrameRing, StreamConfig, StreamType


@pytest.fixture
def tiny_video(tmp_path):
    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 50.0, (64, 48))
    assert writer.isOpened()
    for i in range(10):
        writer.write(np.full((48, 64, 3), i * 20, np.uint8))
    writer.release()
    return path


def _wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _stream(path, loop):
    return CameraStream(StreamConfig(camera_id=7, source=str(path), stream_type=StreamType.FILE,
                                     fps_target=100, loop=loop))


def test_file_stops_at_eof_when_loop_off(tiny_video):
    s = _stream(tiny_video, loop=False)
    s.start()
    try:
        assert _wait(lambda: s.finished)
        st = s.stats
        assert st.finished and st.finished_at is not None
        assert not st.is_connected and st.error_message is None
        assert st.loops_completed == 0
        assert st.frames_read == 10
        assert s.epoch == 1  # a later restart is a new timeline
        s._thread.join(timeout=2)
        assert not s._thread.is_alive()  # capture thread ended on its own
        # restart replays from the beginning and clears the finished flag
        s.start()
        assert not s.finished
        assert _wait(lambda: s.finished)
        assert s.epoch == 2
    finally:
        s.stop()


def test_file_loops_with_new_epoch_when_loop_on(tiny_video):
    s = _stream(tiny_video, loop=True)
    s.start()
    try:
        epochs = set()

        def seen_two_loops():
            pkt = s.get_latest(0)
            if pkt is not None:
                epochs.add(pkt.epoch)
            return s.stats.loops_completed >= 2

        assert _wait(seen_two_loops)
        assert not s.finished
        assert s.epoch >= 2
        assert len(epochs) >= 2  # frames from different passes carry different epochs
        assert s.stats.frames_read >= 20
    finally:
        s.stop()


def _frame(w=1920, h=1080, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)  # noise: worst case for JPEG size


def test_ring_downscales_and_bounds_seconds():
    ring = FrameRing(seconds=2.0, max_bytes=10**9, fps=10, max_width=960)
    for i in range(50):
        ring.push(_frame(seed=i % 3), ts=100.0 + i * 0.1)
    st = ring.stats()
    assert st["seconds"] <= 2.0 + 1e-6
    assert st["frames"] <= 21
    out = ring.frames(0, 1e12)
    assert out and all(f.shape[1] == 960 and f.shape[0] == 540 for _t, f in out)
    assert [t for t, _ in out] == sorted(t for t, _ in out)


def test_ring_bounds_bytes_and_provider_window():
    ring = FrameRing(seconds=1000.0, max_bytes=1_500_000, fps=10, max_width=640)
    for i in range(40):
        ring.push(_frame(seed=i % 3), ts=float(i))
    st = ring.stats()
    assert 0 < st["bytes"] <= 1_500_000
    assert st["frames"] < 40  # the byte cap evicted the oldest frames
    window = ring.frames(35.0, 37.0)
    assert [t for t, _ in window] == [35.0, 36.0, 37.0]
    ring.set_max_bytes(1)
    assert ring.stats()["frames"] == 0


def test_ring_rate_limit_and_timeline_restart():
    ring = FrameRing(seconds=60.0, max_bytes=10**9, fps=4, max_width=320)
    assert ring.due(10.0)
    ring.push(_frame(320, 240), 10.0)
    assert not ring.due(10.1)  # 4 fps -> next frame due 0.25 s later
    assert ring.due(10.3)
    ring.busy = True
    assert not ring.due(20.0)  # an encode is already queued for this camera
    ring.push(_frame(320, 240), 20.0)
    assert not ring.busy
    ring.push(_frame(320, 240), 1.0)  # file looped: older timestamps start a new timeline
    assert [t for t, _ in ring.frames(0, 1e9)] == [1.0]
