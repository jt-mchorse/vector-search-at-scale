"""`WeaviateBackend.ingest`: current create arguments, and failed objects raise (#215).

weaviate-client 4.23, which the `weaviate` extra installs today, deprecates the
`vectorizer_config` and `vector_index_config` arguments the adapter passed
(Dep024/Dep025). `search` went the same way in Qdrant (#193). Separately,
`collection.batch.dynamic()` does not raise when objects fail: the client logs
"Failed to send N objects" and the block exits normally, so a partly-failed
ingest was queried and its recall published.

Weaviate has no in-process engine, so these use a recording fake. The real
client and server were checked by hand against embedded Weaviate 1.34.0 (the
Terraform pin), with DeprecationWarning raised as an error.
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest

from vector_bench.backends.weaviate import WeaviateBackend
from vector_bench.types import BackendError


class _Batch:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def __enter__(self) -> _Batch:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def add_object(self, *, properties: dict[str, Any], vector: list[float]) -> None:
        self._sink.append(properties["orig_id"])


def _wvcc(calls: list[tuple[str, dict[str, Any]]]) -> Any:
    def rec(name: str) -> Any:
        def f(**kw: Any) -> tuple[str, dict[str, Any]]:
            calls.append((name, kw))
            return (name, kw)

        return f

    return types.SimpleNamespace(
        Configure=types.SimpleNamespace(
            Vectors=types.SimpleNamespace(self_provided=rec("Vectors.self_provided")),
            # The deprecated spelling is present too, so code still using it
            # fails on what it passes to `create`, not on a missing attribute.
            Vectorizer=types.SimpleNamespace(none=lambda: ("Vectorizer.none", {})),
            VectorIndex=types.SimpleNamespace(hnsw=rec("VectorIndex.hnsw")),
        ),
        VectorDistances=types.SimpleNamespace(COSINE="cosine"),
        Property=rec("Property"),
        DataType=types.SimpleNamespace(TEXT="text"),
    )


def _backend(*, failed: list[Any]) -> tuple[WeaviateBackend, dict[str, Any], list[str]]:
    created: dict[str, Any] = {}
    sink: list[str] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    b = object.__new__(WeaviateBackend)
    b._client = types.SimpleNamespace(  # type: ignore[attr-defined]
        collections=types.SimpleNamespace(
            exists=lambda _n: False,
            delete=lambda _n: None,
            create=lambda **kw: created.update(kw),
            get=lambda _n: types.SimpleNamespace(
                batch=types.SimpleNamespace(dynamic=lambda: _Batch(sink), failed_objects=failed)
            ),
        )
    )
    b._wvcc = _wvcc(calls)  # type: ignore[attr-defined]
    b._collection_name = "C"  # type: ignore[attr-defined]
    b._hnsw_max_connections, b._hnsw_ef_construction, b._hnsw_ef = 16, 100, 64  # type: ignore[attr-defined]
    created["_calls"] = calls
    return b, created, sink


_V = np.eye(3, 8, dtype=np.float32)
_IDS = ["a", "b", "c"]


def test_create_uses_vector_config_not_the_deprecated_arguments() -> None:
    b, created, sink = _backend(failed=[])
    b.ingest(_V, _IDS)
    calls = created.pop("_calls")
    assert set(created) == {"name", "vector_config", "properties"}
    assert created["vector_config"][0] == "Vectors.self_provided"
    hnsw = dict(calls)["VectorIndex.hnsw"]
    assert hnsw == {
        "max_connections": 16,
        "ef_construction": 100,
        "ef": 64,
        "distance_metric": "cosine",
    }
    assert sink == _IDS


def test_failed_objects_raise_instead_of_returning_a_partial_index() -> None:
    failed = [types.SimpleNamespace(message="insert to vector index: boom")]
    b, _created, _sink = _backend(failed=failed)
    with pytest.raises(BackendError, match=r"failed to ingest 1 of 3 objects.*boom"):
        b.ingest(_V, _IDS)
