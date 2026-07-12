from typing import Any

import numpy as np

from services.common.models.yolo_models import KPIResult
from services.kpis.ikpi import IKPI


class KPIManager:
    """
    Registry for KPI implementations. Register new KPIs here (or via DI/container).
    """

    def __init__(self, registry: dict[str, IKPI] | None = None):
        self._registry: dict[str, IKPI] = registry if registry else {}

    def register(self, name: str, impl: IKPI) -> None:
        self._registry[name] = impl

    async def calculate_kpi(
        self,
        name: str,
        frame: np.ndarray,
        detections: list[dict[str, Any]],
        **kwargs: Any,
    ) -> KPIResult | None:
        # fallback: support patterns like "person_count" -> many KPIs; you can map aliases here
        impl = self._registry.get(name)
        if impl:
            return await impl.compute(frame, detections, **kwargs)

        # fallback: try simple contains (e.g., name contains "count")
        for key, handler in self._registry.items():
            if key in name:
                return await handler.compute(frame, detections, **kwargs)

        return None
