"""Per-thread setup runs before a level's timed queries, not inside them (#251).

`run_under_load` releases pgvector's connections before every level (#212), and
`PgVectorBackend.query` opened its thread's connection and ran
`SET hnsw.ef_search` lazily on each worker's first call -- inside `_one_query`'s
timer and inside the wall-clock `throughput_qps` divides by. Measured on PG 17 +
pgvector 0.8.7 over TCP (2000 x 64, 200 queries): at c=100, 53 of 200 recorded
latencies carried the setup, median 21.3 ms against 3.8 ms reused, and p95 read
28.5 ms.

A backend with a `prepare_thread` hook now has it called once on every worker
thread before the timed phase.
"""

from __future__ import annotations

import threading
import time
import types

import numpy as np
import pytest

from vector_bench.backends.pgvector import PgVectorBackend
from vector_bench.harness import Workload
from vector_bench.load import _execute_at_concurrency, run_under_load
from vector_bench.types import BackendError

SETUP_S = 0.2
QUERY_S = 0.002


class _ColdThreadBackend:
    """A thread's first `query` pays `SETUP_S` unless `prepare_thread` ran on it."""

    name = "cold-thread"

    def __init__(self, *, with_hook: bool = True, prepare_s: float = SETUP_S) -> None:
        self.prepare_s = prepare_s
        self._local = threading.local()
        self._lock = threading.Lock()
        self.prepared: list[int] = []
        self.queried: set[int] = set()
        if not with_hook:
            self.prepare_thread = None  # type: ignore[assignment]

    def _setup(self, cost: float = SETUP_S) -> None:
        if not getattr(self._local, "ready", False):
            time.sleep(cost)
            self._local.ready = True

    def prepare_thread(self) -> None:
        with self._lock:
            self.prepared.append(threading.get_ident())
        self._setup(self.prepare_s)

    def ingest(self, vectors: np.ndarray, ids: list[str]) -> None: ...

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self._lock:
            self.queried.add(threading.get_ident())
        self._setup()
        time.sleep(QUERY_S)
        return []

    def close(self) -> None: ...


def _run(backend: object, n: int, c: int) -> tuple[list[float], list[float]]:
    # Latencies and recalls only, so against the pre-#251 two-tuple a test
    # fails on what it measures rather than on the unpack.
    result = _execute_at_concurrency(backend, np.zeros((n, 4), dtype=np.float32), [[]] * n, 5, c)
    return result[0], result[1]


@pytest.mark.parametrize("c", [1, 4, 16])
def test_the_setup_is_not_in_any_recorded_latency(c: int) -> None:
    latencies, _recalls = _run(_ColdThreadBackend(), 40, c)
    assert max(latencies) < SETUP_S * 1000 / 2, max(latencies)


def test_the_setup_is_not_in_the_throughput_wall_clock() -> None:
    backend = _ColdThreadBackend()
    workload = Workload(n_vectors=20, dim=4, n_queries=40, top_k=5, seed=1, concurrency=1)
    matrix = run_under_load(
        backend, workload, run_id="r", concurrency_levels=(1,), write_json=False
    )
    # 40 queries x 2 ms ~ 80 ms timed; with the 200 ms setup inside it, ~145 qps.
    assert matrix.cells[0].throughput_qps > 40 / (40 * QUERY_S + SETUP_S / 2)


@pytest.mark.parametrize("c", [1, 4, 16])
def test_every_worker_is_prepared_once_and_only_prepared_workers_query(c: int) -> None:
    backend = _ColdThreadBackend()
    _run(backend, 40, c)
    # One call per worker, each on a distinct thread: a worker that took two
    # warm tasks would leave another to set up inside its first timed query.
    assert len(backend.prepared) == c
    assert len(set(backend.prepared)) == c
    assert backend.queried <= set(backend.prepared)


@pytest.mark.parametrize("c", [4, 16])
def test_a_fast_warm_up_still_reaches_every_worker(c: int) -> None:
    # An instant warm task finishes before the next is submitted, and the
    # executor hands the next one to that idle thread instead of starting a
    # new one. Without the barrier every warm task can land on one thread and
    # the rest start cold inside the timed phase.
    backend = _ColdThreadBackend(prepare_s=0.0)
    latencies, _recalls = _run(backend, 40, c)
    assert len(set(backend.prepared)) == c
    assert max(latencies) < SETUP_S * 1000 / 2, max(latencies)


def test_a_failed_warm_up_raises_promptly_and_times_nothing() -> None:
    class _Down(_ColdThreadBackend):
        def prepare_thread(self) -> None:
            with self._lock:
                first = not self.prepared
                self.prepared.append(threading.get_ident())
            if first:
                raise RuntimeError("connect refused")

    backend = _Down()
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="connect refused"):
        _run(backend, 40, 8)
    # The other workers are released from the barrier, not left waiting for it.
    assert time.perf_counter() - started < 5
    assert backend.queried == set()


# --- controls ---------------------------------------------------------------


def test_control_a_backend_without_the_hook_runs_every_query() -> None:
    backend = _ColdThreadBackend(with_hook=False)
    latencies, recalls = _run(backend, 20, 4)
    assert len(latencies) == len(recalls) == 20
    assert backend.prepared == []


def test_control_without_the_hook_the_setup_is_still_timed() -> None:
    # The fake really does charge the first query on a thread; the test above
    # that says otherwise is measuring the hook, not a fake that is always fast.
    latencies, _recalls = _run(_ColdThreadBackend(with_hook=False), 20, 1)
    assert max(latencies) >= SETUP_S * 1000


# --- pgvector ---------------------------------------------------------------


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.rows: list[tuple[str, float]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_a: object) -> None: ...

    def execute(self, sql: str, params: object = None) -> None:
        if sql.startswith("SET hnsw.ef_search"):
            self.conn.ef_search_set = True
        elif sql.startswith("SELECT id"):
            # A query on a session without the SET would run at pgvector's default.
            assert self.conn.ef_search_set
            time.sleep(QUERY_S)
            self.rows = []

    def fetchall(self) -> list[tuple[str, float]]:
        return self.rows


class _Conn:
    def __init__(self) -> None:
        self.ef_search_set = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None: ...

    def close(self) -> None: ...


def _slow_connect(_info: str) -> _Conn:
    time.sleep(SETUP_S)
    return _Conn()


def _pgvector() -> PgVectorBackend:
    b = object.__new__(PgVectorBackend)
    b._conninfo = "postgres:///fake"
    b._psycopg = types.SimpleNamespace(connect=_slow_connect)
    b._local = threading.local()
    b._conns, b._conns_lock, b._ef_search_set = [], threading.Lock(), set()
    b._index_method = "hnsw"
    b._hnsw_m, b._hnsw_ef_construction, b._hnsw_ef_search = 16, 64, 40
    b._dim = None
    b._closed = False
    return b


@pytest.mark.parametrize("c", [1, 8])
def test_pgvector_connect_and_set_are_outside_the_timed_queries(c: int) -> None:
    b = _pgvector()
    b.release_connections()  # as `run_under_load` does before every level (#212)
    latencies, _recalls = _run(b, 24, c)
    assert max(latencies) < SETUP_S * 1000 / 2, max(latencies)
    assert len(b._conns) == c


def test_pgvector_prepare_thread_after_close_raises() -> None:
    b = _pgvector()
    b.close()
    with pytest.raises(BackendError, match="backend is closed"):
        b.prepare_thread()
    assert b._conns == []  # refused before it connects
