"""
OmniTrack AI — Model Management Router.

Lists the weight files in ``model_weight/`` with the role each one plays, and the
models the pipeline has loaded. Nothing here loads a model: weights are loaded
once by the pipeline (one person detector + specialist streams) and class names
are read from those loaded instances.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.models.user import User
from app.security.dependencies import get_current_user
from app.services import model_selection

router = APIRouter(prefix="/api/models", tags=["Models"])


class ModelInfo(BaseModel):
    filename: str
    path: str
    size_bytes: int
    classes: List[Dict[str, Any]]
    num_classes: int
    model_type: str = "yolo"
    role: Optional[str] = None
    loaded: bool = False


class ModelListResponse(BaseModel):
    models: List[ModelInfo]
    default_model: str
    total_models: int


def _roles() -> Dict[str, str]:
    """weight file name -> role it is configured for."""
    roles = {
        Path(settings.PERSON_MODEL_CPU).name: "person (cpu)",
        Path(settings.PERSON_MODEL_GPU).name: "person (gpu)",
        Path(settings.FIRE_MODEL_PATH).name: "fire/smoke",
        Path(settings.FACE_MODEL_PATH).name: "face",
        Path(settings.EMOTION_MODEL_PATH).name: "emotion",
        Path(settings.REID_WEIGHTS_PATH).name: "re-id",
    }
    if settings.PERSON_MODEL:
        roles[Path(settings.PERSON_MODEL).name] = "person"
    if settings.PRODUCT_MODEL_PATH:
        roles[Path(settings.PRODUCT_MODEL_PATH).name] = "product"
    return roles


def _loaded(pipeline) -> Dict[str, Dict[str, Any]]:
    """file name -> {role, class_names, info} for models the pipeline holds."""
    out: Dict[str, Dict[str, Any]] = {}
    if pipeline is None:
        return out
    det = getattr(pipeline, "detector", None)
    if det is not None and det.is_loaded:
        out[det.name] = {"role": "person", "class_names": det.get_class_names(), "info": det.info()}
    for attr, role in (("fire_model", "fire/smoke"), ("product_model", "product")):
        m = getattr(pipeline, attr, None)
        if m is not None:
            out[m.name] = {"role": role, "class_names": dict(m.names), "info": {"conf": m.conf, "imgsz": m.imgsz}}
    return out


def _classes(names: Dict[int, str]) -> List[Dict[str, Any]]:
    return [{"id": int(k), "name": v} for k, v in sorted(names.items())]


@router.get("/", response_model=ModelListResponse)
async def list_models(
    request: Request,
    include_classes: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Weight files in ``model_weight/``, their configured role and whether they are loaded."""
    weights_dir = Path(settings.MODEL_WEIGHTS_DIR)
    roles = _roles()
    loaded = _loaded(getattr(request.app.state, "pipeline", None))
    models: List[ModelInfo] = []
    for name in model_selection.list_all_weight_names():
        f = weights_dir / name
        cls = _classes(loaded[name]["class_names"]) if include_classes and name in loaded else []
        models.append(ModelInfo(
            filename=name,
            path=str(f.resolve()),
            size_bytes=f.stat().st_size,
            classes=cls,
            num_classes=len(cls),
            role=roles.get(name),
            loaded=name in loaded,
        ))
    return ModelListResponse(
        models=models, default_model=model_selection.person_model_name(), total_models=len(models)
    )


@router.get("/current")
async def get_current_model(current_user: User = Depends(get_current_user)):
    """The person detector the pipeline uses on this device (chosen from evaluation)."""
    from app.ai.detector import resolve_device

    device = resolve_device(settings.DEVICE)
    _path, imgsz = settings.person_model_for(device)
    return {
        "default_model": model_selection.person_model_name(device),
        "device": device,
        "imgsz": imgsz,
        "conf": settings.PERSON_CONF,
        "iou": settings.PERSON_IOU,
        "export": settings.DETECTOR_EXPORT,
        "model_weights_dir": settings.MODEL_WEIGHTS_DIR,
    }


@router.get("/loaded")
async def get_loaded_models(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Models the pipeline holds in memory (loaded once at start, never unloaded)."""
    pipeline = getattr(request.app.state, "pipeline", None)
    loaded = _loaded(pipeline)
    cameras = sorted(getattr(pipeline, "_cams", {}) or {}) if pipeline is not None else []
    items = [
        {
            "model_path": name,
            "role": v["role"],
            "class_names": v["class_names"],
            "num_classes": len(v["class_names"]),
            "info": v["info"],
            "active": bool(cameras),
            "assigned_cameras": cameras,
        }
        for name, v in loaded.items()
    ]
    person = loaded.get(getattr(getattr(pipeline, "detector", None), "name", ""), None)
    return {
        "status": "ok" if items else "no_data",
        "loaded_models": items,
        "active_loaded_models": [i for i in items if i["active"]],
        "cached_inactive_models": [i for i in items if not i["active"]],
        "total_loaded": len(items),
        "total_active_loaded": sum(1 for i in items if i["active"]),
        "camera_assignments": {
            cam: {"paths": [pipeline.detector.name] if person else [], "mode": "single"} for cam in cameras
        },
    }


@router.get("/{model_filename}/classes")
async def get_model_classes(
    model_filename: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Class names of a loaded model (models are not loaded on demand)."""
    name = Path(model_filename).name
    if name not in model_selection.list_all_weight_names():
        raise HTTPException(status_code=404, detail=f"Model {name} not found")
    loaded = _loaded(getattr(request.app.state, "pipeline", None))
    if name not in loaded:
        raise HTTPException(status_code=409, detail=f"Model {name} is not loaded by the pipeline")
    return _classes(loaded[name]["class_names"])
