"""pgvector adapter.

Uses `psycopg` (binary) to talk to a `pgvector`-equipped Postgres. The
adapter assumes the table layout that the matching terraform module
boots — see `terraform/modules/pgvector/user_data.sh` for the schema. If
the table is missing, the adapter creates it on first ingest.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Sequence

import numpy as np

from vector_bench.types import BackendError, check_ingest_shape, check_open

TABLE_NAME = "vector_bench"


# pgvector's own HNSW bounds (v0.8.0, `src/hnsw.h` and `src/hnswbuild.c`).
# The server enforces them only when the index is BUILT, i.e. at the first
# `ingest`; a grid cell outside them used to die mid-sweep with
# `InvalidParameterValue: ef_construction must be greater than or equal to
# 2 * m`, after earlier cells had already been written (#188).
HNSW_M_RANGE = (2, 100)
HNSW_EF_CONSTRUCTION_RANGE = (4, 1000)
HNSW_EF_SEARCH_RANGE = (1, 1000)


def validate_hnsw_params(*, m: int, ef_construction: int, ef_search: int) -> None:
    """Raise ``ValueError`` unless pgvector would accept these HNSW knobs.

    Pure, so callers can check a whole grid before connecting to anything.
    """
    for name, value, (lo, hi) in (
        ("hnsw_m", m, HNSW_M_RANGE),
        ("hnsw_ef_construction", ef_construction, HNSW_EF_CONSTRUCTION_RANGE),
        ("hnsw_ef_search", ef_search, HNSW_EF_SEARCH_RANGE),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"pgvector {name} must be an int in [{lo}, {hi}]; got {value!r}")
    if ef_construction < 2 * m:
        raise ValueError(
            f"pgvector requires ef_construction >= 2 * m; got ef_construction={ef_construction} "
            f"with m={m} (needs >= {2 * m})"
        )


class PgVectorBackend:
    name = "pgvector"

    # Class-level, not an instance field: it must be present on an instance
    # built by `object.__new__` too -- that is how this repo's adapter tests
    # drive the SDK-backed backends without a live engine. Also keeps it out
    # of the dataclass field list, so it is not a constructor argument (#133).
    _closed = False

    def __init__(
        self,
        *,
        conninfo: str | None = None,
        index_method: str = "hnsw",
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
        hnsw_ef_search: int = 40,
    ) -> None:
        # Before the import and the DSN check: a bad knob is the caller's error
        # whatever the environment, and the grid relies on this raising early.
        if index_method == "hnsw":
            validate_hnsw_params(
                m=hnsw_m, ef_construction=hnsw_ef_construction, ef_search=hnsw_ef_search
            )
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
        # One connection PER THREAD (#191). A single shared connection was what
        # every load-test worker queued on: psycopg runs one statement at a time
        # per connection, so concurrency only added waiting. Measured on
        # pgvector 0.8.0 (5k x 64, 400 queries): 3806 / 4007 / 3780 qps at
        # concurrency 1 / 4 / 16 shared, against 3698 / 9035 / 5928 with a
        # connection per thread -- and p50 rose 0.24 -> 4.12 ms on the shared
        # one. `load.py` documented "a psycopg connection pool" all along.
        self._local = threading.local()
        self._conns: list = []
        self._conns_lock = threading.Lock()
        # `SET hnsw.ef_search` is per SESSION, so each new connection needs its
        # own; a worker connection without it would query at pgvector's default
        # (40) under a cell labelled with this backend's value.
        self._ef_search_set: set[int] = set()
        self._dim: int | None = None

    def _ensure_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._psycopg.connect(self._conninfo)
            with self._conns_lock:
                self._conns.append(conn)
            self._local.conn = conn
        return conn

    def _apply_ef_search(self, conn) -> None:
        if self._index_method != "hnsw" or id(conn) in self._ef_search_set:
            return
        with conn.cursor() as cur:
            cur.execute(f"SET hnsw.ef_search = {self._hnsw_ef_search};")
        conn.commit()
        with self._conns_lock:
            self._ef_search_set.add(id(conn))

    def _ensure_table(self, dim: int) -> None:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Recreated, not `IF NOT EXISTS` (#184). Postgres skips an existing
            # object without comparing definitions, so every hnsw_grid cell after
            # the first ran on the FIRST cell's index (m, ef_construction) under
            # a grid.json labelled with its own, and a new `--dim` hit the old
            # `vector(dim)` column. The table is bench-owned and `ingest`
            # empties it anyway; dropping it takes the index with it.
            cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME};")
            cur.execute(
                f"CREATE TABLE {TABLE_NAME} (id TEXT PRIMARY KEY, embedding vector({dim}));"
            )
            if self._index_method == "hnsw":
                cur.execute(
                    f"CREATE INDEX {TABLE_NAME}_hnsw ON {TABLE_NAME} "
                    f"USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = {self._hnsw_m}, ef_construction = {self._hnsw_ef_construction});"
                )
        conn.commit()
        self._apply_ef_search(conn)

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        check_open(self._closed, backend="PgVectorBackend", method="ingest")
        check_ingest_shape(vectors, ids)
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
        check_open(self._closed, backend="PgVectorBackend", method="query")
        conn = self._ensure_conn()
        self._apply_ef_search(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, 1 - (embedding <=> %s::vector) FROM {TABLE_NAME} "
                f"ORDER BY embedding <=> %s::vector LIMIT %s;",
                (_to_pgvector_literal(vector), _to_pgvector_literal(vector), k),
            )
            return [(row[0], float(row[1])) for row in cur.fetchall()]

    def close(self) -> None:
        with self._conns_lock:
            conns, self._conns = self._conns, []
            self._ef_search_set.clear()
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()
        self._local = threading.local()
        # Set last, so the flag is only raised once teardown has actually run
        # (#133). `close()` stays idempotent: a second call re-runs the
        # already-safe teardown above and re-sets a flag that is already True.
        self._closed = True


def _to_pgvector_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec.tolist()) + "]"
