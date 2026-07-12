from pydantic import BaseModel

from dtos.response.response_model_info import ModelInfoResponse


class RegionDescription(BaseModel):
    """Description for a region type."""

    type: str
    description: str


class KPIInfoResponse(BaseModel):

    """Response model for KPI information."""

    kpi_name: str
    models: list[ModelInfoResponse] = []
    allowed_region_types: list[str] = []
    region_descriptions: dict[str, str] = {}

    class Config:
        json_schema_extra = {
            "example": {
                "kpi_name": "item_count",
                "model_id": "general_object_detection",
                "model_path": "services/inferenced_weights/best.pt",
                "allowed_region_types": ["Line"],
                "region_descriptions": {"Line": "Used for counting items crossing a line"},
            }
        }
