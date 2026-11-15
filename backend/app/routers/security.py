"""
OmniTrack AI — security endpoints.

/api/security/robustness       GET  : last adversarial-robustness report (ART FGSM/PGD
                                      against the real YOLO detector) — read from disk.
/api/security/robustness/run   POST : admin; runs ``python -m app.security.adversarial_eval``
                                      in a separate process (memory isolation) and
                                      writes the JSON report.
/api/audit/logs                GET  : admin; audit trail with decrypted metadata.
/api/audit/verify              GET  : admin; full HMAC-chain + anchor verification.
/api/audit/status              GET  : any user; result of the last scheduled check.

``audit_verify_loop`` is started from the app lifespan: it verifies the chain at
startup and every AUDIT_VERIFY_INTERVAL_HOURS, logs an error and broadcasts an
``audit_chain_broken`` alert on failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BACKEND_ROOT, settings
from app.database import AsyncSessionLocal, get_db
from app.models.user import User, UserRole
from app.security import adversarial_eval
from app.security.client_ip import client_ip
from app.security.dependencies import get_current_user, require_role
from app.security.encryption import try_decrypt_data
from app.services.crud import AuditService

security_router = APIRouter(prefix="/api/security", tags=["Security"])
audit_router = APIRouter(prefix="/api/audit", tags=["Security Audit"])
admin_only = require_role(UserRole.ADMIN)

_ROBUSTNESS_JOB: Dict[str, Any] = {"status": "idle"}
_LAST_AUDIT_CHECK: Dict[str, Any] = {"status": "pending"}


# ─────────────────────────── posture (live config, no secrets) ───────────────────────────

@security_router.get("/posture")
async def security_posture(_: User = Depends(get_current_user)):
    """The auth and encryption settings this process actually runs with (values only, never keys)."""
    return {
        "access_token_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "refresh_token_days": settings.REFRESH_TOKEN_EXPIRE_DAYS,
        "jwt_algorithm": settings.JWT_ALGORITHM,
        "login_max_failures": settings.LOGIN_MAX_FAILURES,
        "login_lockout_minutes": settings.LOGIN_LOCKOUT_MINUTES,
        "public_registration": settings.ALLOW_PUBLIC_REGISTRATION,
        "encryption_at_rest": "AES-256-GCM" if settings.AES_SECRET_KEY else None,
        "audit_chain": "HMAC-SHA256" if settings.AUDIT_HMAC_KEY else None,
        "audit_verify_interval_hours": settings.AUDIT_VERIFY_INTERVAL_HOURS,
    }


# ─────────────────────────── adversarial robustness ───────────────────────────

@security_router.get("/robustness")
async def robustness_status(_: User = Depends(get_current_user)):
    status = adversarial_eval.get_robustness_status()
    status["job"] = dict(_ROBUSTNESS_JOB)
    return status


async def _run_robustness(images: int, eps: float, pgd_steps: int) -> None:
    cmd = [
        sys.executable, "-m", "app.security.adversarial_eval",
        "--images", str(images), "--eps", str(eps), "--pgd-steps", str(pgd_steps),
        "--out", str(adversarial_eval.REPORT_PATH),
    ]
    _ROBUSTNESS_JOB.update(status="running", started_at=time.time(), error=None)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(BACKEND_ROOT),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=3600)
        if proc.returncode != 0:
            tail = (err or b"").decode(errors="replace").strip().splitlines()[-3:]
            _ROBUSTNESS_JOB.update(status="failed", error=" | ".join(tail)[-400:])
        else:
            _ROBUSTNESS_JOB.update(status="completed")
    except asyncio.TimeoutError:
        proc.kill()
        _ROBUSTNESS_JOB.update(status="failed", error="timed out after 1h")
    except Exception as e:
        _ROBUSTNESS_JOB.update(status="failed", error=str(e)[:300])
    finally:
        _ROBUSTNESS_JOB["finished_at"] = time.time()


_TASKS: set = set()


@security_router.post("/robustness/run", status_code=202)
async def robustness_run(
    request: Request,
    images: int = Query(10, ge=2, le=200),
    eps: float = Query(8 / 255, gt=0, le=0.1),
    pgd_steps: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_only),
):
    """Start an ART FGSM/PGD evaluation of the real detector (COCO person subset)."""
    if not adversarial_eval.art_available():
        raise HTTPException(503, "adversarial-robustness-toolbox is not installed in this environment")
    if _ROBUSTNESS_JOB.get("status") == "running":
        raise HTTPException(409, "A robustness evaluation is already running")
    await AuditService.log_event(
        db, "ROBUSTNESS_RUN", admin.id, "Adversarial robustness evaluation started",
        {"images": images, "eps": eps, "pgd_steps": pgd_steps}, ip_address=client_ip(request),
    )
    _ROBUSTNESS_JOB.clear()
    _ROBUSTNESS_JOB.update(status="queued", images=images, eps=eps, pgd_steps=pgd_steps,
                           requested_by=admin.username)
    task = asyncio.get_running_loop().create_task(_run_robustness(images, eps, pgd_steps))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return dict(_ROBUSTNESS_JOB)


# ─────────────────────────── audit trail ───────────────────────────

@audit_router.get("/logs")
async def audit_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, max_length=50),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_only),
):
    rows = await AuditService.get_logs(db, limit=limit, event_type=event_type, offset=offset)
    return [
        {
            "id": r.id, "event_type": r.event_type, "user_id": r.user_id,
            "description": r.description, "metadata": try_decrypt_data(r.encrypted_metadata),
            "metadata_encrypted": bool(r.encrypted_metadata),
            "ip_address": r.ip_address, "timestamp": r.timestamp,
            "hash_alg": r.hash_alg, "current_hash": r.current_hash, "previous_hash": r.previous_hash,
        }
        for r in rows
    ]


@audit_router.get("/verify")
async def audit_verify(db: AsyncSession = Depends(get_db), _: User = Depends(admin_only)):
    result = await AuditService.verify_integrity(db)
    _record_check(result, trigger="manual")
    return result


@audit_router.get("/status")
async def audit_status(_: User = Depends(get_current_user)):
    """Last scheduled/manual verification (cheap; does not re-verify)."""
    return dict(_LAST_AUDIT_CHECK)


def _record_check(result: Dict[str, Any], trigger: str) -> None:
    _LAST_AUDIT_CHECK.clear()
    _LAST_AUDIT_CHECK.update({
        "status": "ok" if result.get("valid") else "broken",
        "trigger": trigger,
        "checked_at": result.get("checked_at") or datetime.now(timezone.utc).isoformat(),
        "valid": result.get("valid"),
        "broken_at": result.get("broken_at"),
        "reason": result.get("reason"),
        "total": result.get("total"),
        "message": result.get("message"),
        "next_check_hours": settings.AUDIT_VERIFY_INTERVAL_HOURS,
    })


async def run_scheduled_audit_check(trigger: str = "scheduled") -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await AuditService.verify_integrity(db)
    _record_check(result, trigger)
    if not result.get("valid"):
        logger.error(f"AUDIT CHAIN VERIFICATION FAILED: {result.get('message')}")
        try:
            from app.main import broadcast  # app-wide instance (lazy: avoids import cycle)
            await broadcast.broadcast("alerts", "audit_chain_broken", {
                "severity": "critical", "message": result.get("message"),
                "broken_at": result.get("broken_at"), "reason": result.get("reason"),
                "checked_at": result.get("checked_at"),
            })
        except Exception as e:
            logger.error(f"Could not broadcast audit alert: {e}")
    else:
        logger.info(f"Audit chain verified: {result.get('message')}")
    return result


async def audit_verify_loop() -> None:
    """Verify at startup, then every AUDIT_VERIFY_INTERVAL_HOURS (default nightly)."""
    interval = max(0.01, float(settings.AUDIT_VERIFY_INTERVAL_HOURS)) * 3600
    await asyncio.sleep(5)
    trigger = "startup"
    while True:
        try:
            await run_scheduled_audit_check(trigger)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Scheduled audit verification errored: {e}")
            _LAST_AUDIT_CHECK.update(status="error", message=str(e)[:200],
                                     checked_at=datetime.now(timezone.utc).isoformat())
        trigger = "scheduled"
        await asyncio.sleep(interval)


def last_audit_check() -> Dict[str, Any]:
    return json.loads(json.dumps(_LAST_AUDIT_CHECK, default=str))
