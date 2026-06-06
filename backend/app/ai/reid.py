"""
OmniTrack AI — Torchreid Re-Identification Module
Global person Re-ID using osnet_x1_0 backbone.
One shared gallery across ALL cameras: same person on Cam 1 and Cam 2 gets the same global_id.

Body-based (not face): uses full-body appearance (clothing, shape, pose) so it works when
the person's face is not towards the camera. Multiple embeddings per identity (different
angles/poses) improve matching when the same person is seen from the back or side.

Enterprise features:
  - Structured gallery with Dict[str, GalleryIdentity] for O(1) lookup per ID
  - Centroid embedding per identity for fast initial screening (avoids O(N×M) brute force)
  - LRU eviction when gallery exceeds max_identities
  - Temporal metadata (last_seen, last_camera, appearance_count) for blind-spot recovery
  - merge_identities() for post-hoc deduplication
  - prune_stale_identities() for memory management
"""

import time
import numpy as np
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass, field
from loguru import logger
from app.config import settings

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


@dataclass
class EmbeddingEntry:
    """A single stored embedding with metadata for quality-aware matching."""
    vector: np.ndarray          # 512-d L2-normalized float32
    timestamp: float = 0.0      # epoch when this embedding was captured
    camera_id: int = -1         # which camera captured this view
    quality_score: float = 1.0  # higher = better crop (aspect, size, confidence)


@dataclass
class GalleryIdentity:
    """
    All information about one tracked person in the global gallery.
    Supports multi-view matching, temporal tracking, and blind-spot recovery.
    """
    global_id: str
    embeddings: List[EmbeddingEntry] = field(default_factory=list)
    centroid: Optional[np.ndarray] = None  # running weighted average for fast screening
    last_seen: float = 0.0                 # epoch — when was this person last detected
    last_camera_id: int = -1               # last camera that saw this person
    last_bbox: List[float] = field(default_factory=list)  # last bounding box [x,y,w,h]
    appearance_count: int = 0              # total times this identity has been matched
    created_at: float = 0.0                # epoch — when this identity was first created
    max_embeddings: int = 8                # per-identity cap

    def recompute_centroid(self) -> None:
        """Recompute centroid as quality-weighted average of stored embeddings."""
        if not self.embeddings:
            self.centroid = None
            return
        weights = np.array([e.quality_score for e in self.embeddings], dtype=np.float32)
        total = weights.sum()
        if total <= 0:
            weights = np.ones(len(self.embeddings), dtype=np.float32)
            total = float(len(self.embeddings))
        vectors = np.stack([e.vector for e in self.embeddings], axis=0)
        centroid = (vectors * weights[:, None]).sum(axis=0) / total
        norm = np.linalg.norm(centroid)
        self.centroid = (centroid / norm).astype(np.float32) if norm > 0 else centroid.astype(np.float32)

    def add_embedding(self, entry: EmbeddingEntry) -> None:
        """Add embedding, evict oldest if at capacity, recompute centroid."""
        if len(self.embeddings) >= self.max_embeddings:
            # Evict lowest quality or oldest
            self.embeddings.sort(key=lambda e: (e.quality_score, e.timestamp))
            self.embeddings.pop(0)
        self.embeddings.append(entry)
        self.last_seen = max(self.last_seen, entry.timestamp)
        self.last_camera_id = entry.camera_id
        self.appearance_count += 1
        self.recompute_centroid()

    def best_similarity(self, query: np.ndarray) -> float:
        """Best cosine similarity between query and any stored embedding."""
        if not self.embeddings:
            return 0.0
        return max(float(np.dot(query, e.vector)) for e in self.embeddings)

    def centroid_similarity(self, query: np.ndarray) -> float:
        """Fast screening similarity using centroid embedding."""
        if self.centroid is None:
            return 0.0
        return float(np.dot(query, self.centroid))


class PersonReID:
    """
    Person Re-Identification using Torchreid (body-based, not face).
    One GLOBAL gallery shared by all cameras. Enterprise-grade with:
    - Structured identity gallery with O(1) lookup
    - Centroid-first search: screen candidates fast, then refine top-K
    - LRU eviction at gallery capacity
    - Temporal metadata for blind-spot recovery
    """

    EMBEDDING_DIM = 512

    def __init__(
        self,
        model_name: str = "osnet_x1_0",
        device: str = "auto",
        similarity_threshold: float = 0.6,
        max_embeddings_per_id: int = 8,
        max_identities: int = 5000,
    ):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.similarity_threshold = similarity_threshold
        self.max_embeddings_per_id = max(1, max_embeddings_per_id)
        self.max_identities = max(100, max_identities)
        self.model = None
        self.transform = self._build_transform()

        # Enterprise gallery: global_id → GalleryIdentity
        self._identities: Dict[str, GalleryIdentity] = {}
        # LRU access order: most recently accessed global_id is at the end
        self._lru_order: List[str] = []

        self._load_model()

    # ── backward-compat property ──────────────────────────────
    @property
    def _gallery(self) -> List[Tuple[str, np.ndarray]]:
        """Backward-compatible flat gallery view for warm-up and status code."""
        result = []
        for gid, identity in self._identities.items():
            for entry in identity.embeddings:
                result.append((gid, entry.vector))
        return result

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

    def extract_embedding(self, person_crop: np.ndarray) -> Optional[np.ndarray]:
        """
        Extract a 512-d L2-normalized embedding from a cropped person image.
        Input: BGR numpy array (from OpenCV crop).
        Output: 512-d normalized float32 array.
        """
        if self.model is None or self.transform is None:
            if not settings.ALLOW_MOCK_AI:
                return None
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

    def extract_batch(self, crops: List[np.ndarray]) -> List[Optional[np.ndarray]]:
        """Batch embedding extraction for crowded frames."""
        if not crops:
            return []
        if self.model is None or self.transform is None:
            return [self.extract_embedding(crop) for crop in crops]

        tensors = []
        valid_indexes = []
        for idx, crop in enumerate(crops):
            if crop is None or crop.size == 0:
                continue
            try:
                rgb = crop[:, :, ::-1].copy()
                tensors.append(self.transform(rgb))
                valid_indexes.append(idx)
            except Exception:
                continue
        output: List[Optional[np.ndarray]] = [None] * len(crops)
        if not tensors:
            return output

        batch = torch.stack(tensors, dim=0).to(self.device)
        with torch.no_grad():
            features = self.model(batch)
        embeddings = features.cpu().numpy()
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = np.divide(
            embeddings,
            np.maximum(norms, 1e-12),
            out=np.zeros_like(embeddings, dtype=np.float32),
        )
        for idx, emb in zip(valid_indexes, embeddings):
            output[idx] = emb.astype(np.float32)
        return output

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Cosine similarity between two embeddings."""
        return float(np.dot(emb1, emb2))

    def best_similarity_for_id(self, global_id: str, query: np.ndarray) -> float:
        """Best cosine similarity between query and any stored view for one identity."""
        identity = self._identities.get(global_id)
        if identity is None:
            return 0.0
        return identity.best_similarity(query)

    # ── LRU management ────────────────────────────────────────

    def _touch_lru(self, global_id: str) -> None:
        """Move identity to end of LRU list (most recently used)."""
        try:
            self._lru_order.remove(global_id)
        except ValueError:
            pass
        self._lru_order.append(global_id)

    def _evict_lru(self) -> None:
        """Evict least recently used identities until under capacity."""
        while len(self._identities) >= self.max_identities and self._lru_order:
            evict_id = self._lru_order.pop(0)
            self._identities.pop(evict_id, None)
            logger.debug(f"Gallery LRU evicted: {evict_id}")

    # ── Gallery operations ────────────────────────────────────

    def add_to_gallery(
        self,
        global_id: str,
        embedding: np.ndarray,
        camera_id: int = -1,
        timestamp: float = 0.0,
        quality_score: float = 1.0,
        bbox: Optional[List[float]] = None,
    ) -> None:
        """
        Add a person to the global gallery. Creates identity if new,
        or adds a new view if existing.
        """
        emb = embedding.astype(np.float32)
        now = timestamp or time.time()

        identity = self._identities.get(global_id)
        if identity is None:
            # Evict LRU if at capacity
            if len(self._identities) >= self.max_identities:
                self._evict_lru()

            identity = GalleryIdentity(
                global_id=global_id,
                created_at=now,
                max_embeddings=self.max_embeddings_per_id,
            )
            self._identities[global_id] = identity

        entry = EmbeddingEntry(
            vector=emb,
            timestamp=now,
            camera_id=camera_id,
            quality_score=quality_score,
        )
        identity.add_embedding(entry)
        if bbox:
            identity.last_bbox = [float(v) for v in bbox[:4]]

        self._touch_lru(global_id)

    def add_embedding_to_id(
        self,
        global_id: str,
        embedding: np.ndarray,
        camera_id: int = -1,
        timestamp: float = 0.0,
        quality_score: float = 1.0,
        bbox: Optional[List[float]] = None,
    ) -> None:
        """
        Add another view/angle for an existing identity (e.g. person turned).
        If identity doesn't exist yet, creates it.
        """
        self.add_to_gallery(
            global_id, embedding,
            camera_id=camera_id,
            timestamp=timestamp,
            quality_score=quality_score,
            bbox=bbox,
        )

    def search_gallery(
        self,
        query: np.ndarray,
        top_k: int = 1,
        threshold: Optional[float] = None,
    ) -> List[Dict]:
        """
        Search the GLOBAL gallery using centroid-first screening:
        1. Compute centroid similarity for ALL identities (fast dot product)
        2. Take top candidates (3× top_k)
        3. Refine with best-of-N multi-view similarity
        Only returns matches above threshold.
        """
        th = threshold if threshold is not None else self.similarity_threshold

        if not self._identities:
            return []

        # Phase 1: centroid screening (fast)
        centroid_scores: List[Tuple[str, float]] = []
        for gid, identity in self._identities.items():
            csim = identity.centroid_similarity(query)
            # Use a relaxed threshold for centroid screening (allow 0.1 below)
            if csim >= th - 0.12:
                centroid_scores.append((gid, csim))

        # Sort by centroid similarity, take top candidates for refinement
        centroid_scores.sort(key=lambda x: x[1], reverse=True)
        refine_count = min(len(centroid_scores), max(top_k * 3, 15))
        candidates = centroid_scores[:refine_count]

        # Phase 2: multi-view refinement (accurate but slower)
        refined: List[Dict] = []
        for gid, _csim in candidates:
            identity = self._identities.get(gid)
            if identity is None:
                continue
            best_sim = identity.best_similarity(query)
            if best_sim >= th:
                refined.append({"id": gid, "similarity": best_sim})

        refined.sort(key=lambda x: x["similarity"], reverse=True)
        return refined[:top_k]

    def find_matches(
        self,
        query: np.ndarray,
        gallery: List[Tuple[str, np.ndarray]],
        threshold: float = 0.6,
        top_k: int = 10,
    ) -> List[Dict]:
        """
        Find top-K matches against an external flat gallery.
        Each global_id can have multiple embeddings;
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

    # ── Identity management ───────────────────────────────────

    def get_identity(self, global_id: str) -> Optional[GalleryIdentity]:
        """Get a gallery identity by ID."""
        return self._identities.get(global_id)

    def update_identity_seen(
        self,
        global_id: str,
        camera_id: int,
        bbox: List[float],
        timestamp: float = 0.0,
    ) -> None:
        """Update last-seen metadata without adding a new embedding."""
        identity = self._identities.get(global_id)
        if identity is None:
            return
        now = timestamp or time.time()
        identity.last_seen = now
        identity.last_camera_id = camera_id
        identity.last_bbox = [float(v) for v in bbox[:4]]
        identity.appearance_count += 1
        self._touch_lru(global_id)

    def merge_identities(self, keep_id: str, merge_id: str) -> bool:
        """
        Merge two identities that are discovered to be the same person.
        Keeps keep_id, absorbs embeddings from merge_id, deletes merge_id.
        Returns True if merge happened.
        """
        keep = self._identities.get(keep_id)
        merge = self._identities.get(merge_id)
        if keep is None or merge is None or keep_id == merge_id:
            return False

        # Absorb embeddings from merge_id (respect capacity)
        for entry in merge.embeddings:
            keep.add_embedding(entry)

        # Keep the older creation time, higher appearance count
        keep.created_at = min(keep.created_at, merge.created_at)
        keep.appearance_count += merge.appearance_count

        # Remove merged identity
        self._identities.pop(merge_id, None)
        try:
            self._lru_order.remove(merge_id)
        except ValueError:
            pass
        self._touch_lru(keep_id)

        logger.info(f"Merged identity {merge_id} → {keep_id}")
        return True

    def prune_stale_identities(self, max_age_seconds: float = 3600.0) -> int:
        """
        Remove identities not seen for longer than max_age_seconds.
        Returns count of pruned identities.
        """
        now = time.time()
        cutoff = now - max_age_seconds
        stale = [gid for gid, ident in self._identities.items() if ident.last_seen < cutoff]
        for gid in stale:
            self._identities.pop(gid, None)
            try:
                self._lru_order.remove(gid)
            except ValueError:
                pass
        if stale:
            logger.info(f"Pruned {len(stale)} stale identities (older than {max_age_seconds:.0f}s)")
        return len(stale)

    def get_gallery_stats(self) -> Dict:
        """Return gallery statistics for monitoring."""
        total_embeddings = sum(len(i.embeddings) for i in self._identities.values())
        return {
            "total_identities": len(self._identities),
            "total_embeddings": total_embeddings,
            "max_identities": self.max_identities,
            "max_embeddings_per_id": self.max_embeddings_per_id,
            "lru_order_size": len(self._lru_order),
        }

    def _mock_embedding(self) -> np.ndarray:
        """Generate a random normalized embedding for testing."""
        emb = np.random.randn(self.EMBEDDING_DIM).astype(np.float32)
        return emb / np.linalg.norm(emb)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
