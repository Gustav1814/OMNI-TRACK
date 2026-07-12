from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import wraps
from typing import Any, cast

import torch.nn as nn

from constants.detections_constant import BBOX, CLASS_ID, CLASS_NAME, CONFIDENCE, GUID_ID
from utils.guid import generate_short_guid


class BaseStage(nn.Module):
    """
    Abstract base class for all cascade stages.
    """

    # All stages should add a stable identifier for each detection so callers can track objects
    # across stages or across time. Subclasses should call `self._ensure_guid(detection)` in their
    # forward() implementation for each output detection dict.
    REQUIRED_RESULT_KEYS = {BBOX, CONFIDENCE, CLASS_ID, CLASS_NAME, GUID_ID}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        original_forward = cls.__dict__.get("forward")
        if original_forward is None or getattr(original_forward, "__base_stage_wrapped__", False):
            return

        @wraps(original_forward)
        def wrapped_forward(self: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            results = original_forward(self, *args, **kwargs)
            BaseStage._validate_forward_output(results, cls.__name__)
            return cast(list[dict[str, Any]], results)

        wrapped_forward.__base_stage_wrapped__ = True  # type: ignore[attr-defined]
        cls.forward = wrapped_forward  # type: ignore[method-assign]

    def __init__(self, model_id: str):
        super().__init__()
        self.model_id = model_id
        self.is_remote = False

    def health_check(self) -> bool:
        """Override in remote stages to implement a health check."""
        raise NotImplementedError(
            "Health check not implemented for this  stage. Make sure to override health_check() in the stage class if it contains remote functionality."
        )

    def forward(
        self, image: Any, prev_results: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """
        Runs inference on the input image.
        - image: original frame or cropped image
        - prev_results: list of dicts from previous stages
        """
        raise NotImplementedError("Must implement forward() in subclass")

    def postprocess(self, results: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
        """Convert raw model output to dicts with boxes, labels, etc."""
        return results

    @property
    def names(self) -> Mapping[int, str] | Sequence[str]:
        """
        Return the label names exposed by the underlying model.
        Implementations should provide either an integer-indexed mapping or an ordered sequence.
        """
        raise NotImplementedError("Subclasses must implement the names property.")

    @staticmethod
    def _ensure_name_mapping(
        names: Mapping[int, str] | Sequence[str] | None,
    ) -> dict[int, str]:
        """
        Normalise model name containers into a dictionary keyed by class index.
        """
        if not names:
            return {}

        if isinstance(names, Mapping):
            return dict(names)

        return {index: str(name) for index, name in enumerate(names)}

    @staticmethod
    def _ensure_guid(result: dict[str, Any]) -> None:
        """Ensure a detection dict has a stable GUID for tracking."""
        if GUID_ID not in result:
            result[GUID_ID] = generate_short_guid()

    @staticmethod
    def _validate_forward_output(results: Any, stage_name: str) -> None:
        """
        Ensure the forward output is a list of dicts with required keys.
        """
        if results is None:
            return

        if not isinstance(results, list):
            raise TypeError(
                f"{stage_name}.forward must return a list of dictionaries, "
                f"got {type(results).__name__}."
            )

        for index, item in enumerate(results):
            if not isinstance(item, dict):
                raise TypeError(
                    f"{stage_name}.forward result at index {index} must be a dict, "
                    f"got {type(item).__name__}."
                )

            missing = BaseStage.REQUIRED_RESULT_KEYS.difference(item.keys())
            if missing:
                missing_keys = ", ".join(sorted(missing))
                raise KeyError(
                    f"{stage_name}.forward result at index {index} is missing required keys: "
                    f"{missing_keys}."
                )
