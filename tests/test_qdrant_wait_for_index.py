"""`QdrantBackend.ingest` must return only once the HNSW index is built (#205).

Before this, `ingest` returned when `upsert` did, and the harness started
timing queries right away. Two Qdrant defaults meant those queries were not
measuring the configured index. Measured on a real Qdrant 1.14.0 at dim 768,
reading `get_collection()` right after ingest:

    n        hnsw_ef  status   points / indexed   recall@10
    20,000   4        green    20000 / 0          1.0     (exact scan)
    20,000   40       green    20000 / 0          1.0     (exact scan)
    100,000  128      yellow   103410 / 73656     0.618   (index half built)

With the index forced and the wait in place, the same rows give 0.682, 0.912
and 0.446. Each one is green with every vector indexed.

The fake client below scripts the sequence of `get_collection` answers, so
these tests check the wait condition itself: a mid-build answer must not end
the wait, nor may a GREEN that arrives before any vector is indexed.
"""

from __future__ import annotations

import itertools
import time
import types
from typing import Any

import numpy as np
import pytest

from vector_bench.backends import qdrant as qdrant_mod
from vector_bench.backends.qdrant import QdrantBackend
from vector_bench.types import BackendError


class _QModels:
    class VectorParams:
        def __init__(self, **_kw: Any) -> None: ...

    class Distance:
        COSINE = "cosine"

    class HnswConfigDiff:
        def __init__(self, **_kw: Any) -> None: ...

    class OptimizersConfigDiff:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    class CollectionStatus:
        GREEN, YELLOW, GREY, RED = "green", "yellow", "grey", "red"

    class PointStruct:
        def __init__(self, id: Any, vector: Any, payload: Any) -> None:
            self.id, self.vector, self.payload = id, vector, payload


class _ScriptedClient:
    """Answers `get_collection` from a script; repeats the last answer."""

    def __init__(self, script: list[tuple[str, int, int]]) -> None:
        self.script = script
        self.polls = 0
        self.create_kw: dict[str, Any] = {}
        self.events: list[str] = []

    def recreate_collection(self, **kw: Any) -> None:
        self.create_kw = kw

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.events.append("upsert")

    def get_collection(self, collection_name: str) -> Any:
        status, points, indexed = self.script[min(self.polls, len(self.script) - 1)]
        self.polls += 1
        self.events.append(f"poll:{status}:{indexed}/{points}")
        return types.SimpleNamespace(
            status=status, points_count=points, indexed_vectors_count=indexed
        )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patched on the `time` module itself (not `qdrant_mod.time`) so the revert
    # probe, run against an adapter that never imported `time`, fails on the
    # assertions rather than erroring in setup.
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _backend(script: list[tuple[str, int, int]]) -> tuple[QdrantBackend, _ScriptedClient]:
    b = object.__new__(QdrantBackend)  # bypass the live-connection __init__
    client = _ScriptedClient(script)
    b._client = client  # type: ignore[attr-defined]
    b._qmodels = _QModels()  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_m = 16  # type: ignore[attr-defined]
    b._hnsw_ef_construct = 64  # type: ignore[attr-defined]
    return b, client


_VECTORS = np.eye(3, dtype=np.float32)
_IDS = ["a", "b", "c"]


def test_the_collection_is_created_with_indexing_forced_on() -> None:
    b, client = _backend([("green", 3, 3)])
    b.ingest(_VECTORS, _IDS)
    opt = client.create_kw.get("optimizers_config")
    assert opt is not None, "Qdrant's 20,000 KB default leaves small segments unindexed"
    # 0 would *disable* indexing; anything above Qdrant's default leaves it off at
    # the sizes this repo runs.
    assert 0 < opt.kw["indexing_threshold"] <= 1


def test_ingest_waits_through_a_mid_build_answer_and_an_early_green() -> None:
    script = [
        ("green", 3, 0),  # optimizer not started yet: green, nothing indexed
        ("yellow", 3, 1),  # building
        ("yellow", 3, 2),
        ("green", 3, 3),  # done
    ]
    b, client = _backend(script)
    b.ingest(_VECTORS, _IDS)
    assert client.polls == 4, client.events
    assert client.events[0] == "upsert"
    assert client.events[-1] == "poll:green:3/3"


def test_an_already_indexed_collection_is_polled_once() -> None:
    b, client = _backend([("green", 3, 3)])
    b.ingest(_VECTORS, _IDS)
    assert client.polls == 1


def test_a_red_collection_raises_backenderror() -> None:
    b, _ = _backend([("yellow", 3, 0), ("red", 3, 0)])
    with pytest.raises(BackendError, match="RED"):
        b.ingest(_VECTORS, _IDS)


def test_an_index_that_never_finishes_raises_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = itertools.count(0, 10)
    monkeypatch.setattr(time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(qdrant_mod, "INDEX_WAIT_TIMEOUT_S", 25.0, raising=False)
    b, client = _backend([("yellow", 3, 1)])
    with pytest.raises(BackendError, match="not fully indexed .* 1 of 3"):
        b.ingest(_VECTORS, _IDS)
    assert client.polls == 3
