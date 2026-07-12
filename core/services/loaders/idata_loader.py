from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class IDataLoader(ABC):
    """Interface for loading model-specific data (e.g., bbox, keypoints, masks)."""

    @abstractmethod
    def load(self, result: Any, frame: np.ndarray) -> list[dict[str, Any]]:
        ...
