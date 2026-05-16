"""Weaviate adapter.

Uses `weaviate-client` v4 against a self-hosted Weaviate instance (see
`terraform/modules/weaviate/user_data.sh`). The adapter recreates the
target collection on each fresh run for a clean baseline.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence

import numpy as np

from vector_bench.types import BackendError

DEFAULT_COLLECTION = "VectorBench"


class WeaviateBackend:
    name = "weaviate"

    def __init__(
        self,
        *,
        host: str | None = None,
        http_port: int = 8080,
        grpc_port: int = 50051,
        collection: str = DEFAULT_COLLECTION,
        hnsw_max_connections: int = 16,
        hnsw_ef_construction: int = 64,
        hnsw_ef: int = 40,
    ) -> None:
        try:
            import weaviate  # type: ignore
            import weaviate.classes.config as wvcc  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise BackendError(
                "WeaviateBackend requires the `weaviate` extra: pip install 'vector-bench[weaviate]'"
            ) from e
        self._weaviate = weaviate
        self._wvcc = wvcc
        host = host or os.environ.get("WEAVIATE_HOST")
        if not host:
            raise BackendError("WeaviateBackend: pass host or set WEAVIATE_HOST")
        self._client = weaviate.connect_to_custom(
            http_host=host,
            http_port=http_port,
            http_secure=False,
            grpc_host=host,
            grpc_port=grpc_port,
            grpc_secure=False,
        )
        self._collection_name = collection
        self._hnsw_max_connections = hnsw_max_connections
        self._hnsw_ef_construction = hnsw_ef_construction
        self._hnsw_ef = hnsw_ef

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        wvcc = self._wvcc
        if self._client.collections.exists(self._collection_name):
            self._client.collections.delete(self._collection_name)
        self._client.collections.create(
            name=self._collection_name,
            vectorizer_config=wvcc.Configure.Vectorizer.none(),
            vector_index_config=wvcc.Configure.VectorIndex.hnsw(
                max_connections=self._hnsw_max_connections,
                ef_construction=self._hnsw_ef_construction,
                ef=self._hnsw_ef,
                distance_metric=wvcc.VectorDistances.COSINE,
            ),
            properties=[wvcc.Property(name="orig_id", data_type=wvcc.DataType.TEXT)],
        )
        coll = self._client.collections.get(self._collection_name)
        with coll.batch.dynamic() as batch:
            for i in range(vectors.shape[0]):
                batch.add_object(
                    properties={"orig_id": ids[i]},
                    vector=vectors[i].tolist(),
                )

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        coll = self._client.collections.get(self._collection_name)
        res = coll.query.near_vector(
            near_vector=vector.tolist(), limit=k, return_metadata=["distance"]
        )
        out: list[tuple[str, float]] = []
        for obj in res.objects:
            orig_id = obj.properties.get("orig_id")
            distance = obj.metadata.distance if obj.metadata is not None else 0.0
            # Weaviate returns cosine *distance*; convert to similarity.
            similarity = 1.0 - float(distance)
            out.append((orig_id, similarity))
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()
