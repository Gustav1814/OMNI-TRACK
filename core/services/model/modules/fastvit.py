"""
FastViT model wrapper for gender classification.
Loads pre-trained model directly from .pt file without timm dependency.
"""

from __future__ import annotations

from typing import cast

import torch
import torch.nn as nn


def create_fastvit_s12_apple_in1k(_num_classes: int = 2) -> nn.Module:
    """
    Create a simple wrapper that will load the complete model from .pt file.
    The gender_fastvit_ts.pt file already contains the full trained model.

    Args:
        num_classes: Number of output classes (2 for Male/Female)

    Returns:
        Identity wrapper - actual model will be loaded from checkpoint

    Note:
        This function returns a placeholder. The actual model loading
        happens in GenderClassificationStage2 when loading the .pt file.
    """
    # Return a simple identity module as placeholder
    # The actual model will be loaded directly from the .pt file
    return nn.Identity()


def get_model_info() -> dict:
    """
    Get information about the FastViT model.

    Returns:
        Dictionary containing model information
    """
    return {
        "model_name": "gender_fastvit_ts",
        "architecture": "FastViT",
        "input_size": (224, 224),
        "num_classes": 2,
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "library": "pytorch (no timm required)",
    }


# For backward compatibility with existing code
class FastViT(nn.Module):
    """
    Wrapper class for FastViT model.
    This is for backward compatibility if needed.
    """

    def __init__(self, num_classes: int = 2):
        """
        Initialize FastViT model.

        Args:
            num_classes: Number of output classes
        """
        super().__init__()
        self.model = create_fastvit_s12_apple_in1k(num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through the model.

        Args:
            x: Input tensor of shape (B, C, H, W)

        Returns:
            Output logits of shape (B, num_classes)
        """
        return cast(torch.Tensor, self.model(x))
