"""PgVectorBackend gives each thread its own connection (#191).

The adapter held ONE psycopg connection, and psycopg runs one statement at a
time per connection, so every load-test worker queued on it. A hunt agent
measured it on pgvector 0.8.0 (5k x 64, 400 queries, concurrency 1/4/16):

    shared connection      3806 / 4007 / 3780 qps   p50 0.24 / 1.48 / 4.12 ms
    connection per thread  3698 / 9035 / 5928 qps

`load.py` has documented "pgvector via psycopg connection pool" all along.
The second half matters as much: `SET hnsw.ef_search` is per SESSION, so a
worker's own connection must set it, or it queries at pgvector's default (40)
under a cell labelled with the backend's value. These arms use a fake psycopg
module that records which connection ran which statement.
"""

from __future__ import annotations

import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from vector_bench.backends.pgvector import PgVectorBackend


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.conn.log.append(sql)

    def executemany(self, sql: str, params: object) -> None:
        self.conn.log.append(sql)

    def fetchall(self) -> list[tuple[str, float]]:
        return [("a", 0.9)]


class _Conn:
    def __init__(self, registry: list[_Conn]) -> None:
        self.log: list[str] = []
        self.thread = threading.get_ident()
        self.closed = False
        registry.append(self)

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None: ...

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def conns(monkeypatch: pytest.MonkeyPatch) -> list[_Conn]:
    registry: list[_Conn] = []
    lock = threading.Lock()

    def connect(_info: str) -> _Conn:
        with lock:
            return _Conn(registry)

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    return registry


def _backend(**kw: object) -> PgVectorBackend:
    b = PgVectorBackend(conninfo="postgresql://fake", hnsw_ef_search=77, **kw)  # type: ignore[arg-type]
    b.ingest(np.eye(3, 4, dtype=np.float32), ["a", "b", "c"])
    return b


def _queries_from(b: PgVectorBackend, n_threads: int) -> None:
    barrier = threading.Barrier(n_threads)

    def one(_i: int) -> None:
        barrier.wait()  # every thread is live at once
        b.query(np.ones(4, dtype=np.float32), 2)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(one, range(n_threads)))


def _selects(c: _Conn) -> int:
    return sum(1 for s in c.log if s.startswith("SELECT"))


def test_concurrent_queries_run_on_one_connection_per_thread(conns: list[_Conn]) -> None:
    b = _backend()
    _queries_from(b, 4)
    querying = [c for c in conns if _selects(c)]
    assert len(querying) == 4
    assert len({c.thread for c in querying}) == 4


def test_every_querying_connection_set_ef_search_before_its_first_select(
    conns: list[_Conn],
) -> None:
    b = _backend()
    _queries_from(b, 4)
    for c in conns:
        if not _selects(c):
            continue
        first_select = next(i for i, s in enumerate(c.log) if s.startswith("SELECT"))
        assert "SET hnsw.ef_search = 77;" in c.log[:first_select], c.log


def test_a_thread_reuses_its_connection_and_sets_ef_search_once(conns: list[_Conn]) -> None:
    b = _backend()
    for _ in range(3):
        b.query(np.ones(4, dtype=np.float32), 2)
    assert len(conns) == 1  # ingest and queries on this thread share one
    assert conns[0].log.count("SET hnsw.ef_search = 77;") == 1


def test_close_closes_every_threads_connection(conns: list[_Conn]) -> None:
    b = _backend()
    _queries_from(b, 3)
    b.close()
    assert conns
    assert all(c.closed for c in conns)


def test_an_ivfflat_index_sets_no_hnsw_session_knob(conns: list[_Conn]) -> None:
    b = _backend(index_method="ivfflat")
    _queries_from(b, 2)
    assert not any("hnsw.ef_search" in s for c in conns for s in c.log)
