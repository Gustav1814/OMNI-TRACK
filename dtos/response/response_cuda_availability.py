from pydantic import BaseModel


class CudaAvailabilityResponse(BaseModel):
    """Response model for CUDA availability check."""

    cuda_available: bool
    device_count: int
    device_name: str | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "cuda_available": True,
                "device_count": 1,
                "device_name": "NVIDIA GeForce RTX 3090",
            }
        }
