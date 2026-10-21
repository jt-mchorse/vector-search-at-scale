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

from vector_bench.types import BackendError, check_ingest_shape, check_open, unreachable_error

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

#: A built index is not a used one (#219). Qdrant's query planner answers an
#: unfiltered search by plain scan while the collection holds fewer than
#: `hnsw_config.full_scan_threshold` KB of vectors, 10,000 by default: ~40,000
#: vectors at dim 64, so every documented `--dim 64` grid stayed an exact scan at
#: recall 1.0 with every vector `indexed`. Measured on Qdrant 1.14.0 and 1.19.2 at
#: n=20,000 x 64, hnsw_ef=4: recall 1.000 with every search `unfiltered_plain`,
#: against 0.556 / 0.567 `unfiltered_hnsw` here (and n=60,000 x 64, 15,000 KB,
#: already searched the graph at the default). 10 is the smallest value the
#: server accepts.
FULL_SCAN_THRESHOLD_KB = 10

#: The smallest corpus a Qdrant run can measure an HNSW search on: below either
#: threshold above, every query is a brute-force scan (#209, #220).
MIN_CORPUS_KB = max(INDEXING_THRESHOLD_KB, FULL_SCAN_THRESHOLD_KB)

#: How long `ingest` waits for Qdrant's background optimizer to finish building
#: the index before giving up with `BackendError` (#205).
INDEX_WAIT_TIMEOUT_S = 3600.0
INDEX_POLL_INTERVAL_S = 0.2


#: Qdrant's HNSW lower bounds (#222), measured on 1.14.0 and 1.19.2. The server
#: refuses `ef_construct < 4` at `create_collection` (a 422 in the middle of a
#: grid) and 1.19.2 refuses `hnsw_ef < 1` at the first *query*, after the whole
#: ingest and index build; 1.14.0 accepts it and publishes an `efs=0` cell.
#: `m = 0` is accepted but switches the graph off: every segment reports
#: `indexed` and every search is a plain scan, the brute-force run #209 refuses
#: for a tiny corpus. No upper bound: the server took m and ef_construct of
#: 100,000.
HNSW_M_MIN = 1
HNSW_EF_CONSTRUCT_MIN = 4
HNSW_EF_MIN = 1


def validate_hnsw_params(*, m: int, ef_construct: int, ef: int) -> None:
    """Raise ``ValueError`` unless Qdrant would build and search an HNSW graph
    with these knobs.

    Pure, so callers can check a whole grid before connecting to anything --
    the shape pgvector's `validate_hnsw_params` set in #188.
    """
    for name, value, lo in (
        ("hnsw_m", m, HNSW_M_MIN),
        ("hnsw_ef_construct", ef_construct, HNSW_EF_CONSTRUCT_MIN),
        ("hnsw_ef", ef, HNSW_EF_MIN),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < lo:
            raise ValueError(f"qdrant {name} must be an int >= {lo}; got {value!r}")


def validate_corpus(*, n_vectors: int, dim: int) -> None:
    """Raise ``ValueError`` if a corpus of ``n_vectors`` x ``dim`` float32 is
    under `MIN_CORPUS_KB`, where every Qdrant query is a brute-force scan.

    Pure, like `validate_hnsw_params`, so the grid can refuse an under-floor
    corpus before it connects or creates `--out-dir` (#245); `ingest` raises
    the same message as `BackendError`.
    """
    total_bytes = n_vectors * dim * 4
    if total_bytes < MIN_CORPUS_KB * 1024:
        raise ValueError(
            f"qdrant answers every query by brute-force scan for a collection under "
            f"{MIN_CORPUS_KB} KiB of vectors (this one is {total_bytes} B: "
            f"{n_vectors} x {dim} float32): under {INDEXING_THRESHOLD_KB} KiB "
            f"it never builds an HNSW index, and under {FULL_SCAN_THRESHOLD_KB} KiB "
            f"(full_scan_threshold) its query planner does not use it; use at least "
            f"{MIN_CORPUS_KB * 256} vector components (n x dim)"
        )


class QdrantBackend:
    name = "qdrant"

    # Class-level, not an instance field: it must be present on an instance
    # built by `object.__new__` too -- that is how this repo's adapter tests
    # drive the SDK-backed backends without a live engine. Also keeps it out
    # of the dataclass field list, so it is not a constructor argument (#133).
    _closed = False
    # Class-level for the same reason: an `object.__new__` instance wraps
    # nothing, and `except ()` matches no exception (#256).
    _transport_errors: tuple[type[BaseException], ...] = ()
    _target = "?"

    def __init__(
        self,
        *,
        url: str | None = None,
        collection: str = DEFAULT_COLLECTION,
        hnsw_m: int = 16,
        hnsw_ef_construct: int = 64,
        hnsw_ef: int = 40,
    ) -> None:
        # Before the import and the URL check: a bad knob is the caller's error
        # whatever the environment, and the grid relies on this raising early.
        validate_hnsw_params(m=hnsw_m, ef_construct=hnsw_ef_construct, ef=hnsw_ef)
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.http import models as qmodels  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise BackendError(
                "QdrantBackend requires the `qdrant` extra: pip install -e '.[qdrant]' from the vector-search-at-scale checkout"
            ) from e
        from qdrant_client.http.exceptions import (  # type: ignore
            ResponseHandlingException,
            UnexpectedResponse,
        )

        self._qmodels = qmodels
        url = url or os.environ.get("QDRANT_URL")
        if not url:
            raise BackendError("QdrantBackend: pass url or set QDRANT_URL")
        # The client's transport error (#256): qdrant-client wraps every httpx
        # failure -- connection refused, DNS, a timeout -- in this one class, so
        # it is what an unreachable or lost server raises from any call.
        self._transport_errors = (ResponseHandlingException,)
        self._target = url
        self._client = QdrantClient(url=url)
        # `QdrantClient(url=...)` does not connect: an unreachable QDRANT_URL
        # surfaced at the first `ingest` call as the SDK's own traceback at
        # exit 1, after the grid had created `--out-dir` (#256). One cheap call
        # here makes it a setup error, raised from `make_backend` like an unset
        # QDRANT_URL. A non-2xx answer (`UnexpectedResponse`: a URL that reaches
        # some other HTTP server, or an auth failure) is the same misconfigured
        # target, so the probe catches it too; later calls catch transport
        # errors only, leaving a server-side refusal to its own message.
        try:
            self._client.get_collections()
        except (ResponseHandlingException, UnexpectedResponse) as e:
            with contextlib.suppress(Exception):
                self._client.close()
            raise self._unreachable(e) from e
        self._collection = collection
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construct = hnsw_ef_construct
        self._hnsw_ef = hnsw_ef

    def _unreachable(self, e: BaseException) -> BackendError:
        return unreachable_error("QdrantBackend", self._target, e)

    @contextlib.contextmanager
    def _wrap_transport_errors(self):
        """Re-raise the client's transport error as `BackendError` (#256).

        For a server lost after construction: `run`, `load` and the grid
        report a `BackendError` as `error:` at exit 2 (#209), while the raw
        `ResponseHandlingException` escaped as a traceback at exit 1.
        """
        try:
            yield
        except self._transport_errors as e:
            raise self._unreachable(e) from e

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        check_open(self._closed, backend="QdrantBackend", method="ingest")
        check_ingest_shape(vectors, ids)
        with self._wrap_transport_errors():
            self._ingest(vectors, ids)

    def _ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        q = self._qmodels
        dim = int(vectors.shape[1])
        # Below the indexing threshold Qdrant never builds an index (#209):
        # measured on 1.14.0, n=3 x dim=64 (768 B) stayed green with 0 of 3
        # vectors indexed, and `_wait_until_indexed` then slept the whole
        # INDEX_WAIT_TIMEOUT_S. Such a run could only measure a brute-force
        # scan, which is what #205 exists to rule out, so refuse it up front.
        # The floor is the larger of the two size thresholds (#220): under
        # FULL_SCAN_THRESHOLD_KB the index is built but the planner answers
        # every query by plain scan anyway (#219) -- measured on 1.14.0, n=30 x
        # 64 (7,680 B) was fully indexed and searched `unfiltered_plain` at
        # recall 1.000, n=50 x 64 (12,800 B) `unfiltered_hnsw` at 0.962.
        try:
            validate_corpus(n_vectors=int(vectors.shape[0]), dim=dim)
        except ValueError as exc:
            raise BackendError(str(exc)) from exc
        # Delete-then-create, not `recreate_collection` (#211): qdrant-client
        # 1.19 (what the `>=1.10` floor installs today) deprecates it "and will
        # remove it in the future", the path `search` took before #193. The
        # three calls below exist from 1.8. A stale collection from an earlier
        # run is still dropped, so every ingest starts from an empty index.
        if self._client.collection_exists(collection_name=self._collection):
            self._client.delete_collection(collection_name=self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=q.VectorParams(size=dim, distance=q.Distance.COSINE),
            hnsw_config=q.HnswConfigDiff(
                m=self._hnsw_m,
                ef_construct=self._hnsw_ef_construct,
                full_scan_threshold=FULL_SCAN_THRESHOLD_KB,
            ),
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
        with self._wrap_transport_errors():
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
