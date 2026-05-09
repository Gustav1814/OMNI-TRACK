"""
OmniTrack AI — Model Management Router
Lists available YOLO models from model_weight folder and provides class names.
"""

import os
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.user import User
from app.security.dependencies import get_current_user
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
        models.append(ModelInfo(
            filename=file.name,
            path=str(file.resolve()),
            size_bytes=file.stat().st_size,
            classes=classes,
            num_classes=len(classes),
            model_type="yolo"
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