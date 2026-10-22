"""Every level starts all `c` workers, hook or no hook (#254).

`_execute_at_concurrency` runs a level on `ThreadPoolExecutor(max_workers=c)`,
and the executor only starts a thread when none is idle. #251's barrier-held
warm-up made it start all `c` for a backend with a `prepare_thread` hook
(pgvector); stub, hnsw-sim, qdrant and weaviate have none, so with fast queries
the first few threads were recycled. Measured on stub, 10 000 x 64, 500
queries: c=100 ran on about 50 threads and peaked at 30 to 37 queries in flight,
while the cell recorded `concurrency=100`.

The warm-up now runs for every backend, with nothing to run when there is no
hook. These tests count the worker threads alive when the first timed query
starts. That count is fixed by the barrier, so it does not depend on how the
scheduler interleaves the queries that follow.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import Workload
from vector_bench.load import _execute_at_concurrency, run_under_load

QUERY_SPIN_S = 0.0002


class _FastHooklessBackend:
    """No `prepare_thread`; each query holds the GIL for ~0.2 ms, like a short search."""

    name = "fast-hookless"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.baseline_threads = threading.active_count()
        self.workers_at_first_query: int | None = None
        self.query_threads: set[int] = set()
        self.calls = 0

    def ingest(self, vectors: np.ndarray, ids: list[str]) -> None: ...

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self._lock:
            if self.workers_at_first_query is None:
                self.workers_at_first_query = threading.active_count() - self.baseline_threads
            self.query_threads.add(threading.get_ident())
            self.calls += 1
        start = time.perf_counter()
        while time.perf_counter() - start < QUERY_SPIN_S:
            pass
        return []

    def close(self) -> None: ...


def _run(backend: object, n: int, c: int) -> tuple[list[float], list[float]]:
    result = _execute_at_concurrency(backend, np.zeros((n, 4), dtype=np.float32), [[]] * n, 5, c)
    return result[0], result[1]


@pytest.mark.parametrize("c", [32, 64, 100])
def test_a_hookless_backend_has_every_worker_up_before_the_first_timed_query(c: int) -> None:
    # Before #254 one thread was up at the first query, and at c=64 the
    # executor had started only 26-31 by the end of the level.
    backend = _FastHooklessBackend()
    _run(backend, 400, c)
    assert backend.workers_at_first_query == c


class _CountingStub(StubBackend):
    """The real stub, recording per level the workers alive at its first query.

    `run_under_load` calls `release_connections` before every level (#212), so
    it marks the level boundary.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._baseline = 0
        self._first: int | None = None
        self.per_level: list[int] = []

    def release_connections(self) -> None:
        with self._lock:
            if self._first is not None:
                self.per_level.append(self._first)
            self._first = None
            self._baseline = threading.active_count()

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self._lock:
            if self._first is None:
                self._first = threading.active_count() - self._baseline
        return super().query(vector, k)


def test_the_stub_runs_each_committed_level_on_that_many_workers() -> None:
    # The committed `stub-10k` shape: 10 000 x 64, 500 queries, levels 1,10,100.
    backend = _CountingStub()
    workload = Workload(n_vectors=10_000, dim=64, n_queries=500, top_k=10, seed=1)
    matrix = run_under_load(
        backend, workload, run_id="r", concurrency_levels=(1, 10, 100), write_json=False
    )
    backend.release_connections()  # flush the last level's count
    assert backend.per_level == [1, 10, 100]
    assert [cell.concurrency for cell in matrix.cells] == [1, 10, 100]


# --- controls ---------------------------------------------------------------


def test_control_one_worker_was_always_one_worker() -> None:
    # c=1 started its single thread on main too, so the committed c001.json
    # (the cell `scripts/cost_table.py` reads) is unaffected by #254.
    backend = _FastHooklessBackend()
    _run(backend, 50, 1)
    assert backend.workers_at_first_query == 1
    assert len(backend.query_threads) == 1


def test_control_every_query_still_runs_once_in_submission_order() -> None:
    backend = _FastHooklessBackend()
    latencies, recalls = _run(backend, 300, 64)
    assert backend.calls == 300
    assert len(latencies) == len(recalls) == 300
    assert all(lat > 0 for lat in latencies)


def test_control_queries_only_run_on_the_levels_workers() -> None:
    backend = _FastHooklessBackend()
    _run(backend, 400, 16)
    assert 1 <= len(backend.query_threads) <= 16
