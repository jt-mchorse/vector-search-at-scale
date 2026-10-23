"""`PgVectorBackend.prepare_thread` runs one untimed search on the worker's session (#265).

#251's warm-up opened the connection and ran `SET hnsw.ef_search`, but a new
server process still pays a one-off cost on its first real search: loading the
vector type, the table and index relcache, the HNSW scan. #212 gives every
worker a fresh session at every level, so each worker's first TIMED query was
cold. Measured on PG 17 + pgvector 0.8.7 (2000 x 64, k=10): first query 1.44 ms
against 0.33 ms for the second; at c=50 over 300 queries, p95 11.3 -> 8.0 ms.

These arms use a fake psycopg module that records each connection's statements.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pytest

from vector_bench.backends.pgvector import PgVectorBackend
from vector_bench.load import _execute_at_concurrency
from vector_bench.types import BackendError

COLD_S = 0.2
DIM = 4


class _OperationalError(Exception):
    pass


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.conn.log.append((sql, params))
        if sql.startswith("SELECT id"):
            if self.conn.fail_search:
                raise _OperationalError("terminating connection due to administrator command")
            if not self.conn.searched:
                # A fresh server process: its first search pays the cold start.
                time.sleep(COLD_S)
                self.conn.searched = True

    def executemany(self, sql: str, params: object) -> None:
        self.conn.log.append((sql, None))

    def fetchall(self) -> list[tuple[str, float]]:
        return [("a", 0.9)]


class _Conn:
    def __init__(self, registry: list[_Conn]) -> None:
        self.log: list[tuple[str, object]] = []
        self.thread = threading.get_ident()
        self.searched = False
        self.fail_search = False
        registry.append(self)

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None: ...

    def close(self) -> None: ...


@pytest.fixture
def conns(monkeypatch: pytest.MonkeyPatch) -> list[_Conn]:
    registry: list[_Conn] = []
    lock = threading.Lock()

    def connect(_info: str) -> _Conn:
        with lock:
            return _Conn(registry)

    fake = types.SimpleNamespace(
        connect=connect, Error=Exception, OperationalError=_OperationalError
    )
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return registry


def _ingested() -> PgVectorBackend:
    b = PgVectorBackend(conninfo="postgresql://fake", hnsw_ef_search=77)
    b.ingest(np.eye(3, DIM, dtype=np.float32), ["a", "b", "c"])
    return b


def _sqls(c: _Conn) -> list[str]:
    return [sql for sql, _params in c.log]


def _searches(c: _Conn) -> list[tuple[str, object]]:
    return [(sql, p) for sql, p in c.log if sql.startswith("SELECT id")]


def test_prepare_thread_runs_one_search_after_the_set(conns: list[_Conn]) -> None:
    b = _ingested()
    b.release_connections()  # as `run_under_load` does before every level (#212)
    b.prepare_thread()
    (conn,) = [c for c in conns if c.log and c is not conns[0]]
    assert _sqls(conn)[0] == "SET hnsw.ef_search = 77;"
    assert len(_searches(conn)) == 1, conn.log
    _sql, params = _searches(conn)[0]
    literal, _again, k = params  # type: ignore[misc]
    assert literal.count(",") == DIM - 1  # a vector of the ingested dim
    assert k == 1


def test_the_warm_up_sends_the_statement_query_sends(conns: list[_Conn]) -> None:
    b = _ingested()
    b.release_connections()
    b.prepare_thread()
    b.query(np.ones(DIM, dtype=np.float32), 10)
    (conn,) = [c for c in conns if c is not conns[0]]
    warm, timed = _searches(conn)
    assert warm[0] == timed[0]  # same SQL text, so the same plan shape


def test_each_worker_warms_its_own_session(conns: list[_Conn]) -> None:
    b = _ingested()
    b.release_connections()
    barrier = threading.Barrier(4)

    def warm() -> None:
        b.prepare_thread()
        barrier.wait()  # four live threads, four sessions

    threads = [threading.Thread(target=warm) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    workers = [c for c in conns if c is not conns[0]]
    assert len(workers) == 4
    assert all(len(_searches(c)) == 1 for c in workers)


@pytest.mark.parametrize("c", [1, 8])
def test_no_timed_query_pays_the_cold_first_search(conns: list[_Conn], c: int) -> None:
    b = _ingested()
    b.release_connections()
    queries = np.ones((24, DIM), dtype=np.float32)
    latencies, _recalls, _elapsed = _execute_at_concurrency(b, queries, [["a"]] * 24, 1, c)
    assert max(latencies) < COLD_S * 1000 / 2, max(latencies)


def test_a_server_lost_during_the_warm_up_is_a_backend_error(conns: list[_Conn]) -> None:
    b = _ingested()
    conns[0].fail_search = True
    with pytest.raises(BackendError, match="administrator command") as ei:
        b.prepare_thread()
    assert isinstance(ei.value.__cause__, _OperationalError)


# --- controls ---------------------------------------------------------------


def test_before_ingest_prepare_thread_only_sets_ef_search(conns: list[_Conn]) -> None:
    # No dim to build the vector from, and the table may not exist yet: a
    # search here would fail on a fresh database instead of warming anything.
    b = PgVectorBackend(conninfo="postgresql://fake", hnsw_ef_search=77)
    b.prepare_thread()
    assert _sqls(conns[0]) == ["SET hnsw.ef_search = 77;"]


def test_query_still_returns_its_rows(conns: list[_Conn]) -> None:
    b = _ingested()
    assert b.query(np.ones(DIM, dtype=np.float32), 2) == [("a", 0.9)]
