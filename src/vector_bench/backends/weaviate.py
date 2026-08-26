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

from vector_bench.types import BackendError, check_ingest_shape, check_open

DEFAULT_COLLECTION = "VectorBench"


class WeaviateBackend:
    name = "weaviate"

    # Class-level, not an instance field: it must be present on an instance
    # built by `object.__new__` too -- that is how this repo's adapter tests
    # drive the SDK-backed backends without a live engine. Also keeps it out
    # of the dataclass field list, so it is not a constructor argument (#133).
    _closed = False

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
        check_open(self._closed, backend="WeaviateBackend", method="ingest")
        check_ingest_shape(vectors, ids)
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
        check_open(self._closed, backend="WeaviateBackend", method="query")
        coll = self._client.collections.get(self._collection_name)
        res = coll.query.near_vector(
            near_vector=vector.tolist(), limit=k, return_metadata=["distance"]
        )
        out: list[tuple[str, float]] = []
        for obj in res.objects:
            orig_id = obj.properties.get("orig_id")
            # Fail loud on a missing/non-string id, the symmetric twin of the
            # distance-metadata guard below (#63): `.get` yields None when the
            # object has no `orig_id` property, and the old read-through appended
            # `(None, score)` — violating this method's `list[tuple[str, float]]`
            # contract. A None id silently never matches a ground-truth id and
            # deflates recall with no diagnostic. Checked before the distance
            # guard so that error's `{orig_id!r}` always references a real id (#69).
            if not isinstance(orig_id, str):
                raise BackendError(
                    f"weaviate returned an object with no string 'orig_id' property "
                    f"(got {orig_id!r}); the result violates the (id, score) contract"
                )
            if obj.metadata is None or obj.metadata.distance is None:
                # Fail loud instead of inventing a best-case score. The old
                # `else 0.0` fallback made similarity = 1.0 - 0.0 = 1.0 (the
                # maximum), silently ranking a metadata-less result as the top
                # hit and emitting a fabricated benchmark number — which the
                # repo's no-fabricated-numbers invariant forbids and the other
                # three backends never do (#63).
                raise BackendError(
                    f"weaviate returned no distance metadata for object {orig_id!r}; "
                    "ensure the query requests return_metadata=['distance']"
                )
            distance = obj.metadata.distance
            # Weaviate returns cosine *distance*; convert to similarity.
            similarity = 1.0 - float(distance)
            out.append((orig_id, similarity))
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()
        # Set last, so the flag is only raised once teardown has actually run
        # (#133). `close()` stays idempotent: a second call re-runs the
        # already-safe teardown above and re-sets a flag that is already True.
        self._closed = True
