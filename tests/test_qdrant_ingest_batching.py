"""`QdrantBackend.ingest` must split the corpus across requests (#203).

It sent the whole corpus in one `upsert`. Qdrant's REST service refuses a
request body over `service.max_request_size_mb` (32 MiB by default, and the
Terraform module runs the pinned image with that default). Measured against a
real Qdrant 1.14.0 with qdrant-client 1.19.1:

    --n 2000 --dim 768   exit 0, recall 1.0
    --n 8000 --dim 768   exit 1, server log "PUT .../points?wait=true" 400,
                         body "JSON payload (...) is larger than allowed
                         (limit: 33554432 bytes)"

So every documented `--n 1000000 --dim 768` Qdrant run died at ingest.

Driven with the `object.__new__` + fake-client pattern from
`test_qdrant_backend.py`. The fake records each request's points, and the body
arm serializes them the way the REST client does, so it checks the server's
actual constraint rather than an internal constant.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from vector_bench.backends import qdrant as qdrant_mod
from vector_bench.backends.qdrant import QdrantBackend

# Qdrant's default `service.max_request_size_mb`, in bytes.
_QDRANT_DEFAULT_BODY_LIMIT = 32 * 1024 * 1024


class _PointStruct:
    def __init__(self, id: Any, vector: Any, payload: Any) -> None:
        self.id, self.vector, self.payload = id, vector, payload


class _QModels:
    class VectorParams:
        def __init__(self, **_kw: Any) -> None: ...

    class Distance:
        COSINE = "cosine"

    class HnswConfigDiff:
        def __init__(self, **_kw: Any) -> None: ...

    PointStruct = _PointStruct


class _RecordingClient:
    """Keeps every `upsert` request separately, in call order."""

    def __init__(self) -> None:
        self.requests: list[list[_PointStruct]] = []

    def recreate_collection(self, **_kw: Any) -> None: ...

    def upsert(self, collection_name: str, points: list[_PointStruct]) -> None:
        self.requests.append(list(points))


def _ingest(n: int, dim: int) -> _RecordingClient:
    b = object.__new__(QdrantBackend)  # bypass the live-connection __init__
    client = _RecordingClient()
    b._client = client  # type: ignore[attr-defined]
    b._qmodels = _QModels()  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_m = 16  # type: ignore[attr-defined]
    b._hnsw_ef_construct = 64  # type: ignore[attr-defined]
    rng = np.random.default_rng(0)
    vectors = rng.standard_normal((n, dim), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    ids = [f"c{i:08d}" for i in range(n)]
    b.ingest(vectors, ids)
    return client


def _body_bytes(points: list[_PointStruct]) -> int:
    body = {"points": [{"id": p.id, "vector": p.vector, "payload": p.payload} for p in points]}
    return len(json.dumps(body).encode())


def test_every_request_body_fits_under_qdrants_default_limit() -> None:
    # 3,000 x 768 is ~49 MB as one body (measured: 2,000 x 768 is already 32.5 MB).
    client = _ingest(3000, 768)
    sizes = [_body_bytes(r) for r in client.requests]
    assert sum(sizes) > _QDRANT_DEFAULT_BODY_LIMIT, "fixture too small to exercise the limit"
    assert max(sizes) < _QDRANT_DEFAULT_BODY_LIMIT, sizes


def test_every_point_is_upserted_exactly_once_in_order() -> None:
    client = _ingest(3000, 768)
    assert len(client.requests) > 1
    sent = [p.id for r in client.requests for p in r]
    assert sent == list(range(3000))
    orig = [p.payload["orig_id"] for r in client.requests for p in r]
    assert orig == [f"c{i:08d}" for i in range(3000)]


def test_batch_size_scales_down_with_dimension() -> None:
    budget = qdrant_mod.UPSERT_COMPONENTS_PER_REQUEST
    for r in _ingest(3000, 768).requests:
        assert len(r) * 768 <= budget
    # A dimension above the budget still sends one point per request, never zero.
    wide = _ingest(3, budget + 1)
    assert [len(r) for r in wide.requests] == [1, 1, 1]


def test_a_small_corpus_is_still_one_request() -> None:
    client = _ingest(3, 8)
    assert [len(r) for r in client.requests] == [3]
