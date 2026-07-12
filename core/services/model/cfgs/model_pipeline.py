from typing import Any

import numpy as np
import torch.nn as nn

from services.common.models.pipe_structure import PipeStructure


def order_by(model_info: PipeStructure) -> int:
    return model_info.order


class ModelPipeline(nn.Module):
    """
    Generic cascade pipeline supporting dependent and independent YOLO stages.
    """

    def __init__(self, model_configs: list[PipeStructure]):
        super().__init__()
        self.model_configs: list[PipeStructure] = sorted(model_configs, key=order_by)
        self.model_ids = ",".join([model.model_id for model in model_configs])

    def forward(self, image: np.ndarray) -> list[dict[str, Any]]:
        results_dict: dict[str, list[dict[str, Any]]] = {}

        for model_config in self.model_configs:
            model = model_config.model
            dependency = model_config.lead_by

            if dependency and dependency in results_dict:
                prev_results = results_dict[dependency]
            else:
                prev_results = None

            output = model(image, prev_results)
            if prev_results is not None:
                assert dependency is not None
                results_dict[dependency] = prev_results
            else:
                results_dict[model_config.model_id] = output

        return [v for values in results_dict.values() for v in values]
