"""Tests for `WeaviateBackend.query` (#63).

The adapter's constructor opens a live gRPC/HTTP connection, so we build the
instance via `object.__new__` and inject a fake client whose
`collections.get(...).query.near_vector(...)` returns canned result objects.
This exercises the distance→similarity conversion and, crucially, the
missing-metadata path that used to fabricate a perfect-similarity score (1.0)
instead of failing loud.
"""

from __future__ import annotations

import numpy as np
import pytest

from vector_bench.backends.weaviate import WeaviateBackend
from vector_bench.types import BackendError


class _Meta:
    def __init__(self, distance: float | None) -> None:
        self.distance = distance


class _Obj:
    def __init__(self, orig_id: str, distance: float | None, *, has_metadata: bool = True) -> None:
        self.properties = {"orig_id": orig_id}
        self.metadata = _Meta(distance) if has_metadata else None


class _Res:
    def __init__(self, objects: list[_Obj]) -> None:
        self.objects = objects


class _Query:
    def __init__(self, objects: list[_Obj]) -> None:
        self._objects = objects

    def near_vector(self, **_kwargs: object) -> _Res:
        return _Res(self._objects)


class _Coll:
    def __init__(self, objects: list[_Obj]) -> None:
        self.query = _Query(objects)


class _Collections:
    def __init__(self, objects: list[_Obj]) -> None:
        self._objects = objects

    def get(self, _name: str) -> _Coll:
        return _Coll(self._objects)


class _Client:
    def __init__(self, objects: list[_Obj]) -> None:
        self.collections = _Collections(objects)


def _backend_with(objects: list[_Obj]) -> WeaviateBackend:
    b = object.__new__(WeaviateBackend)  # bypass the live-connection __init__
    b._client = _Client(objects)  # type: ignore[attr-defined]
    b._collection_name = "C"  # type: ignore[attr-defined]
    return b


def test_query_converts_distance_to_similarity_and_preserves_order():
    backend = _backend_with([_Obj("a", 0.2), _Obj("c", 0.05)])
    out = backend.query(np.zeros(4, dtype=np.float32), k=2)
    assert [hit_id for hit_id, _ in out] == ["a", "c"]
    assert out[0][1] == pytest.approx(0.8)  # 1 - 0.2
    assert out[1][1] == pytest.approx(0.95)  # 1 - 0.05


def test_query_raises_on_missing_metadata_instead_of_fabricating_one():
    # #63: a metadata-less object must not silently become a (id, 1.0) top hit.
    backend = _backend_with([_Obj("a", 0.2), _Obj("b", None, has_metadata=False)])
    with pytest.raises(BackendError, match="no distance metadata"):
        backend.query(np.zeros(4, dtype=np.float32), k=2)


def test_query_raises_on_none_distance_value():
    backend = _backend_with([_Obj("a", None)])
    with pytest.raises(BackendError, match="no distance metadata"):
        backend.query(np.zeros(4, dtype=np.float32), k=1)
