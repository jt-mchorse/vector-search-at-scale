"""Qdrant adapter.

Uses `qdrant-client` against a self-hosted Qdrant instance (see
`terraform/modules/qdrant/user_data.sh`). The adapter creates the
collection on first ingest if it doesn't exist, and recreates it on
each fresh run for a clean baseline.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Sequence

import numpy as np

from vector_bench.types import BackendError, check_ingest_shape, check_open

DEFAULT_COLLECTION = "vector_bench"

#: Vector components per `upsert` request (#203). Qdrant's REST service refuses
#: a request body over `service.max_request_size_mb`, 32 MiB by default, and the
#: Terraform module runs the image with that default. A component serializes to
#: about 20 bytes of JSON, so a point at `--dim 768` is ~15 KiB and one request
#: holding the whole corpus failed above ~2,000 vectors -- every documented
#: `--n 1000000 --dim 768` run died at ingest. 262,144 components is ~5 MiB per
#: request: a sixth of the limit, with room for the payload and id per point.
UPSERT_COMPONENTS_PER_REQUEST = 262_144

#: Qdrant leaves a segment as a plain, brute-force store until it is larger than
#: `indexing_threshold` KB (default 20,000), and splits a collection into about
#: one segment per CPU. Below ~50k vectors at dim 768 no HNSW index was ever
#: built, so every query was an exact scan and `hnsw_ef` changed nothing
#: (#205). 1 KB indexes every non-trivial segment. Not 0: that value *disables*
#: indexing.
INDEXING_THRESHOLD_KB = 1

#: How long `ingest` waits for Qdrant's background optimizer to finish building
#: the index before giving up with `BackendError` (#205).
INDEX_WAIT_TIMEOUT_S = 3600.0
INDEX_POLL_INTERVAL_S = 0.2


class QdrantBackend:
    name = "qdrant"

    # Class-level, not an instance field: it must be present on an instance
    # built by `object.__new__` too -- that is how this repo's adapter tests
    # drive the SDK-backed backends without a live engine. Also keeps it out
    # of the dataclass field list, so it is not a constructor argument (#133).
    _closed = False

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
        check_open(self._closed, backend="QdrantBackend", method="ingest")
        check_ingest_shape(vectors, ids)
        q = self._qmodels
        dim = int(vectors.shape[1])
        self._client.recreate_collection(
            collection_name=self._collection,
            vectors_config=q.VectorParams(size=dim, distance=q.Distance.COSINE),
            hnsw_config=q.HnswConfigDiff(m=self._hnsw_m, ef_construct=self._hnsw_ef_construct),
            optimizers_config=q.OptimizersConfigDiff(indexing_threshold=INDEXING_THRESHOLD_KB),
        )
        # Several requests, each under the server's body limit (#203). The
        # batch is sized from the dimension, so a high-dim corpus gets fewer
        # points per request rather than a larger body.
        n = int(vectors.shape[0])
        per_request = max(1, UPSERT_COMPONENTS_PER_REQUEST // max(1, dim))
        for start in range(0, n, per_request):
            points = [
                q.PointStruct(id=i, vector=vectors[i].tolist(), payload={"orig_id": ids[i]})
                for i in range(start, min(start + per_request, n))
            ]
            self._client.upsert(collection_name=self._collection, points=points)
        self._wait_until_indexed()

    def _wait_until_indexed(self) -> None:
        """Return once Qdrant has finished building the HNSW index (#205).

        `upsert(wait=True)` returns when the points are written, not when they
        are indexed: the index is built afterwards by a background optimizer.
        Queries issued in that window hit a mix of indexed and brute-force
        segments while the optimizer competes for CPU. Measured at n=100,000:
        status yellow with 74k of 100k vectors indexed, and recall 0.618 against
        0.472 once the index was complete. GREEN alone is not enough, because
        the optimizer may not have started yet when the first poll runs. So we
        also wait until every point is indexed, which `INDEXING_THRESHOLD_KB`
        makes reachable at any size. This also puts the index build inside
        `ingest_seconds`, as it already is for pgvector and Weaviate.
        """
        q = self._qmodels
        deadline = time.monotonic() + INDEX_WAIT_TIMEOUT_S
        while True:
            info = self._client.get_collection(collection_name=self._collection)
            if info.status == q.CollectionStatus.RED:
                raise BackendError(f"qdrant collection {self._collection!r} is RED after ingest")
            points = info.points_count or 0
            indexed = info.indexed_vectors_count or 0
            if info.status == q.CollectionStatus.GREEN and indexed >= points:
                return
            if time.monotonic() >= deadline:
                raise BackendError(
                    f"qdrant collection {self._collection!r} was not fully indexed within "
                    f"{INDEX_WAIT_TIMEOUT_S:g}s (status {info.status}, {indexed} of "
                    f"{points} vectors indexed)"
                )
            time.sleep(INDEX_POLL_INTERVAL_S)

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        check_open(self._closed, backend="QdrantBackend", method="query")
        q = self._qmodels
        # `query_points`, not `search` (#193). qdrant-client 1.19 removed
        # `QdrantClient.search`, and `qdrant-client>=1.10` installs 1.19 today,
        # so every query died with a raw `AttributeError` before returning a
        # hit. `query_points` (the Universal Query API) exists from 1.10, the
        # floor this extra pins, and returns the same scored points under
        # `.points`.
        response = self._client.query_points(
            collection_name=self._collection,
            query=vector.tolist(),
            limit=k,
            search_params=q.SearchParams(hnsw_ef=self._hnsw_ef),
            with_payload=True,
        )
        results = response.points
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
        # Set last, so the flag is only raised once teardown has actually run
        # (#133). `close()` stays idempotent: a second call re-runs the
        # already-safe teardown above and re-sets a flag that is already True.
        self._closed = True
