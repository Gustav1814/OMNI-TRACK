from pydantic import BaseModel


class ModelObjectNamesResponse(BaseModel):
    model_id: str
    object_names: dict[int, str] = {}
