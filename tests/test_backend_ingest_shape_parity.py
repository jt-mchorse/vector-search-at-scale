"""Every backend's `ingest` must reject a length mismatch the same way (#131).

`StubBackend` and `HnswSimBackend` both opened `ingest` with a length check
raising `ValueError("ingest mismatch: N vectors but M ids")`. The three
real-engine adapters — `pgvector`, `qdrant`, `weaviate` — did not, and all three
build their payload with `for i in range(vectors.shape[0])`. Measured across all
five:

    case                       stub        hnsw-sim    pgvector    qdrant      weaviate
    3 vectors, 3 ids (control) indexed 3   indexed 3   indexed 3   indexed 3   indexed 3
    4 ids for 3 vectors        ValueError  ValueError  indexed 3   indexed 3   indexed 3
    2 ids for 3 vectors        ValueError  ValueError  IndexError  IndexError  IndexError
    0 ids for 3 vectors        ValueError  ValueError  IndexError  IndexError  IndexError

The **surplus** row is the one that matters. All three real engines returned
*normally* having indexed three rows, and the harness then scores recall over
the four ids it believes it ingested — the fourth retrievable by no query,
because it was never stored. Recall is silently deflated and the published
benchmark number is wrong.

That is the harm this repo fixed three times on the *other* method. From
`tests/test_qdrant_backend.py`: "a contract violation that deflates recall with
no diagnostic." `#63/#64` and `#69/#70` hardened `WeaviateBackend.query`; `#79`
carried both to `QdrantBackend.query`. All three were on `query`; `ingest` was
never enumerated.

Driven through every adapter with the `object.__new__` + fake-client pattern
`test_qdrant_backend.py` established, so no SDK and no live engine is needed.
This is also the first test to exercise `PgVectorBackend.ingest` at all — it is
the one adapter with no test file, deliberately skipped for the `query` contract
(its schema makes that unviolatable) by reasoning that does not extend here.
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest

from vector_bench.backends.hnsw_sim import HnswSimBackend
from vector_bench.backends.pgvector import PgVectorBackend
from vector_bench.backends.qdrant import QdrantBackend
from vector_bench.backends.stub import StubBackend
from vector_bench.backends.weaviate import WeaviateBackend


# --------------------------------------------------------------------------
# Fakes. Each records the ids that actually reached the "engine", so the
# control row asserts the vectors were really stored rather than merely that
# no exception was raised.
# --------------------------------------------------------------------------
class _FakeQdrantClient:
    def __init__(self) -> None:
        self.points: list[Any] | None = None

    def recreate_collection(self, **_kw: Any) -> None: ...

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.points = points


class _FakeQModels:
    class VectorParams:
        def __init__(self, **_kw: Any) -> None: ...

    class Distance:
        COSINE = "cosine"

    class HnswConfigDiff:
        def __init__(self, **_kw: Any) -> None: ...

    class PointStruct:
        def __init__(self, id: Any, vector: Any, payload: Any) -> None:
            self.id, self.vector, self.payload = id, vector, payload


class _FakeBatch:
    def __init__(self, sink: list[str]) -> None:
        self.sink = sink

    def __enter__(self) -> _FakeBatch:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False

    def add_object(self, properties: dict[str, Any], vector: Any) -> None:
        self.sink.append(properties["orig_id"])


class _FakeCursor:
    def __init__(self, sink: list[str]) -> None:
        self.sink = sink

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False

    def execute(self, *_a: Any, **_kw: Any) -> None: ...

    def executemany(self, _sql: str, params: list[tuple[Any, ...]]) -> None:
        self.sink.extend(p[0] for p in params)


class _FakePgConn:
    def __init__(self, sink: list[str]) -> None:
        self.sink = sink

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.sink)

    def commit(self) -> None: ...


def _build(name: str):
    """Return `(backend, count_indexed)` — the callable reports rows stored."""
    if name == "stub":
        b = StubBackend()
        return b, lambda: len(b._ids)
    if name == "hnsw-sim":
        b = HnswSimBackend()
        return b, lambda: len(b._ids)
    if name == "qdrant":
        b = object.__new__(QdrantBackend)
        b._client = _FakeQdrantClient()
        b._qmodels = _FakeQModels
        b._collection = "c"
        b._hnsw_m, b._hnsw_ef_construct, b._hnsw_ef = 16, 100, 64
        return b, lambda: len(b._client.points or [])
    if name == "weaviate":
        sink: list[str] = []
        b = object.__new__(WeaviateBackend)
        b._client = types.SimpleNamespace(
            collections=types.SimpleNamespace(
                exists=lambda _n: False,
                delete=lambda _n: None,
                create=lambda **_kw: None,
                get=lambda _n: types.SimpleNamespace(
                    batch=types.SimpleNamespace(dynamic=lambda: _FakeBatch(sink))
                ),
            )
        )
        b._wvcc = types.SimpleNamespace(
            Configure=types.SimpleNamespace(
                Vectorizer=types.SimpleNamespace(none=lambda: None),
                VectorIndex=types.SimpleNamespace(hnsw=lambda **_kw: None),
            ),
            VectorDistances=types.SimpleNamespace(COSINE="cosine"),
            Property=lambda **_kw: None,
            DataType=types.SimpleNamespace(TEXT="text"),
        )
        b._collection_name = "C"
        b._hnsw_max_connections, b._hnsw_ef_construction, b._hnsw_ef = 16, 100, 64
        return b, lambda: len(sink)
    if name == "pgvector":
        sink = []
        b = object.__new__(PgVectorBackend)
        b._conn = _FakePgConn(sink)
        b._conninfo = "postgres:///fake"
        b._psycopg = None
        b._index_method = "hnsw"
        b._hnsw_m, b._hnsw_ef_construction, b._hnsw_ef_search = 16, 64, 40
        b._dim = None
        return b, lambda: len(sink)
    raise AssertionError(name)


BACKENDS = ["stub", "hnsw-sim", "pgvector", "qdrant", "weaviate"]
VECTORS = np.random.default_rng(0).normal(size=(3, 4)).astype(np.float32)

# (label, ids) — every one is a mismatch against VECTORS' three rows.
MISMATCHES = [
    ("surplus: 4 ids for 3 vectors", ["a", "b", "c", "d"]),
    ("deficit: 2 ids for 3 vectors", ["a", "b"]),
    ("empty: 0 ids for 3 vectors", []),
]


def test_the_table_covers_every_backend_and_both_directions() -> None:
    """Anti-vacuous: all five adapters, and both a surplus and a deficit. The
    surplus is the silent case; a deficit-only table would miss it entirely."""
    assert len(BACKENDS) == 5
    assert any(len(ids) > VECTORS.shape[0] for _, ids in MISMATCHES)
    assert any(0 < len(ids) < VECTORS.shape[0] for _, ids in MISMATCHES)
    assert any(len(ids) == 0 for _, ids in MISMATCHES)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_a_matched_pair_still_ingests_every_vector(backend_name: str) -> None:
    """The control. Without it, a guard that rejected everything would satisfy
    every mismatch case below — and it asserts rows were really stored, not
    merely that nothing raised."""
    backend, indexed = _build(backend_name)
    backend.ingest(VECTORS, ["a", "b", "c"])
    assert indexed() == 3


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(("label", "ids"), MISMATCHES, ids=[m[0] for m in MISMATCHES])
def test_every_backend_rejects_a_mismatch(backend_name: str, label: str, ids: list[str]) -> None:
    backend, _ = _build(backend_name)
    with pytest.raises(ValueError, match="ingest mismatch"):
        backend.ingest(VECTORS, ids)


@pytest.mark.parametrize(("label", "ids"), MISMATCHES, ids=[m[0] for m in MISMATCHES])
def test_every_backend_produces_the_same_message(label: str, ids: list[str]) -> None:
    """Differential: not just 'each raises', but 'all five say the same thing'.
    Two of them already had this message; the other three now join it rather
    than inventing three more."""
    messages = set()
    for name in BACKENDS:
        backend, _ = _build(name)
        with pytest.raises(ValueError, match="ingest mismatch") as exc:
            backend.ingest(VECTORS, ids)
        messages.add(str(exc.value))
    assert len(messages) == 1, messages
    assert f"{VECTORS.shape[0]} vectors but {len(ids)} ids" in messages.pop()


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_the_guard_fires_before_the_engine_is_touched(backend_name: str) -> None:
    """A surplus used to return normally having indexed three rows — the silent
    case. Nothing may reach the engine on a mismatch, or a partially-populated
    index would still deflate recall."""
    backend, indexed = _build(backend_name)
    with pytest.raises(ValueError, match="ingest mismatch"):
        backend.ingest(VECTORS, ["a", "b", "c", "d"])
    assert indexed() == 0


def test_the_exception_type_is_valueerror_not_backenderror() -> None:
    """`BackendError`'s own docstring: 'Distinct from `ValueError` (which is for
    caller mistakes)'. A length mismatch is a caller mistake, caught before any
    engine is touched — and keeping `ValueError` leaves the two backends that
    already worked byte-for-byte unchanged."""
    from vector_bench.types import BackendError

    backend, _ = _build("qdrant")
    with pytest.raises(ValueError, match="ingest mismatch") as exc:
        backend.ingest(VECTORS, ["a"])
    assert not isinstance(exc.value, BackendError)
