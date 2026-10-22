"""PgVectorBackend refuses an `index_method` it cannot build (#239).

`_ensure_table` creates an index only when `index_method == "hnsw"`. Every
other value constructed without error and ingested into a table with no
index, so every query was a sequential exact scan. That run reports recall
1.0 at brute-force latency under `backend: "pgvector"`, and nothing in the
result names the index. It is the measurement #205/#209 refuse for Qdrant.
These arms use a fake psycopg module that records the SQL each connection ran.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from vector_bench.backends.pgvector import INDEX_METHODS, PgVectorBackend


class _Cursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.log.append(sql)

    def executemany(self, sql: str, params: object) -> None:
        self.log.append(sql)


class _Conn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    def commit(self) -> None: ...

    def close(self) -> None: ...


@pytest.fixture
def sql_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    fake = types.SimpleNamespace(
        connect=lambda _info: _Conn(log),
        # What the adapter wraps mid-run (#264).
        OperationalError=type("OperationalError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return log


# Each was accepted on main and built no index. `" hnsw"` and `"HNSW"` are the
# near-misses: a lenient fix that normalised case or whitespace would still
# have to build the index those spellings name.
@pytest.mark.parametrize("method", ["ivfflat", "HNSW", " hnsw", "hnws", "", None])
def test_an_index_method_the_adapter_cannot_build_is_refused(
    sql_log: list[str], method: object
) -> None:
    with pytest.raises(ValueError, match="index_method") as exc:
        PgVectorBackend(conninfo="postgresql://fake", index_method=method)  # type: ignore[arg-type]
    assert repr(method) in str(exc.value)
    assert sql_log == []


def test_the_refusal_comes_before_the_dsn_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # As the HNSW knobs are (#188): a caller's error whatever the environment.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    with pytest.raises(ValueError, match="index_method"):
        PgVectorBackend(index_method="ivfflat")


@pytest.mark.parametrize("method", INDEX_METHODS)
def test_every_accepted_index_method_builds_an_index(sql_log: list[str], method: str) -> None:
    b = PgVectorBackend(conninfo="postgresql://fake", index_method=method)
    b.ingest(np.eye(3, 4, dtype=np.float32), ["a", "b", "c"])
    created = [s for s in sql_log if s.startswith("CREATE INDEX")]
    assert len(created) == 1, sql_log
    assert f"USING {method} " in created[0]
