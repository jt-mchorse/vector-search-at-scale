"""`run_benchmark` warms the backend after ingest, before its first timed query (#270).

#269 gave `PgVectorBackend.prepare_thread` the untimed search a new server
process needs (#265), but only `load._execute_at_concurrency` called it.
`run_benchmark` -- behind `vector-bench run` and every `scripts/hnsw_grid.py`
cell, each of which builds a new backend and connection -- timed its first query
straight after `ingest`. Measured on PG 17 + pgvector 0.8.7, 2000 x 64: the first
query was the run's max in 5 of 5 runs, 1.39 ms against a p50 of 0.30-0.40 ms.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

from vector_bench.harness import Workload, run_benchmark
from vector_bench.types import BackendError

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

SETUP_S = 0.1
WORKLOAD = Workload(n_vectors=50, dim=4, n_queries=20, top_k=5, seed=1)


class _NoHookBackend:
    """The first search after `ingest` pays `SETUP_S`; no `prepare_thread` attribute."""

    name = "no-hook"

    def __init__(self) -> None:
        self.events: list[str] = []
        self._warm = False

    def ingest(self, vectors: np.ndarray, ids: list[str]) -> None:
        self.events.append("ingest")
        self._warm = False

    def _search(self) -> None:
        if not self._warm:
            time.sleep(SETUP_S)
            self._warm = True

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        self.events.append("query")
        self._search()
        return []

    def close(self) -> None:
        self.events.append("close")


class _ColdFirstQueryBackend(_NoHookBackend):
    """`_NoHookBackend` with the hook: `prepare_thread` runs the cold search untimed."""

    name = "cold-first-query"

    def __init__(self, *, prepare_error: Exception | None = None) -> None:
        super().__init__()
        self._prepare_error = prepare_error

    def prepare_thread(self) -> None:
        self.events.append("prepare")
        if self._prepare_error is not None:
            raise self._prepare_error
        self._search()


class _NoneHookBackend(_NoHookBackend):
    """`prepare_thread = None`, the shape `_execute_at_concurrency` also accepts."""

    prepare_thread = None


def _run(backend: object):
    return run_benchmark(backend, WORKLOAD, run_id="warm", write_json=False)  # type: ignore[arg-type]


def test_prepare_thread_runs_once_after_ingest_before_the_first_query() -> None:
    backend = _ColdFirstQueryBackend()
    _run(backend)
    assert backend.events.count("prepare") == 1, backend.events
    assert backend.events[:3] == ["ingest", "prepare", "query"], backend.events


def test_the_cold_first_search_is_not_in_max_or_p99() -> None:
    result = _run(_ColdFirstQueryBackend())
    half_setup_ms = SETUP_S * 1000 / 2
    assert result.query_latency.max_ms < half_setup_ms, result.query_latency
    assert result.query_latency.p99_ms < half_setup_ms, result.query_latency


def test_the_warm_up_is_not_in_ingest_seconds() -> None:
    # Control: the hook runs after the ingest timer stops, so it is green
    # before and after #270 -- it pins where the call went.
    result = _run(_ColdFirstQueryBackend())
    assert result.ingest_seconds < SETUP_S / 2, result.ingest_seconds


@pytest.mark.parametrize("cls", [_NoHookBackend, _NoneHookBackend])
def test_a_backend_without_the_hook_still_runs(cls: type[_NoHookBackend]) -> None:
    # Control: green before and after; the cold first query is still timed,
    # which is the backend's own business when it offers no hook.
    backend = cls()
    result = _run(backend)
    assert "prepare" not in backend.events
    assert backend.events.count("query") == WORKLOAD.n_queries
    assert result.query_latency.max_ms >= SETUP_S * 1000 / 2


def test_a_backend_error_from_the_hook_propagates_and_the_backend_is_closed() -> None:
    backend = _ColdFirstQueryBackend(prepare_error=BackendError("server went away"))
    with pytest.raises(BackendError, match="server went away"):
        _run(backend)
    assert "query" not in backend.events
    assert backend.events[-1] == "close", backend.events


def test_every_hnsw_grid_cell_is_warmed_before_it_is_timed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[_ColdFirstQueryBackend] = []

    def _make_backend(name: str, **kwargs: object) -> _ColdFirstQueryBackend:
        backend = _ColdFirstQueryBackend()
        built.append(backend)
        return backend

    monkeypatch.setattr(hnsw_grid, "make_backend", _make_backend)
    payload = hnsw_grid.run_grid(
        n_vectors=50,
        n_queries=20,
        dim=4,
        top_k=5,
        seed=1,
        M_values=[8, 16],
        ef_construction_values=[50],
        ef_search_values=[16],
        out_dir=tmp_path,
    )
    assert len(built) == len(payload["cells"]) == 2
    for backend in built:
        assert backend.events[:3] == ["ingest", "prepare", "query"], backend.events
    for cell in payload["cells"]:
        assert cell["p99_ms"] < SETUP_S * 1000 / 2, cell
