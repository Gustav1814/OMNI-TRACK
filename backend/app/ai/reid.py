"""
OmniTrack AI — Torchreid Re-Identification Module
Global person Re-ID using osnet_x1_0 backbone.
Extracts 512-d feature embeddings, L2 normalized for cosine similarity.
"""

import numpy as np
from typing import List, Optional, Tuple, Dict
from loguru import logger

try:
    import torch
    import torchvision.transforms as T
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not installed. Re-ID will run in mock mode.")

try:
    import torchreid
    TORCHREID_AVAILABLE = True
except ImportError:
    TORCHREID_AVAILABLE = False
    logger.warning("Torchreid not installed. Re-ID will run in mock mode.")


class PersonReID:
    """
    Person Re-Identification using Torchreid.
    Extracts 512-d embeddings from cropped person images.
    """

    EMBEDDING_DIM = 512

    def __init__(self, model_name: str = "osnet_x1_0", device: str = "auto"):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.model = None
        self.transform = self._build_transform()
        self._load_model()

    def _resolve_device(self, device: str) -> str:
        if device == "auto":
            if TORCH_AVAILABLE and torch.cuda.is_available():
                return "cuda"
            return "cpu"
        return device

    def _build_transform(self):
        if not TORCH_AVAILABLE:
            return None
        return T.Compose([
            T.ToPILImage(),
            T.Resize((256, 128)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def _load_model(self):
        if not TORCHREID_AVAILABLE or not TORCH_AVAILABLE:
            logger.warning("Running Re-ID in mock mode")
            return
        try:
            self.model = torchreid.models.build_model(
                name=self.model_name,
                num_classes=1000,
                pretrained=True,
            )
            self.model = self.model.to(self.device)
            self.model.eval()
            logger.info(f"Loaded Re-ID model: {self.model_name} on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load Re-ID model: {e}")
            self.model = None

    def extract_embedding(self, person_crop: np.ndarray) -> np.ndarray:
        """
        Extract a 512-d L2-normalized embedding from a cropped person image.
        Input: BGR numpy array (from OpenCV crop).
        Output: 512-d normalized float32 array.
        """
        if self.model is None or self.transform is None:
            return self._mock_embedding()

        # Convert BGR to RGB
        rgb = person_crop[:, :, ::-1].copy()
        tensor = self.transform(rgb).unsqueeze(0).to(self.device)

        with torch.no_grad():
            features = self.model(tensor)

        embedding = features.cpu().numpy().flatten()
        # L2 normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding.astype(np.float32)

    def extract_batch(self, crops: List[np.ndarray]) -> List[np.ndarray]:
        """Batch embedding extraction."""
        return [self.extract_embedding(crop) for crop in crops]

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Cosine similarity between two embeddings."""
        return float(np.dot(emb1, emb2))

    def find_matches(
        self,
        query: np.ndarray,
        gallery: List[Tuple[str, np.ndarray]],
        threshold: float = 0.6,
        top_k: int = 10,
    ) -> List[Dict]:
        """
        Find top-K matches for a query embedding in a gallery.
        Gallery format: [(global_id, embedding), ...]
        """
        scores = []
        for gid, emb in gallery:
            sim = self.compute_similarity(query, emb)
            if sim >= threshold:
                scores.append({"global_id": gid, "similarity": sim})

        scores.sort(key=lambda x: x["similarity"], reverse=True)
        return scores[:top_k]

    def _mock_embedding(self) -> np.ndarray:
        """Generate a random normalized embedding for testing."""
        emb = np.random.randn(self.EMBEDDING_DIM).astype(np.float32)
        return emb / np.linalg.norm(emb)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
