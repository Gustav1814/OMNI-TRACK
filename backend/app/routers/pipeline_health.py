"""
OmniTrack AI — Pipeline health router.

Read-only operational status per camera (last frame time, processed FPS, errors in
the last minute, active tracks, stage latencies). Unauthenticated, like
/api/health, so probes and the ops dashboard can poll it; it exposes no source
URLs or frames.
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/pipeline", tags=["System"])


@router.get("/health")
async def pipeline_health(request: Request):
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialised")
    return pipeline.get_health()
