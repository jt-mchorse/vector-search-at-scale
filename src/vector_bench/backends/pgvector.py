"""pgvector adapter.

Uses `psycopg` (binary) to talk to a `pgvector`-equipped Postgres. The
adapter assumes the table layout that the matching terraform module
boots — see `terraform/modules/pgvector/user_data.sh` for the schema. If
the table is missing, the adapter creates it on first ingest.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence

import numpy as np

from vector_bench.types import BackendError

TABLE_NAME = "vector_bench"


class PgVectorBackend:
    name = "pgvector"

    def __init__(
        self,
        *,
        conninfo: str | None = None,
        index_method: str = "hnsw",
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
        hnsw_ef_search: int = 40,
    ) -> None:
        try:
            import psycopg  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise BackendError(
                "PgVectorBackend requires the `pgvector` extra: pip install 'vector-bench[pgvector]'"
            ) from e
        self._psycopg = psycopg
        self._conninfo = conninfo or os.environ.get("PGVECTOR_DSN")
        if not self._conninfo:
            raise BackendError("PgVectorBackend: pass conninfo or set PGVECTOR_DSN")
        self._index_method = index_method
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construction = hnsw_ef_construction
        self._hnsw_ef_search = hnsw_ef_search
        self._conn = None  # type: ignore[assignment]
        self._dim: int | None = None

    def _ensure_conn(self):
        if self._conn is None:
            self._conn = self._psycopg.connect(self._conninfo)
        return self._conn

    def _ensure_table(self, dim: int) -> None:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (id TEXT PRIMARY KEY, embedding vector({dim}));"
            )
            if self._index_method == "hnsw":
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {TABLE_NAME}_hnsw ON {TABLE_NAME} "
                    f"USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = {self._hnsw_m}, ef_construction = {self._hnsw_ef_construction});"
                )
                cur.execute(f"SET hnsw.ef_search = {self._hnsw_ef_search};")
        conn.commit()

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        dim = vectors.shape[1]
        self._dim = dim
        self._ensure_table(dim)
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TABLE_NAME};")
            params = [(ids[i], _to_pgvector_literal(vectors[i])) for i in range(vectors.shape[0])]
            cur.executemany(
                f"INSERT INTO {TABLE_NAME} (id, embedding) VALUES (%s, %s::vector);",
                params,
            )
        conn.commit()

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, 1 - (embedding <=> %s::vector) FROM {TABLE_NAME} "
                f"ORDER BY embedding <=> %s::vector LIMIT %s;",
                (_to_pgvector_literal(vector), _to_pgvector_literal(vector), k),
            )
            return [(row[0], float(row[1])) for row in cur.fetchall()]

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None


def _to_pgvector_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec.tolist()) + "]"
