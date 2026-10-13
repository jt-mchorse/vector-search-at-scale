"""Queries traverse the HNSW index `ingest` waits for, at any segment size (#219).

#205 made Qdrant *build* the index at any size (`indexing_threshold`), but the
query planner still answers an unfiltered search by plain scan while the
collection holds fewer than `hnsw_config.full_scan_threshold` KB of vectors --
10,000 KB by default, ~40,000 vectors at dim 64. Read from Qdrant's own
per-segment counters (`GET /telemetry`, `vector_index_searches[].unfiltered_plain`
/ `unfiltered_hnsw`) on a real Qdrant 1.14.0, hnsw_ef=4:

    n x dim      full_scan_threshold   segments     search path        recall@10
    20,000 x 64  default (10,000)      3 `indexed`  unfiltered_plain   1.000
    20,000 x 64  10                    4 `indexed`  unfiltered_hnsw    0.556
    60,000 x 64  default (15,000 KB)   7 `indexed`  unfiltered_hnsw    0.459

Qdrant 1.19.2 gave the same split (1.000 plain, 0.567 hnsw). The in-process
client (`QdrantClient(":memory:")`) has no planner and reports the default
whatever it is given, so these arms check what the adapter *sends*: the value,
and that the installed SDK's `HnswConfigDiff` (which forbids unknown fields)
accepts it under that name.
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest

from vector_bench.backends import qdrant as qdrant_mod
from vector_bench.backends.qdrant import QdrantBackend

# Qdrant refuses anything lower: "full_scan_threshold: value 1 invalid, must be
# 10 or larger" (1.14.0 and 1.19.2).
_SERVER_MINIMUM_KB = 10

# 32 x 16 float32 = 2 KiB, over the 1 KiB refusal floor (#209).
_VECTORS = np.random.default_rng(219).standard_normal((32, 16)).astype(np.float32)
_IDS = [f"d{i}" for i in range(32)]


class _Recording:
    def __init__(self) -> None:
        self.create_kw: dict[str, Any] = {}
        self.n = 0

    def collection_exists(self, collection_name: str) -> bool:
        return False

    def create_collection(self, **kw: Any) -> None:
        self.create_kw = kw

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.n += len(points)

    def get_collection(self, collection_name: str) -> Any:
        return types.SimpleNamespace(
            status="green", points_count=self.n, indexed_vectors_count=self.n
        )


class _DictModels:
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


def _backend(client: Any, qmodels: Any) -> QdrantBackend:
    b = object.__new__(QdrantBackend)  # bypass the live-connection __init__
    b._client = client  # type: ignore[attr-defined]
    b._qmodels = qmodels  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_m = 16  # type: ignore[attr-defined]
    b._hnsw_ef_construct = 64  # type: ignore[attr-defined]
    return b


def test_the_collection_is_created_with_full_scan_at_the_server_minimum() -> None:
    client = _Recording()
    _backend(client, _DictModels()).ingest(_VECTORS, _IDS)
    hnsw = client.create_kw["hnsw_config"]
    assert hnsw.get("full_scan_threshold") is not None, (
        "Qdrant's 10,000 KB default scans any collection under ~40k vectors at dim 64"
    )
    # Below 10 the server refuses the collection; above it, a collection of that
    # size is scanned rather than searched through the index.
    assert hnsw["full_scan_threshold"] == _SERVER_MINIMUM_KB
    assert qdrant_mod.FULL_SCAN_THRESHOLD_KB == _SERVER_MINIMUM_KB
    # The knobs the grid sweeps still reach the same config object.
    assert (hnsw["m"], hnsw["ef_construct"]) == (16, 64)


def test_the_installed_sdk_accepts_the_field_and_sends_it() -> None:
    pytest.importorskip("qdrant_client")
    from qdrant_client.http import models as qmodels

    client = _Recording()
    _backend(client, qmodels).ingest(_VECTORS, _IDS)
    sent = client.create_kw["hnsw_config"].model_dump(exclude_none=True)
    assert sent == {"m": 16, "ef_construct": 64, "full_scan_threshold": _SERVER_MINIMUM_KB}
