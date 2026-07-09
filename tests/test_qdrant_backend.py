"""Tests for `QdrantBackend.query`'s `(id, score)` contract guards (#79).

Symmetric to `test_weaviate_backend.py`: the adapter's constructor opens a
live connection, so we build the instance via `object.__new__` and inject a
fake client whose `search(...)` returns canned `ScoredPoint`-like objects.
This exercises the two guards that `WeaviateBackend.query` received in
#69/#70 (non-string `orig_id`) and #63/#64 (missing metric) but that
`QdrantBackend.query` was missing: qdrant's old `r.payload["orig_id"]`
read-through only raised on a truly *absent* key, so a present-but-None /
non-string payload value silently produced a `(None, score)` / `(123, score)`
hit — a contract violation that deflates recall with no diagnostic.

pgvector is intentionally not tested for this: its `id TEXT PRIMARY KEY`
schema and computed `1 - (embedding <=> v)` similarity are never NULL, so the
contract can't be violated there.
"""

from __future__ import annotations

import numpy as np
import pytest

from vector_bench.backends.qdrant import QdrantBackend
from vector_bench.types import BackendError


class _Pt:
    """A stand-in for qdrant-client's `ScoredPoint` (has `.payload`, `.score`)."""

    def __init__(self, payload: dict[str, object] | None, score: float | None) -> None:
        self.payload = payload
        self.score = score


class _Client:
    def __init__(self, points: list[_Pt]) -> None:
        self._points = points

    def search(self, **_kwargs: object) -> list[_Pt]:
        return self._points


class _QModels:
    class SearchParams:  # noqa: D106 - trivial stand-in
        def __init__(self, **_kwargs: object) -> None:
            pass


def _backend_with(points: list[_Pt]) -> QdrantBackend:
    b = object.__new__(QdrantBackend)  # bypass the live-connection __init__
    b._client = _Client(points)  # type: ignore[attr-defined]
    b._qmodels = _QModels()  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_ef = 40  # type: ignore[attr-defined]
    return b


def test_query_maps_id_and_score_and_preserves_order():
    backend = _backend_with([_Pt({"orig_id": "a"}, 0.8), _Pt({"orig_id": "c"}, 0.95)])
    out = backend.query(np.zeros(4, dtype=np.float32), k=2)
    assert out == [("a", pytest.approx(0.8)), ("c", pytest.approx(0.95))]


def test_query_raises_on_none_orig_id_instead_of_returning_none_id():
    # #79 (sibling of weaviate #69): a present-but-None `orig_id` must not
    # silently become a `(None, score)` hit. Inverse safety net: pre-fix this
    # returned [(None, 0.9)].
    backend = _backend_with([_Pt({"orig_id": None}, 0.9)])
    with pytest.raises(BackendError, match="no string 'orig_id'"):
        backend.query(np.zeros(4, dtype=np.float32), k=1)


def test_query_raises_on_non_string_orig_id():
    # An out-of-band int id must not pass through as `(123, score)`.
    backend = _backend_with([_Pt({"orig_id": 123}, 0.8)])
    with pytest.raises(BackendError, match="no string 'orig_id'"):
        backend.query(np.zeros(4, dtype=np.float32), k=1)


def test_query_raises_on_missing_orig_id_key():
    # A payload with no `orig_id` key at all (or a None payload).
    backend = _backend_with([_Pt({}, 0.8), _Pt(None, 0.7)])
    with pytest.raises(BackendError, match="no string 'orig_id'"):
        backend.query(np.zeros(4, dtype=np.float32), k=2)


def test_query_raises_on_none_score_instead_of_raw_typeerror():
    # #79 (sibling of weaviate #63): a None score must fail as a diagnostic
    # BackendError, not `float(None)`'s bare TypeError. The orig_id guard runs
    # first so the message references a real id.
    backend = _backend_with([_Pt({"orig_id": "doc-1"}, None)])
    with pytest.raises(BackendError, match="no score for point 'doc-1'"):
        backend.query(np.zeros(4, dtype=np.float32), k=1)


def test_query_well_formed_results_unchanged_after_guards():
    # Over-rejection guard: the guards must not perturb the happy path.
    backend = _backend_with([_Pt({"orig_id": "a"}, 0.8), _Pt({"orig_id": "c"}, 0.95)])
    out = backend.query(np.zeros(4, dtype=np.float32), k=2)
    assert [hit_id for hit_id, _ in out] == ["a", "c"]
