from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from dtos.request.request_register_use_case import Region
from services.managers.color_manager import ColorManager


class IAnnotationRenderer(ABC):
    """Interface for rendering annotations on frames."""

    @abstractmethod
    def render(
        self,
        frame: np.ndarray,
        detection: dict[str, Any],
        regions: list[Region],
        color_manager: ColorManager,
        **kwargs: Any,
    ) -> np.ndarray:
        pass

    def get_color_track(self, region_name: str, track_id: int, global_id: object) -> str | None:
        color_track_label = None
        if len(region_name) > 0 and region_name != "global":
            color_track_label = region_name
        elif track_id != -1:
            color_track_label = str(track_id)
        elif global_id != "N/A":
            color_track_label = str(global_id)
        return color_track_label
