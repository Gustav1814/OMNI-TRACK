from typing import Any, cast

from pydantic import BaseModel, Field

from services.model.cfgs.ibase_stage import BaseStage


class ModelStage(BaseModel):
    model_id: str
    stage: int
    stage_class: type[BaseStage] = Field(exclude=True)

    def create_instance(
        self,
        model_path: str | None,
        tag: str | list[str],
        device: str,
        is_global_reid: bool = False,
        **kwargs: Any,
    ) -> BaseStage:
        import inspect
        from collections.abc import Callable

        sig = inspect.signature(self.stage_class.__init__)
        params: dict[str, Any] = {
            "model_id": self.model_id,
            "model_path": model_path,
            "tag": tag,
            "device": device,
        }

        if "is_global_reid" in sig.parameters:
            params["is_global_reid"] = is_global_reid

        track_val = kwargs.get("track_name", "bytetrack")
        if "tracker_name" in sig.parameters:
            params["tracker_name"] = track_val
        elif "track_name" in sig.parameters:
            params["track_name"] = track_val

        if "job_id" in sig.parameters:
            params["job_id"] = kwargs.get("job_id")

        ctor = cast(Callable[..., BaseStage], self.stage_class)
        return ctor(**params)


class KPIModelMapping(BaseModel):
    kpi_name: str
    model_stages: list[ModelStage]


class KPIRegionMapping(BaseModel):
    kpi_name: str
    allowed_region_types: list[str] = Field(default_factory=list)


class ModelRegistryConfig(BaseModel):
    kpi_model_mappings: list[KPIModelMapping] = Field(default_factory=list)
    kpi_region_mappings: list[KPIRegionMapping] = Field(default_factory=list)
