"""Demographics: per-visit aggregation, face->track association and the min-sample API rule.
Classifier outputs are injected here (tests only); the product never fabricates them."""

import numpy as np
import pytest

from app.ai.demographics import (
    AGE_BANDS,
    DemographicsClassifier,
    VisitAggregator,
    match_faces_to_tracks,
    summarize,
)


def _age(band, p=0.8):
    a = np.full(len(AGE_BANDS), (1 - p) / (len(AGE_BANDS) - 1))
    a[AGE_BANDS.index(band)] = p
    return a


def test_visit_needs_min_samples():
    agg = VisitAggregator(min_samples=2, max_samples=5)
    agg.add("1", 3, np.array([0.9, 0.1]), _age("20-29"), ts=1.0)
    assert agg.finish("1", 3) is None  # one reading is not enough
    assert len(agg) == 0


def test_visit_averages_readings_and_caps_samples():
    agg = VisitAggregator(min_samples=2, max_samples=3)
    agg.add("1", 3, np.array([0.9, 0.1]), _age("20-29"), ts=1.0)
    agg.add("1", 3, np.array([0.7, 0.3]), _age("30-39", 0.5), ts=2.0)
    assert agg.wants("1", 3)
    agg.add("1", 3, np.array([0.8, 0.2]), _age("20-29"), ts=3.0)
    assert not agg.wants("1", 3)
    agg.add("1", 3, np.array([0.0, 1.0]), _age("70+", 1.0), ts=4.0)  # over the cap: ignored
    res = agg.finish("1", 3)
    assert res["sample_count"] == 3
    assert res["age_band"] == "20-29"
    assert res["gender"] == "male"
    assert res["first_ts"] == 1.0 and res["last_ts"] == 3.0


def test_unclear_gender_is_unknown():
    agg = VisitAggregator(min_samples=2, max_samples=5)
    agg.add("1", 1, np.array([0.6, 0.4]), _age("40-49"))
    agg.add("1", 1, np.array([0.45, 0.55]), _age("40-49"))
    assert agg.finish("1", 1)["gender"] == "unknown"


def test_prune_and_drop_camera():
    agg = VisitAggregator(min_samples=1, max_samples=5)
    agg.add("1", 1, np.array([1.0, 0.0]), _age("3-9"), ts=0.0)
    agg.add("2", 1, np.array([1.0, 0.0]), _age("3-9"), ts=100.0)
    assert agg.prune(50.0, now=120.0) == 1
    agg.drop_camera("2")
    assert len(agg) == 0


def test_faces_matched_only_when_unambiguous():
    tracks = {1: (0, 0, 100, 200), 2: (300, 0, 400, 200), 3: (320, 0, 420, 200)}
    faces = [
        (30, 10, 70, 50),    # head of track 1
        (40, 150, 60, 170),  # lower half of track 1 -> not a head
        (330, 10, 370, 50),  # inside tracks 2 and 3 -> ambiguous
        (600, 10, 640, 50),  # nobody
    ]
    assert match_faces_to_tracks(faces, tracks) == {0: 1}
    # two faces in one person's head area -> neither is used
    assert match_faces_to_tracks([(10, 10, 40, 40), (50, 10, 90, 40)], {1: (0, 0, 100, 200)}) == {}


def test_summary_statuses_and_min_sample():
    none = summarize([], total_visits=0, installed=False, min_sample=20)
    assert none["status"] == "model_not_installed" and none["age_bands"] == {} and none["gender"] == {}
    assert summarize([], total_visits=5, installed=True, min_sample=20)["status"] == "no_data"

    few = summarize([("20-29", "male")] * 5, total_visits=12, installed=True, min_sample=20)
    assert few["status"] == "insufficient_samples"
    assert few["sample_count"] == 5 and few["unattributed"] == 7
    assert few["age_bands"] == {} and few["gender"] == {}  # percentages hidden under the minimum
    assert few["bias_caveat"] and few["model"]["installed"] is True

    rows = [("20-29", "male")] * 10 + [("30-39", "female")] * 8 + [("30-39", "unknown")] * 2
    ok = summarize(rows, total_visits=30, installed=True, min_sample=20)
    assert ok["status"] == "ok" and ok["sample_count"] == 20 and ok["unattributed"] == 10
    assert ok["age_bands"]["20-29"] == 50.0 and ok["age_bands"]["30-39"] == 50.0
    assert ok["gender"] == {"male": 50.0, "female": 40.0, "unknown": 10.0}
    assert set(ok["age_bands"]) == set(AGE_BANDS)


def test_missing_model_disables_classifier(tmp_path):
    clf = DemographicsClassifier(str(tmp_path / "absent.onnx"))
    assert clf.available is False
    assert clf.classify([np.zeros((50, 50, 3), np.uint8)]) is None


@pytest.mark.parametrize("w,h", [(30, 40), (300, 200)])
def test_preprocess_shape(w, h):
    batch = DemographicsClassifier.preprocess([np.zeros((h, w, 3), np.uint8)])
    assert batch.shape == (1, 3, 224, 224) and batch.dtype == np.float32
