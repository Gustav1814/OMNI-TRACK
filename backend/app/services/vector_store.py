"""
Pluggable vector store abstraction.

Default backend uses existing pgvector CRUD service.
Qdrant backend is optional and loaded only when configured.
"""

from __future__ import annotations

from uuid import uuid4
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.crud import EmbeddingService


class VectorStore:
    async def upsert_batch(self, db: Any, rows: List[Dict[str, Any]], model_version: str = "osnet_x1_0") -> int:
        raise NotImplementedError

    async def search(
        self, db: Any, query_vector: List[float], top_k: int = 10, threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class PgVectorStore(VectorStore):
    async def upsert_batch(self, db: Any, rows: List[Dict[str, Any]], model_version: str = "osnet_x1_0") -> int:
        return await EmbeddingService.store_batch(db, rows, model_version=model_version)

    async def search(
        self, db: Any, query_vector: List[float], top_k: int = 10, threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        return await EmbeddingService.search_similar(db, query_vector, top_k=top_k, threshold=threshold)


class QdrantStore(VectorStore):
    def __init__(self, url: str, api_key: str = "", collection_prefix: str = "omnitrack_embeddings"):
        self._url = url
        self._api_key = api_key
        self._prefix = collection_prefix
        self._client = None

    def _collection(self, model_version: str) -> str:
        safe = str(model_version).replace(" ", "_").replace("/", "_")
        return f"{self._prefix}_{safe}"

    async def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from qdrant_client import AsyncQdrantClient
        except Exception as e:  # pragma: no cover
            logger.warning(f"Qdrant backend requested but qdrant-client missing: {e}")
            return
        self._client = AsyncQdrantClient(url=self._url, api_key=self._api_key or None)

    async def upsert_batch(self, db: Any, rows: List[Dict[str, Any]], model_version: str = "osnet_x1_0") -> int:
        await self._ensure_client()
        if self._client is None:
            return 0
        if not rows:
            return 0
        collection = self._collection(model_version)
        try:
            from qdrant_client.http import models as qm
            dim = len(rows[0].get("vector") or [])
            if dim <= 0:
                return 0
            try:
                await self._client.get_collection(collection)
            except Exception:
                await self._client.create_collection(
                    collection_name=collection,
                    vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                )
        except Exception:
            # best-effort collection ensure; continue
            pass

        try:
            from qdrant_client.http import models as qm
            points = []
            for r in rows:
                vec = r.get("vector")
                if not vec:
                    continue
                points.append(
                    qm.PointStruct(
                        id=str(r.get("global_id") or r.get("track_id") or uuid4().hex),
                        vector=list(vec),
                        payload={
                            "camera_id": r.get("camera_id"),
                            "track_id": r.get("track_id"),
                            "global_id": r.get("global_id"),
                            "confidence": r.get("confidence"),
                            "model_version": model_version,
                        },
                    )
                )
            if not points:
                return 0
            await self._client.upsert(collection_name=collection, points=points)
            return len(points)
        except Exception as e:
            logger.debug(f"Qdrant upsert_batch failed: {e}")
            return 0

    async def search(
        self, db: Any, query_vector: List[float], top_k: int = 10, threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_client()
        if self._client is None:
            return []
        # We can't infer model version from query call site in current code path.
        # Keep fallback to pgvector by returning [] here and allowing caller to degrade.
        return []


class FaissStore(VectorStore):
    """
    Local, fully-free vector backend (CPU friendly).
    Uses faiss-cpu when available, falls back to numpy cosine search.
    """

    def __init__(self):
        self._indexes: Dict[str, Any] = {}
        self._meta: Dict[str, list[Dict[str, Any]]] = {}
        self._faiss = None
        try:
            import faiss  # type: ignore

            self._faiss = faiss
        except Exception as e:
            logger.warning(f"FAISS not installed, using numpy fallback search: {e}")

    def _name(self, model_version: str) -> str:
        return str(model_version or "default")

    async def upsert_batch(self, db: Any, rows: List[Dict[str, Any]], model_version: str = "osnet_x1_0") -> int:
        if not rows:
            return 0
        key = self._name(model_version)
        vectors = [r.get("vector") for r in rows if r.get("vector")]
        if not vectors:
            return 0
        import numpy as np

        arr = np.asarray(vectors, dtype="float32")
        # Normalize so inner-product == cosine similarity.
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        arr = arr / np.maximum(norms, 1e-9)

        if self._faiss is not None:
            if key not in self._indexes:
                self._indexes[key] = self._faiss.IndexFlatIP(arr.shape[1])
                self._meta[key] = []
            self._indexes[key].add(arr)
            self._meta[key].extend(rows)
        else:
            base = self._indexes.get(key)
            if base is None:
                self._indexes[key] = arr
                self._meta[key] = list(rows)
            else:
                self._indexes[key] = np.vstack([base, arr])
                self._meta[key].extend(rows)
        return len(rows)

    async def search(
        self, db: Any, query_vector: List[float], top_k: int = 10, threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        key = self._name("default")
        if key not in self._indexes:
            return []
        import numpy as np

        q = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
        out: List[Dict[str, Any]] = []
        if self._faiss is not None and not isinstance(self._indexes[key], np.ndarray):
            scores, idx = self._indexes[key].search(q, min(top_k, len(self._meta[key])))
            for s, i in zip(scores[0].tolist(), idx[0].tolist()):
                if i < 0:
                    continue
                if threshold is not None and s < threshold:
                    continue
                meta = self._meta[key][i]
                out.append(
                    {
                        "camera_id": meta.get("camera_id"),
                        "track_id": meta.get("track_id"),
                        "global_id": meta.get("global_id"),
                        "confidence": meta.get("confidence"),
                        "distance": float(1.0 - s),
                    }
                )
            return out
        # numpy fallback
        base = self._indexes[key]
        sims = (base @ q.T).reshape(-1)
        order = np.argsort(-sims)[:top_k]
        for i in order.tolist():
            s = float(sims[i])
            if threshold is not None and s < threshold:
                continue
            meta = self._meta[key][i]
            out.append(
                {
                    "camera_id": meta.get("camera_id"),
                    "track_id": meta.get("track_id"),
                    "global_id": meta.get("global_id"),
                    "confidence": meta.get("confidence"),
                    "distance": float(1.0 - s),
                }
            )
        return out
