import re

from pydantic import BaseModel, Field, model_validator

from config.BaseConfig import config
from constants.camera_direction import DOWN_TO_UP, RIGHT_TO_LEFT, UP_TO_DOWN
from dtos.common.enums.region_type import RegionType
from dtos.common.enums.url_type import URLType
from services.common.models.model_kpi_region_mapping import KPIModelMapping, KPIRegionMapping


class Point(BaseModel):
    x: int
    y: int


class BoundingBoxCoordinates(BaseModel):
    x_min: int
    y_min: int
    x_max: int
    y_max: int


class Region(BaseModel):
    type: RegionType
    name: str
    points: list[Point] | None = None  # for polygon
    coordinates: BoundingBoxCoordinates | None = None  # for bounding box
    line_points: list[Point] | None = None  # for line (must be 2 points)
    object_moving_direction: str | None = None

    @model_validator(mode="after")
    def validate_region_exclusivity(self):
        """Ensure only the relevant field is set for each region type."""
        if self.type == RegionType.POLYGON:
            if not self.points:
                raise ValueError("Polygon region must have 'points'.")
        elif self.type == RegionType.BOUNDING_BOX:
            if not self.coordinates:
                raise ValueError("Bounding box region must have 'coordinates'.")
        elif self.type == RegionType.Line:
            if not self.line_points or len(self.line_points) != 2:
                raise ValueError("Line region must have exactly 2 points.")
            if self.object_moving_direction is None:
                raise ValueError(
                    f"object_moving_direction is null, when working with line tool it must have object_moving_direction object"
                    f"any one from the values given [{RIGHT_TO_LEFT},{RIGHT_TO_LEFT},{UP_TO_DOWN},{DOWN_TO_UP}]"
                )

        return self


class ModelInfo(BaseModel):
    model_id: str
    order: int
    lead_by: str | None = None


class RequestRegisterUseCase(BaseModel):
    job_id: str | None = Field(
        default=None,
        description=(
            "Optional unique job identifier. Must be unique across in-memory and Redis jobs. "
            "If null, a GUID is auto-generated when `Is_JOB_GUID_DEFAULT_ALLOWED=True`; "
            "otherwise the request is rejected with `job_id is required`."
        ),
        examples=[None],
    )
    camera_id: str
    location_id: str
    kpi_name: str
    url: str = ""
    url_type: URLType
    tag: str = ""
    regions: list[Region] | None = []
    snapshots_at_tag: str = "wrong shelf item,wrongly placed item"
    tag_at_reid: str | None = ""
    is_global_reid: bool = False
    num_of_frame_per_sec: int = Field(
        default=1,
        ge=1,
        description=(
            "Redis KPI persistence throttle (frames): after each successful inference, KPI logs "
            "are written when the running frame counter is divisible by this value. "
            "Default **1** writes on every processed frame; use larger values for fewer Redis writes."
        ),
        examples=[1],
    )
    redis_job_ttl_seconds: int = Field(
        default=86400,
        ge=1,
        description="TTL for the Redis job record key (seconds). Default **86400** (1 day).",
        examples=[86400],
    )
    redis_detection_log_ttl_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "TTL for Redis detection/KPI log list keys (seconds); expiry resets on each write to "
            "that key (last activity). Default **300** (5 minutes)."
        ),
        examples=[300],
    )
    track_name: str = Field(
        default_factory=lambda: config.FALLBACK_TRACKER,
        description="Tracker name (e.g. bytetrack, botsort, ocsort)",
    )
    config_file_url: str | None = Field(
        default=None,
        description="URL of the custom tracker configuration file (YAML format) to fetch dynamically.",
    )
    config_file_content: str | None = Field(
        default=None,
        description="Raw embedded YAML string of the custom tracker configuration.",
    )

    # model_id: str = None  #TODO: will be removed if using anywhere #rafy test end to end after removing from everywhere
    model_infos: list[ModelInfo]

    @staticmethod
    def _is_plaintext_url(url: str) -> bool:
        """Check if the url looks like a recognized plaintext URL or file path."""
        if not url:
            return False
        lower = url.strip().lower()
        # Known URL schemes
        if lower.startswith(("rtsp://", "http://", "https://")):
            return True
        # Local video file paths
        if re.search(r"\.(mp4|avi|mov|mkv|flv|wmv|webm)$", lower):
            return True
        return False

    @model_validator(mode="after")
    def validate_url_and_type(self):
        # If the url doesn't look like a plaintext URL, it is assumed to be
        # an AES-192 encrypted hash. Skip format validation here; the router
        # layer will decrypt it before forwarding to the service layer.
        if not self._is_plaintext_url(self.url):
            return self

        if self.url_type == URLType.RTSP.value:
            if not self.url.startswith("rtsp://"):
                raise ValueError("For url_type=rtsp, url must start with 'rtsp://'.")

        elif self.url_type == URLType.VIDEO.value:
            if not re.search(r"\.(mp4|avi|mov|mkv|flv|wmv|webm)$", self.url, re.IGNORECASE):
                raise ValueError(
                    "For url_type=video, url must be a valid video file "
                    "(.mp4, .avi, .mov, .mkv, .flv, .wmv)."
                )

        elif self.url_type == URLType.YOUTUBE.value:
            # Match any valid YouTube URL
            youtube_regex = re.compile(
                r"^(https?://)?(www\.)?"
                r"(youtube\.com/(watch\?v=|embed/|shorts/|playlist\?list=)|youtu\.be/)"
                r"[\w\-]+"
                r"(\S*)?$",
                re.IGNORECASE,
            )
            if not youtube_regex.match(self.url):
                raise ValueError("For url_type=youtube, url must be a valid YouTube link.")

        return self

    @model_validator(mode="after")
    def validate_model_id_and_kpi_mapping(self):
        """
        Validate that all model_ids inside model_infos match the allowed models
        for the given KPI.
        """
        if not self.model_infos or len(self.model_infos) == 0:
            raise ValueError("model_infos list cannot be empty")

        from services.model.model_registry import ModelRegistry

        # Get allowed models for this KPI
        allowed_models = ModelRegistry.get_model_id_for_kpi(self.kpi_name)

        if not allowed_models:
            raise ValueError(f"No model mapping found for KPI '{self.kpi_name}'")
        available_models = [model.model_id for kpi in allowed_models for model in kpi.model_stages]
        # Validate each model_info.model_id
        for model_info in self.model_infos:
            if model_info.model_id not in available_models:
                raise ValueError(
                    f"Model ID '{model_info.model_id}' is not valid for KPI '{self.kpi_name}'. "
                    f"Allowed models: {', '.join(available_models)}"
                )

        return self

    @model_validator(mode="after")
    def validate_regions_for_kpi(self):
        """Validate that region types match allowed types for the KPI"""
        if self.regions:
            from services.model.model_registry import ModelRegistry

            # Get allowed region types for this
            available_regions: list[
                KPIRegionMapping
            ] | None = ModelRegistry.get_allowed_regions_for_kpi(self.kpi_name)

            # If no specific restrictions (empty list), all region types are allowed
            if available_regions is not None:
                available_regions_list = [
                    b for a in available_regions for b in a.allowed_region_types
                ]
                for region in self.regions:
                    region_type_value = (
                        region.type.value if hasattr(region.type, "value") else str(region.type)
                    )
                    if region_type_value not in available_regions_list:
                        raise ValueError(
                            f"Region type '{region_type_value}' is not allowed for KPI '{self.kpi_name}'. "
                            f"Allowed region types: {', '.join(available_regions_list)}"
                        )

        return self

    @staticmethod
    def _build_stage_maps(
        kpi_mappings: list[KPIModelMapping],
    ) -> tuple[dict[str, int], dict[int, list[str]]]:
        model_to_stage_map: dict[str, int] = {}
        stages_by_number: dict[int, list[str]] = {}
        for mapping in kpi_mappings:
            for model_stage in mapping.model_stages:
                model_to_stage_map[model_stage.model_id] = model_stage.stage
                if model_stage.stage not in stages_by_number:
                    stages_by_number[model_stage.stage] = []
                stages_by_number[model_stage.stage].append(model_stage.model_id)
        return model_to_stage_map, stages_by_number

    def _validate_multistage_lead_by(
        self,
        model_info: ModelInfo,
        model_stage: int,
        model_to_stage_map: dict[str, int],
        stages_by_number: dict[int, list[str]],
        lead_by_model_ids: list[str],
    ) -> None:
        if model_stage <= 0:
            return
        if not model_info.lead_by:
            raise ValueError(
                f"Model '{model_info.model_id}' is in stage {model_stage}. "
                f"For multistage KPI '{self.kpi_name}', models in stage > 0 must have 'lead_by' field "
                f"to specify which stage 0 model they depend on."
            )
        if model_info.lead_by not in lead_by_model_ids:
            raise ValueError(
                f"Model '{model_info.model_id}' has lead_by='{model_info.lead_by}', "
                f"but this model is not in the provided model_infos list."
            )
        lead_by_stage = model_to_stage_map.get(model_info.lead_by)
        if lead_by_stage != 0:
            raise ValueError(
                f"Model '{model_info.model_id}' has lead_by='{model_info.lead_by}', "
                f"but '{model_info.lead_by}' is not a stage 0 model (it's stage {lead_by_stage}). "
                f"Available stage 0 models: {', '.join(stages_by_number.get(0, []))}"
            )

    def _validate_single_stage_no_lead_by(self, model_info: ModelInfo) -> None:
        if model_info.lead_by:
            raise ValueError(
                f"Model '{model_info.model_id}' has 'lead_by' specified, "
                f"but KPI '{self.kpi_name}' is a single-stage KPI (only stage 0). "
                f"Remove the 'lead_by' field for single-stage KPIs."
            )

    def _require_multistage_has_stage_0(
        self,
        stages_by_number: dict[int, list[str]],
        provided_model_ids: list[str],
    ) -> None:
        has_stage_0 = any(
            model_id in stages_by_number.get(0, []) for model_id in provided_model_ids
        )
        if not has_stage_0:
            raise ValueError(
                f"KPI '{self.kpi_name}' is a multistage KPI and requires at least one stage 0 model. "
                f"Available stage 0 models: {', '.join(stages_by_number.get(0, []))}"
            )

    def _validate_stage_order_non_decreasing(
        self,
        model_to_stage_map: dict[str, int],
    ) -> None:
        sorted_infos = sorted(self.model_infos, key=lambda x: x.order)
        for i in range(len(sorted_infos) - 1):
            current_model = sorted_infos[i]
            next_model = sorted_infos[i + 1]
            current_stage = model_to_stage_map.get(current_model.model_id, 0)
            next_stage = model_to_stage_map.get(next_model.model_id, 0)
            if next_stage < current_stage:
                raise ValueError(
                    f"Invalid stage ordering: Model '{next_model.model_id}' (stage {next_stage}) "
                    f"has order {next_model.order} which comes after "
                    f"model '{current_model.model_id}' (stage {current_stage}) with order {current_model.order}. "
                    f"Stage 0 models must come before stage 1 models in the 'order' field."
                )

    @model_validator(mode="after")
    def validate_kpi_multistage_mapping(self):
        """
        Validator for multistage KPI validation.
        First maps the KPI to understand stage structure, then validates:
        1. Model IDs belong to correct stages
        2. Stage ordering is correct (stage 0 before stage 1, etc.)
        3. Lead-by dependencies for multistage models
        """
        from services.model.model_registry import ModelRegistry

        kpi_mappings = ModelRegistry.get_model_id_for_kpi(self.kpi_name)
        if not kpi_mappings:
            raise ValueError(f"No model mapping found for KPI '{self.kpi_name}'")

        model_to_stage_map, stages_by_number = self._build_stage_maps(kpi_mappings)
        is_multistage = len(stages_by_number) > 1
        lead_by_model_ids = [mi.model_id for mi in self.model_infos]

        for model_info in self.model_infos:
            model_stage = model_to_stage_map.get(model_info.model_id)
            if model_stage is None:
                continue
            if is_multistage:
                self._validate_multistage_lead_by(
                    model_info,
                    model_stage,
                    model_to_stage_map,
                    stages_by_number,
                    lead_by_model_ids,
                )
            else:
                self._validate_single_stage_no_lead_by(model_info)

        if is_multistage:
            provided_model_ids = [mi.model_id for mi in self.model_infos]
            self._require_multistage_has_stage_0(stages_by_number, provided_model_ids)
            if len(self.model_infos) > 1:
                self._validate_stage_order_non_decreasing(model_to_stage_map)

        return self
