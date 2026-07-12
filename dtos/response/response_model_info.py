from pydantic import BaseModel


class ModelInfoResponse(BaseModel):
    """Response model for model information."""

    model_id: str
    model_name: str
    device: str

    class Config:
        json_schema_extra = {
            "example": {
                "model_id": "general_object_detection",
                "model_name": "services/inferenced_weights/best.pt",
                "device": "cuda",
            }
        }


class KPIInfo(BaseModel):
    name: str


class ResponseAllKPIsInfo(BaseModel):
    """Response model for model information."""

    kpis_list: list[KPIInfo] = []
