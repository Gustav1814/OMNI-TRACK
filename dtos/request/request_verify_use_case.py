# common/dtos/request/request_verify_use_case.py

from pydantic import BaseModel

from dtos.common.enums.url_type import URLType

from .request_register_use_case import Region


class RequestVerifyUseCase(BaseModel):
    camera_id: str
    location_id: str
    kpi_name: str
    url_type: URLType
    url: str
    tag: str = "person"  # Comma-separated tags like "person,car,chair"
    regions: list[Region] | None = None
    snapshots_at_tag: str = "wrong shelf item,wrongly placed item"
