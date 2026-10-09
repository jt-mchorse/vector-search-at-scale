"""`load --backend pgvector` fits the server's connection limit (#212).

`PgVectorBackend` holds one connection per worker thread (#191) and closed them
only in `close()`, while `run_under_load` runs each level on a fresh thread
pool. Measured on main against a real Postgres 17 (default max_connections=100,
pgvector SQL answered by a shim with a real 50 ms round trip): levels 1 and 10
left 12 connections open, and level 100 failed with "FATAL: sorry, too many
clients already" after the ingest and two levels.

Here a fake psycopg enforces a server limit the way Postgres does.
"""

from __future__ import annotations

import threading
import time
import types
from typing import Any

import pytest

from vector_bench.backends.pgvector import PgVectorBackend
from vector_bench.harness import Workload
from vector_bench.load import run_under_load


class _Server:
    def __init__(self, limit: int, others: int = 0, superuser: bool = True) -> None:
        self.limit, self.others, self.superuser = limit, others, superuser
        self.live = others
        self.peak = others
        self.connects = 0
        self.lock = threading.Lock()

    def connect(self, _info: str) -> _Conn:
        with self.lock:
            if self.live >= self.limit:
                raise RuntimeError("FATAL:  sorry, too many clients already")
            self.live += 1
            self.connects += 1
            self.peak = max(self.peak, self.live)
        return _Conn(self)


class _Cursor:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.rows: list[Any] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_a: object) -> None: ...

    def execute(self, sql: str, params: Any = None) -> None:
        if "max_connections" in sql:
            s = self.server
            self.rows = [(s.limit, 3, 0, s.superuser, False, s.others + (s.live - s.others - 1))]
        elif sql.startswith("SELECT id"):
            time.sleep(0.002)  # long enough that a level really runs its workers at once
            self.rows = [(f"c{i}", 0.9) for i in range(params[2])]

    def executemany(self, sql: str, params: Any) -> None: ...

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any:
        return self.rows[0]


class _Conn:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self.server)

    def commit(self) -> None: ...

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            with self.server.lock:
                self.server.live -= 1


def _backend(server: _Server) -> PgVectorBackend:
    b = object.__new__(PgVectorBackend)
    b._conninfo = "postgres:///fake"
    b._psycopg = types.SimpleNamespace(connect=server.connect)
    b._local = threading.local()
    b._conns, b._conns_lock, b._ef_search_set = [], threading.Lock(), set()
    b._index_method = "hnsw"
    b._hnsw_m, b._hnsw_ef_construction, b._hnsw_ef_search = 16, 64, 40
    b._dim = None
    b._closed = False
    return b


WORKLOAD = Workload(n_vectors=200, dim=8, n_queries=200, top_k=5, seed=1, concurrency=1)


def test_the_default_levels_fit_a_stock_server() -> None:
    server = _Server(limit=100)
    b = _backend(server)
    matrix = run_under_load(
        b, WORKLOAD, run_id="r", concurrency_levels=(1, 10, 100), write_json=False
    )
    assert [c.concurrency for c in matrix.cells] == [1, 10, 100]
    assert server.peak <= 100


def test_a_finished_level_does_not_hold_its_connections() -> None:
    server = _Server(limit=1000)
    b = _backend(server)
    run_under_load(b, WORKLOAD, run_id="r", concurrency_levels=(10, 20), write_json=False)
    # Never the sum of the levels: each level runs on fresh connections.
    assert server.peak <= 20


@pytest.mark.parametrize(
    ("limit", "others", "superuser", "levels"),
    [
        (100, 0, True, (1, 10, 101)),
        (100, 0, False, (1, 98)),  # 3 slots reserved for superusers
        (100, 10, True, (1, 91)),  # other clients hold 10
    ],
)
def test_a_level_the_server_cannot_serve_is_refused_before_ingest(
    limit: int, others: int, superuser: bool, levels: tuple[int, ...]
) -> None:
    server = _Server(limit=limit, others=others, superuser=superuser)
    b = _backend(server)
    ingested: list[int] = []
    b.ingest = lambda *_a: ingested.append(1)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match=r"need that many connections at once"):
        run_under_load(b, WORKLOAD, run_id="r", concurrency_levels=levels, write_json=False)
    assert ingested == []
    assert server.live == others  # the capacity probe's connection was released


def test_the_largest_level_that_fits_runs() -> None:
    server = _Server(limit=100, others=0, superuser=False)
    b = _backend(server)
    matrix = run_under_load(b, WORKLOAD, run_id="r", concurrency_levels=(1, 97), write_json=False)
    assert [c.concurrency for c in matrix.cells] == [1, 97]
