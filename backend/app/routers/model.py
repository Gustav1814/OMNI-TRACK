"""
OmniTrack AI — Model Management Router
Lists available YOLO models from model_weight folder and provides class names.
"""

import hashlib
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from app.models.user import User
from app.models.user import UserRole
from app.security.dependencies import get_current_user, require_role
from app.config import settings

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

router = APIRouter(prefix="/api/models", tags=["Models"])

MODEL_WEIGHTS_DIR = Path(settings.MODEL_WEIGHTS_DIR)


class ModelInfo(BaseModel):
    filename: str
    path: str
    size_bytes: int
    classes: List[Dict[str, Any]]
    num_classes: int
    model_type: str = "yolo"
    task: str = "detect"
    family: str = "custom"
    size_mb: float = 0.0
    recommended_profile: str = "laptop"


class ModelListResponse(BaseModel):
    models: List[ModelInfo]
    default_model: str
    total_models: int


def _get_model_classes(model_path: str) -> List[Dict[str, Any]]:
    """Extract class names from a YOLO model."""
    if not ULTRALYTICS_AVAILABLE:
        return []
    try:
        model = YOLO(model_path)
        names = model.names
        return [{"id": class_id, "name": class_name} for class_id, class_name in names.items()]
    except Exception:
        return []


def _guess_family(filename: str) -> str:
    name = filename.lower()
    if "yolo26" in name:
        return "yolo26"
    if "yolo11" in name:
        return "yolo11"
    if "yolov8" in name:
        return "yolov8"
    if "rtdetr" in name:
        return "rtdetr"
    return "custom"


def _recommended_profile(filename: str, size_bytes: int) -> str:
    name = filename.lower()
    if any(t in name for t in ("26x", "11x", "26l", "11l")) or size_bytes > 100 * 1024 * 1024:
        return "workstation"
    if any(t in name for t in ("26m", "11m")) or size_bytes > 40 * 1024 * 1024:
        return "workstation"
    return "laptop"


def _infer_task(model_path: str) -> str:
    if not ULTRALYTICS_AVAILABLE:
        return "detect"
    try:
        model = YOLO(model_path)
        return str(getattr(model, "task", "detect"))
    except Exception:
        return "detect"


def _scan_models() -> List[ModelInfo]:
    """Scan model_weight folder for available YOLO models."""
    models = []
    if not MODEL_WEIGHTS_DIR.exists():
        MODEL_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        return models
    for file in MODEL_WEIGHTS_DIR.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() not in {".pt", ".onnx", ".engine", ".tflite"}:
            continue
        classes = _get_model_classes(str(file))
        task = _infer_task(str(file))
        size_bytes = file.stat().st_size
        models.append(ModelInfo(
            filename=file.name,
            path=str(file.resolve()),
            size_bytes=size_bytes,
            classes=classes,
            num_classes=len(classes),
            model_type="yolo",
            task=task,
            family=_guess_family(file.name),
            size_mb=round(size_bytes / (1024 * 1024), 2),
            recommended_profile=_recommended_profile(file.name, size_bytes),
        ))
    models.sort(key=lambda x: x.filename)
    return models


@router.get("/", response_model=ModelListResponse)
async def list_models(current_user: User = Depends(get_current_user)):
    """List all available YOLO models from the model_weight folder."""
    models = _scan_models()
    return ModelListResponse(
        models=models,
        default_model=settings.DEFAULT_YOLO_MODEL,
        total_models=len(models)
    )


@router.get("/{model_filename}/classes")
async def get_model_classes(model_filename: str, current_user: User = Depends(get_current_user)):
    """Get all class names that a specific model can detect."""
    model_path = MODEL_WEIGHTS_DIR / os.path.basename(model_filename)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail=f"Model {model_filename} not found")
    classes = _get_model_classes(str(model_path))
    if not classes:
        raise HTTPException(status_code=500, detail="Could not load model classes")
    return classes


@router.get("/current")
async def get_current_model(current_user: User = Depends(get_current_user)):
    """Get the currently active default model."""
    return {
        "default_model": settings.DEFAULT_YOLO_MODEL,
        "model_weights_dir": settings.MODEL_WEIGHTS_DIR
    }


@router.post("/upload")
async def upload_model(
    model_file: UploadFile = File(...),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    """
    Upload a model file directly into model_weight/.
    """
    MODEL_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = os.path.basename(model_file.filename or "")
    if not filename:
        raise HTTPException(status_code=400, detail="Missing file name")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pt", ".onnx", ".engine", ".tflite", ".pth"}:
        raise HTTPException(status_code=400, detail="Unsupported model format")
    target = MODEL_WEIGHTS_DIR / filename
    tmp = MODEL_WEIGHTS_DIR / f".{filename}.uploading"
    content = await model_file.read()
    tmp.write_bytes(content)
    tmp.replace(target)
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"ok": True, "filename": filename, "size_bytes": target.stat().st_size, "sha256": sha}


class FetchModelRequest(BaseModel):
    source: str
    filename: Optional[str] = None


@router.post("/fetch")
async def fetch_model(
    payload: FetchModelRequest,
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    """
    Fetch a model by URL or known Ultralytics asset name (e.g. yolo26l.pt).
    """
    MODEL_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    src = (payload.source or "").strip()
    if not src:
        raise HTTPException(status_code=400, detail="source is required")
    out_name = payload.filename or os.path.basename(src)
    if not out_name:
        out_name = src
    target = MODEL_WEIGHTS_DIR / os.path.basename(out_name)
    if src.startswith(("http://", "https://")):
        import urllib.request

        urllib.request.urlretrieve(src, str(target))
    else:
        if not ULTRALYTICS_AVAILABLE:
            raise HTTPException(status_code=500, detail="Ultralytics unavailable for asset fetch")
        from ultralytics.utils.downloads import attempt_download_asset

        cwd = os.getcwd()
        try:
            os.chdir(str(MODEL_WEIGHTS_DIR))
            attempt_download_asset(src)
        finally:
            os.chdir(cwd)
        maybe = MODEL_WEIGHTS_DIR / os.path.basename(src)
        if maybe.exists():
            target = maybe
    if not target.exists():
        raise HTTPException(status_code=404, detail="Failed to fetch model")
    return {"ok": True, "filename": target.name, "size_bytes": target.stat().st_size}


@router.delete("/{model_filename}")
async def delete_model(
    model_filename: str,
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    target = MODEL_WEIGHTS_DIR / os.path.basename(model_filename)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Model not found")
    target.unlink()
    return {"ok": True, "deleted": model_filename}


@router.get("/loaded")
async def get_loaded_models(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Get all currently loaded models in the pipeline with their class names.
    Shows which models are actively being used for inference.
    """
    pipeline = request.app.state.pipeline
    loaded = []
    for model_path, detector in pipeline._detectors.items():
        if detector.is_loaded:
            loaded.append({
                "model_path": model_path,
                "class_names": detector.get_class_names(),
                "num_classes": len(detector.get_class_names()),
            })
    return {
        "loaded_models": loaded,
        "total_loaded": len(loaded),
        "camera_assignments": {
            cam_id: model for cam_id, model in pipeline._camera_models.items()
        }
    }