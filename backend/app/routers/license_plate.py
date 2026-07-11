"""
OmniTrack AI — License Plate Recognition API

This router exposes a lightweight endpoint for image-based
license plate detection and OCR without requiring Streamlit.
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.models.user import User
from app.security.dependencies import get_current_user
from app.ai.license_plate import (
    DETECTOR_MODELS,
    OCR_MODELS,
    recognize_license_plate_image,
)
from app.schemas.schemas import (
    LicensePlateModelsResponse,
    LicensePlatePrediction,
    LicensePlateResponse,
)

license_plate_router = APIRouter(prefix="/api/license-plate", tags=["License Plate"])


@license_plate_router.get("/health", include_in_schema=True, summary="License plate API health check")
async def license_plate_health():
    return {"status": "ok", "endpoint": "/api/license-plate/recognize"}


@license_plate_router.get("/models", response_model=LicensePlateModelsResponse)
async def list_license_plate_models(
    current_user: User = Depends(get_current_user),
):
    return {
        "detector_models": DETECTOR_MODELS,
        "ocr_models": OCR_MODELS,
    }


@license_plate_router.post("/recognize", response_model=LicensePlateResponse)
async def recognize_license_plate(
    detector_model: str = DETECTOR_MODELS[0],
    ocr_model: str = OCR_MODELS[0],
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Unable to read uploaded file: {exc}")

    try:
        payload = await asyncio.to_thread(
            recognize_license_plate_image,
            file_bytes,
            detector_model,
            ocr_model,
        )
        return payload
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"License plate recognition failed: {exc}")
