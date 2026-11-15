"""Alerts centre, alert evidence and data export (agent5 port of origin/multimodel).

Pure tests use synthetic frames and fake users. ``db`` tests write their own camera /
alert rows into the dev database and delete them afterwards; no users are created
and audit writes are stubbed (the audit chain is append-only).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analytics.engine import ZoneAnalyticsEngine
from app.config import settings
from app.database import get_db
from app.models.user import UserRole
from app.routers import alerts as alerts_router
from app.routers import evidence as evidence_router
from app.routers import export as export_router
from app.security.dependencies import get_current_user
from app.security.encryption import BLOB_MAGIC
from app.security.route_policy import required_role_for
from app.services import evidence

ADMIN = SimpleNamespace(id=None, role=UserRole.ADMIN, username="pytest-admin", is_active=True)
VIEWER = SimpleNamespace(id=None, role=UserRole.VIEWER, username="pytest-viewer", is_active=True)


# ─────────────────────────── helpers ───────────────────────────


def _frames(n=24, w=320, h=240, t0=100.0, fps=8.0):
    out = []
    for i in range(n):
        f = np.zeros((h, w, 3), np.uint8)
        f[:, :, 1] = 40
        x = 10 + i * 8
        f[100:140, x:x + 40] = (0, 0, 255)
        out.append((t0 + i / fps, f))
    return out


def _fire_event(ts=101.5, key=None):
    return {
        "type": "fire", "camera_id": "7", "ts": ts, "value": 0.81,
        "payload": {"action": "start", "incident_key": key or f"7:{ts:.3f}", "bbox_xywh": [40, 80, 60, 50],
                    "frame_wh": [320, 240], "class_name": "fire", "peak_conf": 0.81},
    }


def _app(*routers, user=ADMIN, session_maker=None) -> TestClient:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    app.dependency_overrides[get_current_user] = lambda: user
    if session_maker is not None:
        async def _db():
            async with session_maker() as s:
                yield s
        app.dependency_overrides[get_db] = _db
    else:
        async def _no_db():
            yield None
        app.dependency_overrides[get_db] = _no_db
    return TestClient(app)


@pytest.fixture()
def evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(settings, "EVIDENCE_PRE_S", 1.0)
    monkeypatch.setattr(settings, "EVIDENCE_POST_S", 1.5)
    return tmp_path / "evidence"


# ─────────────────────────── access policy ───────────────────────────


def test_ack_is_admin_only_and_reads_are_authenticated():
    assert required_role_for("POST", alerts_router.acknowledge_alert) == "admin"
    for fn in (alerts_router.list_alerts, alerts_router.alert_facets, evidence_router.list_evidence,
               evidence_router.evidence_file, evidence_router.evidence_thumb, export_router.export_dataset,
               export_router.list_datasets):
        assert required_role_for("GET", fn) == "authenticated"


def test_non_admin_ack_gets_403():
    c = _app(alerts_router.router, user=VIEWER)
    assert c.post("/api/alerts/zone_event/1/ack", json={}).status_code == 403
    assert c.post("/api/alerts/bogus/1/ack", json={}).status_code in (403, 422)


def test_non_admin_cannot_export_audit_or_full():
    c = _app(export_router.router, user=VIEWER)
    assert c.get("/api/export/audit").status_code == 403
    assert c.get("/api/export/full").status_code == 403
    assert c.get("/api/export/not_a_dataset").status_code == 422


# ─────────────────────────── evidence ───────────────────────────


def test_schedule_capture_only_takes_alert_opens_and_fire_starts():
    prov = lambda a, b: []  # noqa: E731
    assert evidence._build_job({"type": "alert", "ts": 1.0, "payload": {"action": "close"}, "event_uid": "u"}, "1", prov) is None
    assert evidence._build_job({"type": "alert", "ts": 1.0, "payload": {"action": "open"}}, "1", prov) is None  # no uid
    assert evidence._build_job({"type": "zone_visit", "ts": 1.0, "event_uid": "u"}, "1", prov) is None
    assert evidence._build_job(dict(_fire_event(), payload={"action": "clear", "incident_key": "k"}), "7", prov) is None
    job = evidence._build_job(_fire_event(), "7", prov)
    assert job.source == "fire_incident" and job.key == "7:101.500" and job.box == (40.0, 80.0, 100.0, 130.0)
    job = evidence._build_job({"type": "alert", "ts": 5.0, "event_uid": "abc", "track_id": 3,
                               "payload": {"action": "open", "alert_type": "dwell_overrun"}}, "2", prov)
    assert (job.source, job.key, job.camera_id, job.track_id) == ("zone_event", "abc", 2, 3)


def test_full_queue_drops_without_blocking(monkeypatch):
    rec = evidence.EvidenceRecorder(maxsize=1)
    monkeypatch.setattr(rec, "_ensure_thread", lambda: None)  # no worker: the queue stays full
    prov = lambda a, b: []  # noqa: E731
    t = time.monotonic()
    assert rec.submit(evidence._build_job(_fire_event(key="a"), "7", prov)) is True
    assert rec.submit(evidence._build_job(_fire_event(key="b"), "7", prov)) is False
    assert rec.submit(evidence._build_job(_fire_event(key="a"), "7", prov)) is False  # duplicate
    assert time.monotonic() - t < 0.5
    assert rec.stats["dropped"] == 1 and rec.pending() == 1


def test_capture_encrypts_snapshot_and_playable_clip(evidence_dir, monkeypatch):
    import cv2

    rec = evidence.EvidenceRecorder(maxsize=4)
    recorded = {}
    monkeypatch.setattr(rec._db, "run", lambda fn, job, files: recorded.setdefault("files", files) and [1, 2])
    frames = _frames()
    seen = {}

    def provider(a, b):
        seen["window"] = (a, b)
        return frames

    rec.capture(evidence._build_job(_fire_event(), "7", provider))
    assert seen["window"] == pytest.approx((100.5, 103.0))
    files = {kind: rel for kind, rel, _n in recorded["files"]}
    assert set(files) == {"snapshot", "clip"}

    for rel in [*files.values(), evidence.thumb_rel_path(files["snapshot"])]:
        raw = (evidence_dir / rel).read_bytes()
        assert raw.startswith(BLOB_MAGIC), "evidence must be encrypted at rest"
        assert b"JFIF" not in raw[:64] and b"ftyp" not in raw[:64]

    snap = cv2.imdecode(np.frombuffer(evidence.read_item_bytes(files["snapshot"]), np.uint8), cv2.IMREAD_COLOR)
    assert snap.shape == (240, 320, 3)
    assert evidence.media_type_for(files["snapshot"]) == "image/jpeg"

    clip = evidence.read_item_bytes(files["clip"])
    ext = Path(files["clip"][:-4]).suffix
    assert evidence.media_type_for(files["clip"]) in ("video/mp4", "video/webm")
    out = evidence_dir / f"check{ext}"
    out.write_bytes(clip)
    cap = cv2.VideoCapture(str(out))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    assert n >= 10, f"clip decoded only {n} frames"


def test_capture_without_frames_writes_nothing(evidence_dir, monkeypatch):
    rec = evidence.EvidenceRecorder(maxsize=1)
    monkeypatch.setattr(rec._db, "run", lambda *a: pytest.fail("no rows without frames"))
    assert rec.capture(evidence._build_job(_fire_event(), "7", lambda a, b: [])) == []
    assert not any(evidence_dir.rglob("*.enc")) if evidence_dir.exists() else True


def test_evidence_path_cannot_escape_root(evidence_dir):
    with pytest.raises(ValueError):
        evidence.resolve_path("../../etc/passwd")


# ─────────────────────────── engine: dwell overrun ───────────────────────────


def test_dwell_overrun_alert_has_event_uid_and_closes():
    from tests.test_analytics_engine import CAM, _drive, _of

    zone = {"id": 9, "name": "Aisle", "zone_type": "shelf", "config": {"dwell_alert_seconds": 10},
            "points": [[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]]}
    eng = ZoneAnalyticsEngine()
    eng.set_zones(CAM, [zone])
    ev, _ = _drive(eng, [(15.0, {1: (0.1, 0.1), 2: None}), (2.0, {1: (0.8, 0.8)}), (8.0, {1: (0.8, 0.8)})])
    alerts = [e for e in _of(ev, "alert") if e["payload"]["alert_type"] == "dwell_overrun"]
    opens = [e for e in alerts if e["payload"]["action"] == "open"]
    closes = [e for e in alerts if e["payload"]["action"] == "close"]
    assert len(opens) == 1 and opens[0]["track_id"] == 1 and opens[0]["value"] >= 10
    assert len(closes) == 1 and closes[0]["payload"]["open_event_uid"] == opens[0]["event_uid"]
    assert all(uuid.UUID(e["event_uid"]) for e in alerts)


def test_csv_cells_cannot_inject_formulas():
    assert export_router._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert export_router._cell(-1.5) == -1.5
    assert export_router._cell({"a": 1}) == '{"a":1}'


def test_migration_005_chain(backend_root):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(backend_root / "alembic.ini")))
    rev = script.get_revision("005_alerts_evidence")
    assert rev.down_revision == "004_reid"
    heads = script.get_heads()
    assert len(heads) == 1, heads  # one linear chain
    assert "005_alerts_evidence" in {r.revision for r in script.walk_revisions()}
    assert script.get_revision("006_calibration_points").down_revision == "005_alerts_evidence"


# ─────────────────────────── database-backed ───────────────────────────


@pytest.fixture()
def nullpool_maker():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def seeded(nullpool_maker):
    """One inactive camera + zone + zone alert (open/close) + fire incident + evidence row."""
    import asyncio

    from sqlalchemy import delete, select

    from app.models.alerts import AlertAck, EvidenceItem
    from app.models.camera import Camera
    from app.models.zones import FireIncident, Zone, ZoneEvent

    uid = str(uuid.uuid4())
    t0 = datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    ids = {}

    async def setup():
        async with nullpool_maker() as s:
            cam = Camera(name="pytest-alerts-cam", stream_url="pytest://none", is_active=False)
            s.add(cam)
            await s.flush()
            zone = Zone(camera_id=cam.id, name="pytest zone", zone_type="crowd", capacity=2,
                        points=[[0, 0], [1, 0], [1, 1]], is_active=False)
            s.add(zone)
            await s.flush()
            ze = ZoneEvent(camera_id=cam.id, zone_id=zone.id, event_type="alert", ts=t0, value=1.5, event_uid=uid,
                           payload={"alert_type": "overcrowding", "action": "open", "count": 3, "capacity": 2})
            close = ZoneEvent(camera_id=cam.id, zone_id=zone.id, event_type="alert", ts=t0 + timedelta(seconds=40),
                              payload={"alert_type": "overcrowding", "action": "close", "open_event_uid": uid})
            fire = FireIncident(incident_key=f"pytest:{uid[:8]}", camera_id=cam.id, status="cleared",
                                started_at=t0 + timedelta(minutes=1), cleared_at=t0 + timedelta(minutes=2),
                                peak_conf=0.9, class_name="smoke", hits=3, retriggers=0)
            s.add_all([ze, close, fire])
            await s.flush()
            ev = EvidenceItem(source="zone_event", source_id=None, event_uid=uid, camera_id=cam.id, kind="snapshot",
                              path="nope/missing_snapshot.jpg.enc", encrypted=True, bytes=10)
            s.add(ev)
            await s.commit()
            ids.update(cam=cam.id, zone=zone.id, ze=ze.id, close=close.id, fire=fire.id, ev=ev.id)

    async def teardown():
        async with nullpool_maker() as s:
            await s.execute(delete(AlertAck).where(
                ((AlertAck.source == "zone_event") & AlertAck.source_id.in_([ids.get("ze", -1)]))
                | ((AlertAck.source == "fire_incident") & (AlertAck.source_id == ids.get("fire", -1)))))
            await s.execute(delete(EvidenceItem).where(EvidenceItem.camera_id == ids.get("cam", -1)))
            await s.execute(delete(ZoneEvent).where(ZoneEvent.camera_id == ids.get("cam", -1)))
            await s.execute(delete(FireIncident).where(FireIncident.camera_id == ids.get("cam", -1)))
            await s.execute(delete(Zone).where(Zone.camera_id == ids.get("cam", -1)))
            await s.execute(delete(Camera).where(Camera.id == ids.get("cam", -1)))
            await s.commit()
            left = (await s.execute(select(Camera.id).where(Camera.name == "pytest-alerts-cam"))).all()
            assert not left

    asyncio.run(setup())
    try:
        yield ids
    finally:
        asyncio.run(teardown())


@pytest.mark.db
def test_alert_list_filter_facets_and_ack(seeded, nullpool_maker, monkeypatch):
    async def _no_audit(*a, **k):
        return None

    monkeypatch.setattr(alerts_router.AuditService, "log_event", _no_audit)
    c = _app(alerts_router.router, session_maker=nullpool_maker)
    cam = seeded["cam"]

    body = c.get("/api/alerts", params={"camera_id": cam}).json()
    assert body["total"] == 2
    by_src = {i["source"]: i for i in body["items"]}
    z, f = by_src["zone_event"], by_src["fire_incident"]
    assert body["items"][0]["source"] == "fire_incident"  # newest first
    assert z["id"] == seeded["ze"] and z["kind"] == "overcrowding" and z["severity"] == "high"
    assert z["camera_name"] == "pytest-alerts-cam" and z["zone_name"] == "pytest zone"
    assert z["ended_at"].startswith("2001-02-03T04:05:46")
    assert z["evidence"] == [{"id": seeded["ev"], "kind": "snapshot"}]
    assert f["kind"] == "fire" and f["severity"] == "critical" and f["ended_at"] and f["acked"] is False

    assert c.get("/api/alerts", params={"camera_id": cam, "source": "fire_incident"}).json()["total"] == 1
    assert c.get("/api/alerts", params={"camera_id": cam, "kind": "overcrowding"}).json()["total"] == 1
    assert c.get("/api/alerts", params={"camera_id": cam, "until": "2001-01-01T00:00:00Z"}).json()["status"] == "no_data"

    facets = c.get("/api/alerts/facets", params={"camera_id": cam}).json()
    assert facets["sources"]["zone_event"] == 1 and facets["sources"]["fire_incident"] == 1
    assert facets["cameras"] == [{"id": cam, "name": "pytest-alerts-cam", "n": 2}] and facets["unacked"] == 2

    r = c.post(f"/api/alerts/zone_event/{seeded['ze']}/ack", json={"note": "seen"})
    assert r.status_code == 200 and r.json()["acked"] is True and r.json()["ack_note"] == "seen"
    assert c.post(f"/api/alerts/zone_event/{seeded['ze']}/ack", json={}).status_code == 200  # idempotent
    assert c.post(f"/api/alerts/zone_event/{seeded['close']}/ack", json={}).status_code == 404  # close events aren't alerts
    assert c.get("/api/alerts", params={"camera_id": cam, "acked": "true"}).json()["total"] == 1
    assert c.get("/api/alerts/facets", params={"camera_id": cam}).json()["unacked"] == 1

    c2 = _app(evidence_router.router, session_maker=nullpool_maker)
    lst = c2.get("/api/evidence", params={"camera_id": cam}).json()
    assert lst["total"] == 1 and lst["items"][0]["kind"] == "snapshot"
    assert c2.get(f"/api/evidence/{seeded['ev']}/file").status_code == 410  # row without a file


@pytest.mark.db
def test_export_csv_headers_and_legacy_shape(seeded, nullpool_maker, monkeypatch):
    async def _no_audit(*a, **k):
        return None

    monkeypatch.setattr(export_router, "_audit_export", _no_audit)
    monkeypatch.setattr(export_router, "AsyncSessionLocal", nullpool_maker)
    c = _app(export_router.router, session_maker=nullpool_maker)

    r = c.get("/api/export/zone_events", params={"camera_id": seeded["cam"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment;" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == ",".join(export_router.DATASETS["zone_events"][0])
    assert len(lines) == 3

    for name in ("alerts", "fire_incidents", "zone_samples", "track_visits", "emotion", "detections", "traffic"):
        r = c.get(f"/api/export/{name}", params={"since": "2100-01-01T00:00:00Z", "download": "true"})
        assert r.status_code == 200, name
        assert r.text.strip() == ",".join(export_router.DATASETS[name][0]), name

    j = c.get("/api/export/fire_incidents", params={"camera_id": seeded["cam"], "format": "json"}).json()
    assert len(j) == 1 and j[0]["class_name"] == "smoke"

    legacy = c.get("/api/export/detections", params={"since": "2100-01-01T00:00:00Z"}).json()
    assert set(legacy) >= {"filename", "content", "content_type", "row_count"} and legacy["row_count"] == 0

    lst = c.get("/api/export").json()["datasets"]
    names = {d["dataset"]: d for d in lst}
    assert names["audit"]["admin_only"] and names["audit"]["allowed"] and names["zone_events"]["rows"] >= 2
