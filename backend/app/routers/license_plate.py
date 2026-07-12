"""
OmniTrack AI — License Plate Recognition API

This router exposes a lightweight endpoint for image-based
license plate detection and OCR without requiring Streamlit.
"""

import asyncio
import os
import tempfile
import urllib.request
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from app.models.user import User
from app.security.dependencies import get_current_user
from app.ai.license_plate import (
    DETECTOR_MODELS,
    OCR_MODELS,
    recognize_license_plate_image,
    recognize_license_plate_video,
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
    media_type: str = Form("image"),
    source_url: str = Form(None),
    detection_threshold: float = Form(0.4),
    ocr_threshold: float = Form(0.0),
    file: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
):
    media_type = media_type.lower()
    if media_type not in ("image", "video"):
        raise HTTPException(400, "media_type must be either 'image' or 'video'.")

    try:
        if media_type == "image":
            if file is None and not source_url:
                raise HTTPException(400, "Please upload an image file or provide an image URL.")
            if source_url and file is None:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                    urllib.request.urlretrieve(source_url, temp_file.name)
                    temp_path = temp_file.name
                with open(temp_path, "rb") as f:
                    file_bytes = f.read()
                os.unlink(temp_path)
            else:
                file_bytes = await file.read()
            payload = await asyncio.to_thread(
                recognize_license_plate_image,
                file_bytes,
                detector_model,
                ocr_model,
                detection_threshold,
                ocr_threshold,
            )
        else:
            source = source_url
            if file is not None:
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                try:
                    temp_file.write(await file.read())
                    temp_file.close()
                    source = temp_file.name
                finally:
                    temp_file.close()
            if not source:
                raise HTTPException(400, "Provide a video file or a video URL.")
            payload = await asyncio.to_thread(
                recognize_license_plate_video,
                source,
                detector_model,
                ocr_model,
                detection_threshold,
                ocr_threshold,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"License plate recognition failed: {exc}")
    finally:
        if media_type == "video" and file is not None:
            try:
                os.unlink(temp_file.name)
            except Exception:
                pass

    return payload
