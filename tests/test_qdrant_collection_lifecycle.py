"""`QdrantBackend.ingest` resets the collection without `recreate_collection` (#211).

qdrant-client 1.19, which the `qdrant` extra's `>=1.10` floor installs today,
deprecates `recreate_collection` "and will remove it in the future". `search`
went that way before (#193), and nothing saw it coming, because every adapter
test drives a fake that implements whatever method the adapter calls.

So there are two halves. The recording fake pins the order: check, delete only
what exists, create, then upsert. The real-client half runs `ingest` and `query`
against qdrant-client's own in-process engine (`QdrantClient(":memory:")`) with
DeprecationWarning raised as an error. A call the installed client deprecates or
removes therefore fails here, not at the first live run. That half is skipped
when the extra isn't installed, and CI installs it for this test.
"""

from __future__ import annotations

import types
import warnings
from typing import Any

import numpy as np
import pytest

from vector_bench.backends.qdrant import QdrantBackend


class _QModels:
    class Distance:
        COSINE = "Cosine"

    class CollectionStatus:
        GREEN = "green"
        RED = "red"

    @staticmethod
    def VectorParams(**kw: Any) -> dict[str, Any]:  # noqa: N802 - mirrors the SDK
        return kw

    @staticmethod
    def HnswConfigDiff(**kw: Any) -> dict[str, Any]:  # noqa: N802
        return kw

    @staticmethod
    def OptimizersConfigDiff(**kw: Any) -> dict[str, Any]:  # noqa: N802
        return kw

    @staticmethod
    def PointStruct(**kw: Any) -> dict[str, Any]:  # noqa: N802
        return kw


class _Recording:
    def __init__(self, *, exists: bool) -> None:
        self.exists = exists
        self.calls: list[str] = []
        self.n = 0

    def collection_exists(self, collection_name: str) -> bool:
        self.calls.append("exists")
        return self.exists

    def delete_collection(self, collection_name: str) -> None:
        self.calls.append("delete")

    def create_collection(self, **_kw: Any) -> None:
        self.calls.append("create")

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.calls.append("upsert")
        self.n += len(points)

    def get_collection(self, collection_name: str) -> Any:
        return types.SimpleNamespace(
            status="green", points_count=self.n, indexed_vectors_count=self.n
        )


def _backend(client: Any, qmodels: Any) -> QdrantBackend:
    b = object.__new__(QdrantBackend)  # bypass the live-connection __init__
    b._client = client  # type: ignore[attr-defined]
    b._qmodels = qmodels  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_m = 16  # type: ignore[attr-defined]
    b._hnsw_ef_construct = 64  # type: ignore[attr-defined]
    b._hnsw_ef = 40  # type: ignore[attr-defined]
    return b


# 32 x 128 float32 = 16 KiB (the 20-row reingest below is exactly 10 KiB), over
# the 10 KiB refusal floor (#209, #220).
_RNG = np.random.default_rng(211)
_VECTORS = _RNG.standard_normal((32, 128)).astype(np.float32)
_IDS = [f"d{i}" for i in range(32)]


@pytest.mark.parametrize(
    ("exists", "expected"),
    [(False, ["exists", "create", "upsert"]), (True, ["exists", "delete", "create", "upsert"])],
)
def test_ingest_deletes_only_an_existing_collection_then_creates(
    exists: bool, expected: list[str]
) -> None:
    client = _Recording(exists=exists)
    _backend(client, _QModels()).ingest(_VECTORS, _IDS)
    assert client.calls == expected


class _IndexedView:
    """A real in-process client whose `get_collection` reports every point indexed.

    The in-process engine never builds an HNSW index, so `indexed_vectors_count`
    stays 0 and `ingest`'s wait (#205) would run out its timeout. Every other call
    goes straight through to the real client.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def get_collection(self, collection_name: str) -> Any:
        info = self._real.get_collection(collection_name=collection_name)
        return info.model_copy(update={"indexed_vectors_count": info.points_count})


def test_ingest_and_query_against_the_installed_client_use_no_deprecated_call() -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client.http import models as qmodels

    real = qdrant_client.QdrantClient(":memory:")
    backend = _backend(_IndexedView(real), qmodels)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        backend.ingest(_VECTORS, _IDS)
        # A second ingest replaces the collection rather than adding to it.
        backend.ingest(_VECTORS[:20], _IDS[:20])
        hits = backend.query(_VECTORS[3], k=5)
    assert real.get_collection(collection_name="vector_bench").points_count == 20
    assert hits[0][0] == "d3"
    assert len(hits) == 5
    backend.close()
