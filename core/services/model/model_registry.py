# services/model_registry.py
"""
Model Registry - A singleton class to manage multiple YOLO model instances.
This allows loading multiple models and reusing them across different tasks.
"""

import torch
from ultralytics import RTDETR, YOLO

from constants.model_kpi_region_mapping_constant import MODEL_REGISTRY_CONFIG
from core.container import container
from services.common.models.model_kpi_region_mapping import KPIModelMapping, KPIRegionMapping
from services.core.interfaces.ilogger_service import ILoggerService


class ModelRegistry:
    """
    Singleton class to manage multiple YOLO model instances.
    Stores models in a static dictionary keyed by model_id.
    Also manages KPI to model mapping (one-to-many relationship).
    """

    _instance = None
    _models: dict[str, YOLO | RTDETR] = {}
    _model_names: dict[str, str] = {}
    _device: str = "cuda" if torch.cuda.is_available() else "cpu"
    _config = MODEL_REGISTRY_CONFIG

    # -------------------------------------------------
    # ✅ KPI to Model Mapping (refactored, no hardcoded strings)
    # -------------------------------------------------
    KPI_MODEL_MAPPING: list[KPIModelMapping] = _config.kpi_model_mappings
    KPI_REGION_MAPPING: list[KPIRegionMapping] = _config.kpi_region_mappings

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        pass

    async def load_model(self, model_id: str, model_path: str) -> bool:
        """
        Load a YOLO model into the registry.

        Args:
            model_id: Unique identifier for the model (e.g., "general_object_detection", "oil_detection")
            model_path: Path to the model weights file

        Returns:
            True if model loaded successfully, False otherwise
        """
        try:
            logger: ILoggerService = container.resolve(ILoggerService)
            # Check if model already loaded
            if model_id in self._models:
                await logger.info(f"Model {model_id} already loaded")
                return True

            # Detect model type and load accordingly
            # RT-DETR models have 'rtdetr' in their model_id
            if "rtdetr" in model_id.lower():
                self._models[model_id] = RTDETR(model_path)
            else:
                self._models[model_id] = YOLO(model_path)

            self._model_names[model_id] = model_path

            await logger.info(f"Model {model_id} loaded successfully from {model_path}")

            return True
        except Exception as e:
            print(f"Failed to load model {model_id}: {e}")
            return False

    def get_model(self, model_id: str) -> YOLO | RTDETR | None:
        """
        Get a model instance by ID.

        Args:
            model_id: Unique identifier for the model

        Returns:
            YOLO model instance or None if not found
        """
        return self._models.get(model_id)

    def get_model_name(self, model_id: str) -> str | None:
        """
        Get the model file path/name by ID.

        Args:
            model_id: Unique identifier for the model

        Returns:
            Model file path or None if not found
        """
        return self._model_names.get(model_id)

    def is_model_loaded(self, model_id: str) -> bool:
        """
        Check if a model is loaded in the registry.

        Args:
            model_id: Unique identifier for the model

        Returns:
            True if model is loaded, False otherwise
        """
        return model_id in self._models

    def get_all_loaded_models(self) -> dict[str, str]:
        """
        Get all loaded model IDs and their paths.

        Returns:
            Dictionary mapping model_id to model_path
        """
        return self._model_names.copy()

    def unload_model(self, model_id: str) -> bool:
        """
        Unload a model from the registry.

        Args:
            model_id: Unique identifier for the model

        Returns:
            True if model was unloaded, False if not found
        """
        if model_id in self._models:
            del self._models[model_id]
            del self._model_names[model_id]
            return True
        return False

    def get_device(self) -> str:
        """
        Get the device (cuda/cpu) being used.

        Returns:
            Device string
        """
        return self._device

    @classmethod
    def get_model_id_for_kpi(cls, kpi_name: str) -> list[KPIModelMapping]:
        """
        Get KPI-to-model mappings for the given KPI name.

        Args:
            kpi_name: Name of the KPI

        Returns:
            Matching KPI model mappings (empty if none)
        """
        return [stage for stage in cls.KPI_MODEL_MAPPING if stage.kpi_name == kpi_name]

    @classmethod
    def get_allowed_regions_for_kpi(cls, kpi_name: str) -> list[KPIRegionMapping] | None:
        """
        Get allowed region types for a given KPI.

        Args:
            kpi_name: Name of the KPI

        Returns:
            List of allowed region types
        """
        return [stage for stage in cls.KPI_REGION_MAPPING if stage.kpi_name == kpi_name]

    @classmethod
    def validate_model_for_kpi(cls, kpi_name: str, model_id: str) -> tuple[bool, str]:
        """
        Validate if a model_id is valid for a given KPI.

        Args:
            kpi_name: Name of the KPI
            model_id: Model ID to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        allowed_models = cls.get_model_id_for_kpi(kpi_name)

        if not allowed_models:
            return False, f"No model mapping found for KPI '{kpi_name}'"

        allowed_model_ids = [stage.model_id for m in allowed_models for stage in m.model_stages]

        if model_id not in allowed_model_ids:
            return (
                False,
                f"Model ID '{model_id}' is not valid for KPI '{kpi_name}'. Allowed models: {', '.join(allowed_model_ids)}",
            )
        return True, ""

    @classmethod
    def validate_regions_for_kpi(cls, kpi_name: str, region_types: list[str]) -> tuple[bool, str]:
        """
        Validate if region types are allowed for a given KPI.

        Args:
            kpi_name: Name of the KPI
            region_types: List of region type values to validate

        Returns:
            Tuple of (is_valid, error_message)
        """

        available_regions: list[KPIRegionMapping] | None = cls.get_allowed_regions_for_kpi(kpi_name)
        invalid_types = []
        available_regions_list: list[str] = []
        # If no specific restrictions (empty list), all region types are allowed
        if available_regions is not None:
            available_regions_list = [b for a in available_regions for b in a.allowed_region_types]
            for region in region_types:
                if region not in available_regions_list:
                    invalid_types.append(region)

        if invalid_types:
            return (
                False,
                f"Region types {invalid_types} are not allowed for KPI '{kpi_name}'. Allowed region types: {', '.join(invalid_types)}",
            )

        return True, ""


# Create a singleton instance
model_registry = ModelRegistry()
