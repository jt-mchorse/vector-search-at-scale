"""A corpus Qdrant will never index is refused up front, and a mid-run
BackendError is exit 2 (#209).

Measured on Qdrant 1.14.0: below 1 KiB of vectors (n x dim < 256 float32) the
collection stayed green with 0 vectors indexed, so #206's index wait slept the
whole INDEX_WAIT_TIMEOUT_S (3,600 s) -- n=3 x 64 and n=15 x 16 never indexed,
n=4 x 64 and n=16 x 16 (exactly 1 KiB) did. The BackendError then escaped
`vector-bench run` as a traceback at exit 1: `run`/`load` caught BackendError
only around backend construction.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from vector_bench import cli
from vector_bench.backends.qdrant import QdrantBackend
from vector_bench.backends.stub import StubBackend
from vector_bench.types import BackendError


class _NoCallsClient:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the SDK must not be called: {name}")


class _Models:
    class VectorParams:
        def __init__(self, **_kw: Any) -> None: ...

    class Distance:
        COSINE = "cosine"

    class HnswConfigDiff:
        def __init__(self, **_kw: Any) -> None: ...

    class OptimizersConfigDiff:
        def __init__(self, **_kw: Any) -> None: ...


def _backend(client: Any) -> QdrantBackend:
    b = object.__new__(QdrantBackend)
    b._client = client  # type: ignore[attr-defined]
    b._qmodels = _Models()  # type: ignore[attr-defined]
    b._collection = "vector_bench"  # type: ignore[attr-defined]
    b._hnsw_m = 16  # type: ignore[attr-defined]
    b._hnsw_ef_construct = 64  # type: ignore[attr-defined]
    return b


def _vectors(n: int, dim: int) -> np.ndarray:
    return np.ones((n, dim), dtype=np.float32) / np.sqrt(dim)


@pytest.mark.parametrize(("n", "dim"), [(3, 64), (15, 16), (50, 4), (1, 255), (255, 1)])
def test_a_corpus_below_the_threshold_is_refused_before_any_sdk_call(n: int, dim: int) -> None:
    with pytest.raises(BackendError, match="never builds an HNSW index") as e:
        _backend(_NoCallsClient()).ingest(_vectors(n, dim), [f"c{i}" for i in range(n)])
    assert f"{n * dim * 4} B" in str(e.value)


@pytest.mark.parametrize(("n", "dim"), [(4, 64), (16, 16), (1, 256), (256, 1)])
def test_exactly_one_kib_reaches_the_sdk(n: int, dim: int) -> None:
    calls: list[str] = []

    class _Recording:
        def recreate_collection(self, **_kw: Any) -> None:
            calls.append("recreate")
            raise RuntimeError("stop here")

    with pytest.raises(RuntimeError, match="stop here"):
        _backend(_Recording()).ingest(_vectors(n, dim), [f"c{i}" for i in range(n)])
    assert calls == ["recreate"]


class _FailingIngest(StubBackend):
    def ingest(self, vectors: np.ndarray, ids: Any) -> None:
        raise BackendError("qdrant collection 'x' is RED after ingest")


@pytest.mark.parametrize("command", ["run", "load"])
def test_a_backend_error_during_the_run_is_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Any, command: str
) -> None:
    monkeypatch.setattr(cli, "make_backend", lambda _name, **_kw: _FailingIngest())
    argv = [
        command,
        "--backend",
        "stub",
        "--n",
        "50",
        "--queries",
        "4",
        "--top-k",
        "2",
        "--run-id",
        "r1",
    ]
    argv += ["--results-dir", str(tmp_path)]
    if command == "load":
        argv += ["--concurrency", "1"]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert err == "error: qdrant collection 'x' is RED after ingest\n"
