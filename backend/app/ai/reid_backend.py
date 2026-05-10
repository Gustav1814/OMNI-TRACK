"""
Pluggable Re-ID backends.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from loguru import logger


class ReIDBackend:
    def extract(self, person_crop: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def extract_batch(self, crops: List[np.ndarray]) -> List[np.ndarray]:
        return [self.extract(c) for c in crops]

    @property
    def is_loaded(self) -> bool:
        return False


class TorchreidBackend(ReIDBackend):
    def __init__(self, model_name: str = "osnet_x1_0", device: str = "cpu"):
        self._model = None
        self._transform = None
        self._device = device
        self._dim = 512
        try:
            import torch
            import torchvision.transforms as T
            import torchreid
            self._torch = torch
            self._transform = T.Compose([
                T.ToPILImage(),
                T.Resize((256, 128)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            self._model = torchreid.models.build_model(name=model_name, num_classes=1000, pretrained=True).to(device)
            self._model.eval()
            logger.info(f"Torchreid backend loaded: {model_name} on {device}")
        except Exception as e:
            logger.warning(f"Torchreid backend unavailable, using mock embeddings: {e}")

    def extract(self, person_crop: np.ndarray) -> np.ndarray:
        if self._model is None or self._transform is None:
            emb = np.random.randn(self._dim).astype(np.float32)
            return emb / max(np.linalg.norm(emb), 1e-9)
        rgb = person_crop[:, :, ::-1].copy()
        t = self._transform(rgb).unsqueeze(0).to(self._device)
        with self._torch.no_grad():
            f = self._model(t)
        emb = f.cpu().numpy().flatten().astype(np.float32)
        n = np.linalg.norm(emb)
        return emb / n if n > 0 else emb

    @property
    def is_loaded(self) -> bool:
        return self._model is not None


class FastReIDBackend(ReIDBackend):
    """
    Optional workstation backend. Falls back to mock if fastreid isn't installed.
    """

    def __init__(self, weights_path: str = "", device: str = "cuda"):
        self._dim = 2048
        self._loaded = False
        self._device = device
        self._weights = weights_path
        try:
            import torch
            self._torch = torch
            self._loaded = False
            logger.info("FastReID backend selected (lazy init placeholder)")
        except Exception as e:
            logger.warning(f"FastReID backend unavailable, using mock embeddings: {e}")

    def extract(self, person_crop: np.ndarray) -> np.ndarray:
        emb = np.random.randn(self._dim).astype(np.float32)
        return emb / max(np.linalg.norm(emb), 1e-9)

    @property
    def is_loaded(self) -> bool:
        return self._loaded


def build_reid_backend(name: str, model_name: str, weights: str, device: str) -> ReIDBackend:
    n = (name or "torchreid").lower()
    if n == "fastreid":
        return FastReIDBackend(weights_path=weights, device=device)
    return TorchreidBackend(model_name=model_name, device=device)
