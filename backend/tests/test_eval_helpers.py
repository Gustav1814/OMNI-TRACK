"""Fast checks for the evaluation helpers in eval/ (no models, no datasets)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import common  # noqa: E402


def test_passes_ops_and_missing():
    assert common.passes(0.9, 0.85, ">") is True
    assert common.passes(0.85, 0.85, ">") is False
    assert common.passes(0.85, 0.85, ">=") is True
    assert common.passes(80, 100, "<") is True
    assert common.passes(None, 0.85, ">") is None
    assert common.passes(0.5, None, None) is None


def test_average_precision_perfect_and_half():
    assert common.average_precision([0.9, 0.8], [True, True], 2)["ap"] == pytest.approx(1.0)
    r = common.average_precision([0.9], [True], 2)
    assert r["max_recall"] == pytest.approx(0.5)
    assert r["ap"] == pytest.approx(51 / 101)
    # a false positive ranked first lowers precision
    assert common.average_precision([0.9, 0.8], [False, True], 1)["ap"] == pytest.approx(0.5)
    assert np.isnan(common.average_precision([], [], 0)["ap"])


def test_box_iou():
    iou = common.box_iou([[0, 0, 10, 10]], [[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]])
    assert iou.shape == (1, 3)
    assert iou[0].tolist() == pytest.approx([1.0, 1 / 3, 0.0])


def test_manifest_freeze_is_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "MANIFESTS_DIR", tmp_path)
    calls = []

    def builder():
        calls.append(1)
        return {"dataset": "t", "items": [{"file": "a.jpg"}, {"file": "b.jpg"}]}

    m1 = common.load_or_freeze_manifest("t", builder, refreeze=False)
    m2 = common.load_or_freeze_manifest("t", builder, refreeze=False)
    assert len(calls) == 1, "a frozen manifest must be reused, not rebuilt"
    assert m1 == m2
    assert m1["n_items"] == 2 and m1["items_sha256"] == common.sha256_json(m1["items"])
    # a hand-edited manifest is rejected instead of silently changing the split
    data = json.loads((tmp_path / "t.json").read_text())
    data["items"].pop()
    (tmp_path / "t.json").write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        common.load_or_freeze_manifest("t", builder, refreeze=False)


def test_write_result_refuses_nan_and_records_target(tmp_path):
    with pytest.raises(SystemExit):
        common.write_result("emotion", value=float("nan"), metrics={}, dataset={}, model=None,
                            device="cpu", started=0.0, out_dir=tmp_path)
    out = common.write_result("emotion", value=0.56, metrics={"accuracy": 0.56}, dataset={"name": "x"},
                              model=None, device="cpu", started=0.0, out_dir=tmp_path)
    r = json.loads(out.read_text())
    assert r["primary"]["target"] == 0.70 and r["passed"] is False
    assert r["hardware"]["device"] == "cpu"


def test_every_criterion_has_a_script():
    crit = json.loads((EVAL_DIR / "criteria.json").read_text())["criteria"]
    ids = [c["id"] for c in crit]
    assert len(ids) == len(set(ids))
    for c in crit:
        assert (EVAL_DIR.parent / c["script"]).is_file(), c["script"]


def test_k_of_n_alarm_rule():
    import importlib.util

    spec = importlib.util.spec_from_file_location("eval_fire", EVAL_DIR / "fire.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.k_of_n_events([True] * 2 + [False] * 10, k=3, n=5, cooldown=0) == 0
    assert mod.k_of_n_events([True] * 3, k=3, n=5, cooldown=0) == 1
    # cooldown suppresses re-alarming on a sustained positive run
    assert mod.k_of_n_events([True] * 20, k=3, n=5, cooldown=100) == 1
    # isolated single-scan blips never alarm
    assert mod.k_of_n_events([True, False, False, False] * 10, k=3, n=5, cooldown=0) == 0


def test_reid_cmc_map_protocol():
    import importlib.util

    spec = importlib.util.spec_from_file_location("eval_reid", EVAL_DIR / "reid.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    q = np.eye(2, 4, dtype=np.float32)
    # gallery: same-cam copy of query 0 (must be ignored), correct match for 0 in other cam,
    # and for query 1 a distractor ranked above the true match.
    g = np.array([[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 0.9, 0.1, 0], [0, 0.5, 0.5, 0]], dtype=np.float32)
    g /= np.linalg.norm(g, axis=1, keepdims=True)
    r = mod.evaluate(q, np.array([0, 1]), np.array([1, 1]), g, np.array([0, 0, 2, 1]), np.array([1, 2, 2, 2]))
    assert r["rank1"] == pytest.approx(0.5)
    assert r["mAP"] == pytest.approx(0.75)
