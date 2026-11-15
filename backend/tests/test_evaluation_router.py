"""The evaluation API merges criteria with result files and never invents numbers."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import evaluation
from app.security.dependencies import get_current_user


@pytest.fixture()
def client(tmp_path, monkeypatch):
    (tmp_path / "results").mkdir()
    (tmp_path / "criteria.json").write_text(json.dumps({"criteria": [
        {"id": "detection", "title": "Person detection", "metric": "mAP@0.5", "target": 0.85, "op": ">",
         "unit": "ratio", "script": "eval/detect.py", "dataset": "CAVIAR"},
        {"id": "emotion", "title": "Facial emotion", "metric": "Accuracy", "target": 0.70, "op": ">",
         "unit": "ratio", "script": "eval/emotion.py", "dataset": "FER-2013"},
    ]}))
    monkeypatch.setenv("OMNITRACK_EVAL_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(evaluation.router)
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app), tmp_path


def test_no_results_is_no_data(client):
    c, _ = client
    body = c.get("/api/evaluation/summary").json()
    assert body["status"] == "no_data"
    assert body["counts"] == {"total": 2, "measured": 0, "passed": 0, "failed": 0}
    for item in body["criteria"]:
        assert item["status"] == "no_data"
        assert item["value"] is None and item["passed"] is None
    assert c.get("/api/evaluation/results/emotion").json() == {"id": "emotion", "status": "no_data"}


def test_measured_result_is_passed_through(client):
    c, root = client
    (root / "results" / "emotion.json").write_text(json.dumps({
        "id": "emotion", "status": "measured", "passed": False,
        "primary": {"metric": "Accuracy", "value": 0.5599, "target": 0.7, "op": ">"},
        "dataset": {"name": "FER-2013", "n_items": 7178}, "hardware": {"device": "cpu"},
        "created_at": "2026-11-15T00:00:00+00:00",
    }))
    (root / "results" / "detection.json").write_text("{not json")  # corrupt file -> treated as missing
    body = c.get("/api/evaluation/summary").json()
    items = {i["id"]: i for i in body["criteria"]}
    assert items["emotion"]["value"] == 0.5599 and items["emotion"]["passed"] is False
    assert items["emotion"]["dataset"]["n_items"] == 7178
    assert items["detection"]["status"] == "no_data"
    assert body["counts"] == {"total": 2, "measured": 1, "passed": 0, "failed": 1}
    assert c.get("/api/evaluation/results/nope").status_code == 404


def test_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNITRACK_EVAL_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(evaluation.router)
    assert TestClient(app).get("/api/evaluation/summary").status_code in (401, 403)
