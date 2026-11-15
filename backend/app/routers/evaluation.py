"""
OmniTrack AI — Evaluation Router (read-only)

Serves the proposal success criteria (eval/criteria.json) merged with the
measured results written by the eval/ scripts (eval/results/<id>.json).
Nothing is computed here: a criterion without a result file is returned with
status "no_data" and null numbers, never a placeholder value.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.models.user import User
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/evaluation", tags=["Evaluation"])

_DEFAULT_EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


def eval_dir() -> Path:
    return Path(os.environ.get("OMNITRACK_EVAL_DIR") or _DEFAULT_EVAL_DIR)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.error(f"Evaluation: unreadable {path.name}: {exc}")
        return None


def _summary(crit: Dict[str, Any], result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = {
        "id": crit["id"],
        "title": crit.get("title"),
        "criterion": crit.get("criterion"),
        "metric": crit.get("metric"),
        "target": crit.get("target"),
        "op": crit.get("op"),
        "unit": crit.get("unit"),
        "script": crit.get("script"),
        "planned_dataset": crit.get("dataset"),
    }
    if result is None:
        return {**base, "status": "no_data", "value": None, "passed": None, "secondary": None,
                "dataset": None, "hardware": None, "created_at": None}
    primary = result.get("primary") or {}
    return {
        **base,
        "status": result.get("status", "measured"),
        "value": primary.get("value"),
        "passed": result.get("passed"),
        "secondary": result.get("secondary"),
        "dataset": result.get("dataset"),
        "model": result.get("model"),
        "hardware": result.get("hardware"),
        "created_at": result.get("created_at"),
        "duration_s": result.get("duration_s"),
        "notes": result.get("notes") or [],
    }


def _load() -> Dict[str, Any]:
    root = eval_dir()
    criteria = _read_json(root / "criteria.json")
    if not criteria:
        return {"status": "no_data", "criteria": [], "results": {}}
    results = {}
    for crit in criteria.get("criteria", []):
        res = _read_json(root / "results" / f"{crit['id']}.json")
        if res is not None and res.get("id") == crit["id"]:
            results[crit["id"]] = res
    return {"status": "ok" if results else "no_data", "criteria": criteria["criteria"], "results": results}


@router.get("/summary")
async def evaluation_summary(current_user: User = Depends(get_current_user)):
    """Every criterion with its latest measured result (or status no_data)."""
    data = _load()
    items = [_summary(c, data["results"].get(c["id"])) for c in data["criteria"]]
    measured = [i for i in items if i["status"] != "no_data"]
    return {
        "status": data["status"],
        "criteria": items,
        "counts": {
            "total": len(items),
            "measured": len(measured),
            "passed": sum(1 for i in measured if i["passed"] is True),
            "failed": sum(1 for i in measured if i["passed"] is False),
        },
    }


@router.get("/results/{result_id}")
async def evaluation_result(result_id: str, current_user: User = Depends(get_current_user)):
    """Full result JSON (all metrics, model, git, hardware) for one criterion."""
    data = _load()
    known = {c["id"] for c in data["criteria"]}
    if result_id not in known:
        raise HTTPException(status_code=404, detail="Unknown evaluation id")
    res = data["results"].get(result_id)
    if res is None:
        return {"id": result_id, "status": "no_data"}
    return res
