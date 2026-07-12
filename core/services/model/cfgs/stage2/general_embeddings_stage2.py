"""
General Embeddings Stage 2 — Dynamic embedding generation and identity resolution.

Extends Stage 1 detections by:
1. Generating embeddings using any IEmbedder implementation
2. Identifying identities via CacheService (external gRPC or local)
3. Attaching identity metadata to detections

Usage:
    embedder = ArcFaceEmbedder()  # Any IEmbedder implementation
    stage2 = GeneralEmbeddingsStage2(
        embedder=embedder,
        tag=["face"],
    )
    results = stage2(image=frame, prev_results=stage1_detections)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import torch

from constants.detections_constant import BBOX, CLASS_NAME, MODEL_ID
from services.model.cfgs.ibase_stage import BaseStage


class GeneralEmbeddingsStage2(BaseStage):
    """
    Stage 2 - Generate and resolve identities for detected objects.

    Extends Stage 1 detections by:
      1. Extracting crops for configured object classes
      2. Generating embeddings using any IEmbedder implementation
      3. Resolving identities via CacheService
      4. Attaching identity metadata to detections

    All embedders and cache backends are dynamically swappable.

    Parameters
    ----------
    embedder : IEmbedder
        Embedding model (e.g., ArcFaceEmbedder, CLIPEmbedder, custom).
        Must implement the IEmbedder interface.
    cache_name : str
        Unique name for the vector cache instance.
    cache_type : str, optional
        Backend type ('faiss', 'weaviate', etc.).
        Default: 'faiss'. Must be registered with VectorCacheManager.
    tags_to_embed : List[str] | str, optional
        Class names to generate embeddings for.
        Default: ['all'] (embed all detections).
    padding_ratio : float, optional
        Padding ratio for bounding box crops (0.0 = no padding).
        Default: 0.1 (10% padding on each side).
    """

    def __init__(
        self,
        model_path: str,
        model_id: str,
        tag: list[str] | str,
        device: str | None = None,
        embedder_type: str = "arcface",
        cache_name: str = "general_cache",
        cache_type: str = "faiss",
        padding_ratio: float = 0.1,
        **_kwargs: Any,
    ):
        """
        Initialize the Stage 2 processor with embedder and cache configuration.

        Args:
            model_path: Absolute path to the embedding model weights.
            model_id: Unique identifier for this stage.
            tag: Class names to generate embeddings for (e.g., ["face"]).
            device: Computing device (defaults to CUDA if available).
            embedder_type: type of embedder to create ("arcface", "clip").
            cache_name: Unique identifier for the cache instance.
            cache_type: Vector cache backend type.
            padding_ratio: Extra padding around detected regions.
        """
        super().__init__(model_id)

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.padding_ratio = padding_ratio
        self.cache_name = cache_name
        self.cache_type = cache_type

        # Use tag as tags_to_embed (standardized across stages)
        tags_to_embed: list[str]
        if isinstance(tag, str):
            tags_to_embed = [t.strip().lower() for t in tag.split(",")]
        else:
            tags_to_embed = [t.lower() for t in tag]
        self.tags_to_embed = tags_to_embed

        # Use Adapter to wrap the specific embedder implementation
        from services.embedders.arcface_embedder import ArcFaceEmbedder
        from services.embedders.embedder_adapter import EmbedderAdapter

        # Initialize the actual embedder module
        raw_embedder = ArcFaceEmbedder(model_path=model_path)

        # Wrap it into the generalized interface
        self.wrapper = EmbedderAdapter(
            wrapped=raw_embedder,
            embedding_dim=getattr(raw_embedder, "embedding_dim", 512),
            model_id=getattr(raw_embedder, "model_id", "arcface"),
        )
        self.embedder_instance = raw_embedder

        # Get dimension
        self.dimension = self.wrapper.embedding_dim

        # Initialize CacheService for identity resolution
        from services.core.cache_service import CacheService

        self.cache_service = CacheService(backend_type=cache_type)

        print(
            f"✓ Stage2 initialized: {model_id} | Type={embedder_type}, "
            f"Using CacheService (backend={cache_type}, dim={self.dimension})"
        )

    def _clip_bbox(self, bbox: Sequence[int], width: int, height: int) -> list[int] | None:
        """Ensure bounding box is within image boundaries."""
        x1, y1, x2, y2 = map(int, bbox)
        x1 = max(0, min(width - 1, x1))
        x2 = max(0, min(width - 1, x2))
        y1 = max(0, min(height - 1, y1))
        y2 = max(0, min(height - 1, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2, y2]

    def _extract_crop_with_padding(
        self,
        image: np.ndarray,
        bbox: list[int],
        padding_ratio: float,
    ) -> np.ndarray | None:
        """
        Extract a crop from the image with optional padding.

        Args:
            image: Input image (HxWxC).
            bbox: [x1, y1, x2, y2] bounding box.
            padding_ratio: Padding ratio (0.0 = no padding).

        Returns:
            Cropped image, or None if invalid.
        """
        h, w = image.shape[:2]
        x1, y1, x2, y2 = map(int, bbox)

        box_w = x2 - x1
        box_h = y2 - y1
        pad_w = int(box_w * padding_ratio)
        pad_h = int(box_h * padding_ratio)

        # Apply padding
        nx1 = max(0, x1 - pad_w)
        ny1 = max(0, y1 - pad_h)
        nx2 = min(w, x2 + pad_w)
        ny2 = min(h, y2 + pad_h)

        crop = image[ny1:ny2, nx1:nx2]
        if crop.size == 0:
            return None

        return crop

    def forward(
        self,
        image: np.ndarray,
        prev_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Process Stage 1 detections by generating and storing embeddings.

        Workflow:
          1. Receive detections from Stage 1 (with bounding boxes)
          2. Extract crops for configured object classes
          3. Generate embeddings using the IEmbedder
          4. Store in the IVectorCache backend
          5. Attach embedding metadata to detections
          6. Return enriched detections

        Args:
            image: Input frame/image.
            prev_results: List of detections from Stage 1.

        Returns:
            Enriched detections with embedding metadata.
        """
        if not prev_results:
            return []

        h, w = image.shape[:2]

        for detection in prev_results:
            # Ensure stable tracking ID across stages
            self._ensure_guid(detection)

            # Filter by class name
            class_name = detection.get(CLASS_NAME, "").lower()
            if "all" not in self.tags_to_embed and class_name not in self.tags_to_embed:
                continue

            bbox = detection.get(BBOX)
            if not bbox:
                continue

            # Clip bounding box to image boundaries
            clipped_bbox = self._clip_bbox(bbox, w, h)
            if clipped_bbox is None:
                continue

            # Extract crop with padding
            crop = self._extract_crop_with_padding(image, clipped_bbox, self.padding_ratio)
            if crop is None:
                continue

            # Generate embedding using the unified adapter
            embedding = self.wrapper.embed_crop(crop)
            if embedding is None:
                continue

            # Identify using CacheService (compares against external/local database)
            identity_id = self.cache_service.identify(embedding)

            # Show error/log for unknown identities as requested
            if identity_id == "unknown":
                print(f"✗ [Recognition] Unknown identity for Track {detection.get('track_id')}")

            # Attach result to detection
            detection["identity"] = identity_id
            detection["is_known"] = identity_id != "unknown"
            detection["embedding_dim"] = self.dimension
            detection[MODEL_ID] = self.model_id

            # For debugging/registration purposes, we include the raw embedding
            detection["embedding"] = embedding

            # The display should say 'unknown' correctly
            detection[CLASS_NAME] = identity_id

        return prev_results

    @property
    def names(self) -> dict[int, str]:
        """Embedding stage doesn't define own class labels."""
        return {}
