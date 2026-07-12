from typing import Any, cast

import numpy as np

from services.loaders.idata_loader import IDataLoader


class DetectionDataLoader(IDataLoader):
    """Loads data for standard detection models (bounding boxes)."""

    def load(self, result: Any, _frame: np.ndarray) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], result)
