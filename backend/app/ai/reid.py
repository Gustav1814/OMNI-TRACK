"""
OmniTrack AI — Torchreid Re-Identification Module
Global person Re-ID using osnet_x1_0 backbone.
One shared gallery across ALL cameras: same person on Cam 1 and Cam 2 gets the same global_id.

Body-based (not face): uses full-body appearance (clothing, shape, pose) so it works when
the person's face is not towards the camera. Multiple embeddings per identity (different
angles/poses) improve matching when the same person is seen from the back or side.
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
    Person Re-Identification using Torchreid (body-based, not face).
    One GLOBAL gallery shared by all cameras. Supports multiple embeddings per
    global_id so the same person seen from different angles (face away, side)
    still matches.
    """

    EMBEDDING_DIM = 512

    def __init__(
        self,
        model_name: str = "osnet_x1_0",
        device: str = "auto",
        similarity_threshold: float = 0.6,
        max_embeddings_per_id: int = 5,
    ):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.similarity_threshold = similarity_threshold
        self.max_embeddings_per_id = max(1, max_embeddings_per_id)
        self.model = None
        self.transform = self._build_transform()
        # Gallery: (global_id, embedding). Same global_id can appear multiple times (multi-view).
        self._gallery: List[Tuple[str, np.ndarray]] = []
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

    def add_to_gallery(self, global_id: str, embedding: np.ndarray) -> None:
        """
        Add a person to the global gallery. If this global_id already has
        max_embeddings_per_id entries, drop the oldest so we keep diverse
        views (front, back, side) for matching when face is not visible.
        """
        emb = embedding.astype(np.float32)
        same_id = [(i, g, e) for i, (g, e) in enumerate(self._gallery) if g == global_id]
        if len(same_id) >= self.max_embeddings_per_id:
            # Remove oldest (first occurrence) for this id
            idx = same_id[0][0]
            self._gallery.pop(idx)
        self._gallery.append((global_id, emb))

    def add_embedding_to_id(self, global_id: str, embedding: np.ndarray) -> None:
        """
        Add another view/angle for an existing identity (e.g. person turned).
        Uses same cap as add_to_gallery. Call when Re-ID matched so we store
        back/side views for future matching.
        """
        self.add_to_gallery(global_id, embedding.astype(np.float32))

    def search_gallery(
        self,
        query: np.ndarray,
        top_k: int = 1,
        threshold: Optional[float] = None,
    ) -> List[Dict]:
        """
        Search the GLOBAL gallery. For each global_id we take the BEST
        similarity over all its embeddings (multi-view), so same person
        from different angles still matches.
        """
        th = threshold if threshold is not None else self.similarity_threshold
        matches = self.find_matches(query, self._gallery, threshold=th, top_k=top_k)
        return [{"id": m["global_id"], "similarity": m["similarity"]} for m in matches]

    def find_matches(
        self,
        query: np.ndarray,
        gallery: List[Tuple[str, np.ndarray]],
        threshold: float = 0.6,
        top_k: int = 10,
    ) -> List[Dict]:
        """
        Find top-K matches. Each global_id can have multiple embeddings;
        we use the best similarity per identity (so multiple views help).
        """
        best_per_id: Dict[str, float] = {}
        for gid, emb in gallery:
            sim = self.compute_similarity(query, emb)
            if sim >= threshold:
                best_per_id[gid] = max(best_per_id.get(gid, sim), sim)
        scores = [{"global_id": gid, "similarity": sim} for gid, sim in best_per_id.items()]
        scores.sort(key=lambda x: x["similarity"], reverse=True)
        return scores[:top_k]

    def _mock_embedding(self) -> np.ndarray:
        """Generate a random normalized embedding for testing."""
        emb = np.random.randn(self.EMBEDDING_DIM).astype(np.float32)
        return emb / np.linalg.norm(emb)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
