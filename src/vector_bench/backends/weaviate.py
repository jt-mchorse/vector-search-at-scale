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

from vector_bench.types import BackendError, check_ingest_shape, check_open, unreachable_error

DEFAULT_COLLECTION = "VectorBench"

#: Weaviate's HNSW bounds (#224), measured on 1.34.0 (the terraform pin). The
#: server refuses `maxConnections` outside [4, 2047] and `efConstruction < 4`
#: with a 422 at `collections.create` -- inside `ingest`, so in the grid it was a
#: traceback mid-sweep with earlier cell JSONs on disk. `ef < 1` is accepted and
#: stored, but Weaviate reads it as *dynamic* ef (>= dynamicEfMin, 100): an
#: `efs=0` cell published the recall of an ef=100 search. No upper bound on
#: either ef: the server took 100,000.
HNSW_MAX_CONNECTIONS_RANGE = (4, 2047)
HNSW_EF_CONSTRUCTION_MIN = 4
HNSW_EF_MIN = 1


def validate_hnsw_params(*, max_connections: int, ef_construction: int, ef: int) -> None:
    """Raise ``ValueError`` unless Weaviate would build and search an HNSW graph
    with exactly these knobs.

    Pure, so callers can check a whole grid before connecting to anything --
    the shape pgvector's `validate_hnsw_params` set in #188.
    """
    lo, hi = HNSW_MAX_CONNECTIONS_RANGE
    for name, value, vlo, vhi in (
        ("hnsw_max_connections", max_connections, lo, hi),
        ("hnsw_ef_construction", ef_construction, HNSW_EF_CONSTRUCTION_MIN, None),
        ("hnsw_ef", ef, HNSW_EF_MIN, None),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < vlo
            or (vhi is not None and value > vhi)
        ):
            bound = f"in [{vlo}, {vhi}]" if vhi is not None else f">= {vlo}"
            raise ValueError(f"weaviate {name} must be an int {bound}; got {value!r}")


class WeaviateBackend:
    name = "weaviate"

    # Class-level, not an instance field: it must be present on an instance
    # built by `object.__new__` too -- that is how this repo's adapter tests
    # drive the SDK-backed backends without a live engine. Also keeps it out
    # of the dataclass field list, so it is not a constructor argument (#133).
    _closed = False
    # Class-level for the same reason: an `object.__new__` instance wraps
    # nothing, and `except ()` matches no exception (#256).
    _transport_errors: tuple[type[BaseException], ...] = ()
    _query_errors: tuple[type[BaseException], ...] = ()
    _retry_errors: tuple[type[BaseException], ...] = ()
    _target = "?"

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
        # Before the import and the host check: a bad knob is the caller's error
        # whatever the environment, and the grid relies on this raising early.
        validate_hnsw_params(
            max_connections=hnsw_max_connections,
            ef_construction=hnsw_ef_construction,
            ef=hnsw_ef,
        )
        try:
            import weaviate  # type: ignore
            import weaviate.classes.config as wvcc  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise BackendError(
                "WeaviateBackend requires the `weaviate` extra: pip install -e '.[weaviate]' from the vector-search-at-scale checkout"
            ) from e
        from weaviate.exceptions import (  # type: ignore
            WeaviateConnectionError,
            WeaviateGRPCUnavailableError,
            WeaviateQueryError,
            WeaviateRetryError,
            WeaviateStartUpError,
            WeaviateTimeoutError,
        )

        self._weaviate = weaviate
        self._wvcc = wvcc
        host = host or os.environ.get("WEAVIATE_HOST")
        if not host:
            raise BackendError("WeaviateBackend: pass host or set WEAVIATE_HOST")
        self._target = f"http://{host}:{http_port} (grpc :{grpc_port})"
        # `connect_to_custom` connects and checks the server is ready, so an
        # unreachable WEAVIATE_HOST raised the SDK's own exception here, inside
        # `make_backend`, past the `BackendError` arm `_make_backend_or_none`
        # gives every other setup error: a traceback at exit 1 (#256). These
        # are the classes it raises for a server it cannot reach or that is not
        # up (HTTP refused, gRPC port unavailable, readiness check failed or
        # timed out). The connection and timeout errors are also what a server
        # lost mid-run raises from `ingest` and `query`.
        self._transport_errors = (WeaviateConnectionError, WeaviateTimeoutError)
        # A server lost mid-QUERY raises neither (#264). The gRPC search retries
        # `UNAVAILABLE` with backoff (about 63 s against weaviate-client 4.24),
        # then raises `WeaviateRetryError`, which `grpc_search` re-raises as a
        # `WeaviateQueryError` -- the class a rejected query raises too. The
        # retry error in the chain is what tells them apart.
        self._query_errors = (WeaviateQueryError,)
        self._retry_errors = (WeaviateRetryError,)
        try:
            self._client = weaviate.connect_to_custom(
                http_host=host,
                http_port=http_port,
                http_secure=False,
                grpc_host=host,
                grpc_port=grpc_port,
                grpc_secure=False,
            )
        except (
            WeaviateConnectionError,
            WeaviateGRPCUnavailableError,
            WeaviateStartUpError,
            WeaviateTimeoutError,
        ) as e:
            raise self._unreachable(e) from e
        self._collection_name = collection
        self._hnsw_max_connections = hnsw_max_connections
        self._hnsw_ef_construction = hnsw_ef_construction
        self._hnsw_ef = hnsw_ef

    def _unreachable(self, e: BaseException) -> BackendError:
        return unreachable_error("WeaviateBackend", self._target, e)

    @contextlib.contextmanager
    def _wrap_transport_errors(self):
        """Re-raise the client's connection errors as `BackendError` (#256).

        For a server lost after construction: `run`, `load` and the grid
        report a `BackendError` as `error:` at exit 2 (#209). A query error
        is wrapped only when the client gave up retrying a lost gRPC channel
        (#264); one the server answered keeps its own type.
        """
        try:
            yield
        except self._transport_errors as e:
            raise self._unreachable(e) from e
        except self._query_errors as e:
            if not self._retries_exhausted(e):
                raise
            raise self._unreachable(e) from e

    def _retries_exhausted(self, e: BaseException) -> bool:
        """True when `e` was raised after the client gave up retrying gRPC `UNAVAILABLE` (#264)."""
        seen: set[int] = set()
        link: BaseException | None = e
        while link is not None and id(link) not in seen:
            if isinstance(link, self._retry_errors):
                return True
            seen.add(id(link))
            link = link.__cause__ or link.__context__
        return False

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        check_open(self._closed, backend="WeaviateBackend", method="ingest")
        check_ingest_shape(vectors, ids)
        with self._wrap_transport_errors():
            self._ingest(vectors, ids)

    def _ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        wvcc = self._wvcc
        if self._client.collections.exists(self._collection_name):
            self._client.collections.delete(self._collection_name)
        # `vector_config` with self-provided vectors, not `vectorizer_config` +
        # `vector_index_config` (#215). weaviate-client 4.23 (what an unpinned
        # install gets) deprecates both (Dep024/Dep025), the path that cost
        # Qdrant `search` (#193). `Configure.Vectors` exists from 4.16, the floor.
        self._client.collections.create(
            name=self._collection_name,
            vector_config=wvcc.Configure.Vectors.self_provided(
                vector_index_config=wvcc.Configure.VectorIndex.hnsw(
                    max_connections=self._hnsw_max_connections,
                    ef_construction=self._hnsw_ef_construction,
                    ef=self._hnsw_ef,
                    distance_metric=wvcc.VectorDistances.COSINE,
                ),
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
        # The batch context does not raise when objects fail (#215). The
        # client only logs "Failed to send N objects ... inspect failed_objects"
        # and the block exits normally, so a partly-failed ingest went on to be
        # queried and its recall published.
        failed = coll.batch.failed_objects
        if failed:
            raise BackendError(
                f"weaviate failed to ingest {len(failed)} of {vectors.shape[0]} objects "
                f"into {self._collection_name!r}; first error: {failed[0].message}"
            )

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        check_open(self._closed, backend="WeaviateBackend", method="query")
        coll = self._client.collections.get(self._collection_name)
        with self._wrap_transport_errors():
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
