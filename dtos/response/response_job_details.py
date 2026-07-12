# common/dtos/response/response_job_details.py

from pydantic import BaseModel


class DetectionInfo(BaseModel):
    class_name: str
    confidence: float
    bbox: list[int]
    in_region: bool
    region_name: str


class ResponseJobDetails(BaseModel):
    job_id: str
    timestamp: str
    frame_number: int
    kpi_value: int
    detections: list[DetectionInfo]
    frame_base64: str | None = None
