"""Each pgvector backend instance gets its own index parameters and dimension (#184).

`_ensure_table` used `CREATE TABLE/INDEX IF NOT EXISTS`, which Postgres skips
for an existing object without comparing definitions: every hnsw_grid cell
after the first ran on the first cell's `m`/`ef_construction` (while grid.json
labelled it with its own), and a new `--dim` hit the old `vector(dim)` column.
A stand-in `psycopg` records the SQL; pgvector itself is not needed.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest


class _Cursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def execute(self, sql: str, params: object = None) -> None:
        self.log.append(" ".join(sql.split()))

    def executemany(self, sql: str, params: object) -> None:
        self.log.append(" ".join(sql.split()))


class _Conn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def sql_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    fake = types.ModuleType("psycopg")
    fake.connect = lambda conninfo: _Conn(log)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return log


def _ingest(m: int, efc: int, dim: int) -> None:
    from vector_bench.backends.pgvector import PgVectorBackend

    b = PgVectorBackend(conninfo="postgresql://x", hnsw_m=m, hnsw_ef_construction=efc)
    b.ingest(np.zeros((2, dim), dtype=np.float32), ["a", "b"])
    b.close()


def test_each_grid_cell_builds_its_own_index(sql_log: list[str]) -> None:
    _ingest(8, 32, 4)
    _ingest(32, 128, 4)
    creates = [s for s in sql_log if s.startswith("CREATE INDEX")]
    assert "WITH (m = 8, ef_construction = 32)" in creates[0]
    assert "WITH (m = 32, ef_construction = 128)" in creates[1]
    # Each create follows a drop of the table that carries the old index.
    for i, s in enumerate(sql_log):
        if s.startswith("CREATE TABLE"):
            assert sql_log[i - 1] == "DROP TABLE IF EXISTS vector_bench;"


def test_a_new_dimension_gets_a_new_column(sql_log: list[str]) -> None:
    _ingest(16, 64, 4)
    _ingest(16, 64, 32)
    tables = [s for s in sql_log if s.startswith("CREATE TABLE")]
    assert "vector(4)" in tables[0]
    assert "vector(32)" in tables[1]


def test_no_definition_is_left_to_if_not_exists(sql_log: list[str]) -> None:
    _ingest(16, 64, 4)
    assert not [s for s in sql_log if "TABLE IF NOT EXISTS" in s or "INDEX IF NOT EXISTS" in s]
