"""Qdrant adapter.

Uses `qdrant-client` against a self-hosted Qdrant instance (see
`terraform/modules/qdrant/user_data.sh`). The adapter creates the
collection on first ingest if it doesn't exist, and recreates it on
each fresh run for a clean baseline.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence

import numpy as np

from vector_bench.types import BackendError, check_ingest_shape

DEFAULT_COLLECTION = "vector_bench"


class QdrantBackend:
    name = "qdrant"

    def __init__(
        self,
        *,
        url: str | None = None,
        collection: str = DEFAULT_COLLECTION,
        hnsw_m: int = 16,
        hnsw_ef_construct: int = 64,
        hnsw_ef: int = 40,
    ) -> None:
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.http import models as qmodels  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise BackendError(
                "QdrantBackend requires the `qdrant` extra: pip install 'vector-bench[qdrant]'"
            ) from e
        self._qmodels = qmodels
        url = url or os.environ.get("QDRANT_URL")
        if not url:
            raise BackendError("QdrantBackend: pass url or set QDRANT_URL")
        self._client = QdrantClient(url=url)
        self._collection = collection
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construct = hnsw_ef_construct
        self._hnsw_ef = hnsw_ef

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        check_ingest_shape(vectors, ids)
        q = self._qmodels
        dim = int(vectors.shape[1])
        self._client.recreate_collection(
            collection_name=self._collection,
            vectors_config=q.VectorParams(size=dim, distance=q.Distance.COSINE),
            hnsw_config=q.HnswConfigDiff(m=self._hnsw_m, ef_construct=self._hnsw_ef_construct),
        )
        points = [
            q.PointStruct(id=i, vector=vectors[i].tolist(), payload={"orig_id": ids[i]})
            for i in range(vectors.shape[0])
        ]
        self._client.upsert(collection_name=self._collection, points=points)

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        q = self._qmodels
        results = self._client.search(
            collection_name=self._collection,
            query_vector=vector.tolist(),
            limit=k,
            search_params=q.SearchParams(hnsw_ef=self._hnsw_ef),
        )
        out: list[tuple[str, float]] = []
        for r in results:
            payload = r.payload or {}
            orig_id = payload.get("orig_id")
            # Same (id, score) contract guards WeaviateBackend.query got in
            # #69/#70 (orig_id) and #63/#64 (metric). The old read-through
            # `r.payload["orig_id"]` only raised on a truly *absent* key; a
            # present-but-None / non-string value (out-of-band ingest, schema
            # drift) passed straight through as `(None, score)` / `(123, score)`,
            # silently violating this method's `list[tuple[str, float]]` contract
            # and deflating recall with no diagnostic. Checked before the score
            # guard so that error's `{orig_id!r}` always references a real id (#69).
            if not isinstance(orig_id, str):
                raise BackendError(
                    f"qdrant returned a point with no string 'orig_id' payload "
                    f"(got {orig_id!r}); the result violates the (id, score) contract"
                )
            # `float(None)` would otherwise raise a bare TypeError instead of a
            # backend-native diagnostic; mirror weaviate's missing-metric guard.
            if r.score is None:
                raise BackendError(
                    f"qdrant returned no score for point {orig_id!r}; "
                    "the result violates the (id, score) contract"
                )
            out.append((orig_id, float(r.score)))
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()
