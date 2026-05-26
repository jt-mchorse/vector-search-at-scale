"""Latency-under-load study (issue #4).

Runs the same `Workload` against a `Backend` at multiple concurrency
levels using `ThreadPoolExecutor`, capturing per-query latency at each
level. Output is one ``LoadResult`` per concurrency cell, written as
JSON under ``results/load/<run_id>/c<NN>.json``.

Why threads, not asyncio: the three backend SDKs we ship adapters for
(`psycopg2` for pgvector, `qdrant-client`, `weaviate-client`) are sync
clients. Wrapping each in `asyncio.to_thread` would buy nothing over
`ThreadPoolExecutor` and would add an extra layer to debug. The stub
backend is in-process numpy so concurrency for it is GIL-bound; that's
intentional — the stub exists to verify the harness, not to benchmark
concurrent dot products.

Why not k6 or locust: the issue body suggested those tools, but
`pgvector` talks the PostgreSQL wire protocol and Qdrant's REST API
is one of two surfaces (gRPC is the production one). Driving load
through the same `Backend` Protocol the rest of the package uses keeps
the apples-to-apples comparison intact and removes a translation layer.
See `MEMORY/core_decisions_human.md` D-008 for the deliberation.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from vector_bench.harness import (
    LatencyStats,
    Workload,
    _git_sha,
    _percentile,
    _utc_now_iso,
    generate_corpus,
    ground_truth_topk,
    recall_at_k,
)
from vector_bench.io_utils import atomic_write_text
from vector_bench.types import Backend


@dataclass(frozen=True)
class LoadCell:
    """One row of the latency matrix: backend at one concurrency level."""

    run_id: str
    backend: str
    workload: Workload
    concurrency: int
    ingest_seconds: float
    query_latency: LatencyStats
    mean_recall_at_k: float
    throughput_qps: float
    started_at: str
    git_sha: str | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoadMatrix:
    """All cells for one `(backend, workload)` pair across concurrency levels."""

    run_id: str
    backend: str
    workload: Workload
    cells: tuple[LoadCell, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "workload": asdict(self.workload),
            "cells": [c.to_json() for c in self.cells],
        }


def _execute_at_concurrency(
    backend: Backend,
    queries: np.ndarray,
    truth: list[list[str]],
    top_k: int,
    concurrency: int,
) -> tuple[list[float], list[float]]:
    """Drive the query phase at `concurrency` workers.

    Returns ``(latencies_ms, recalls)`` in submission order so the
    caller can correlate with the deterministic query set.

    `backend.query` must already be safe to call from multiple threads
    against a single ingested backend instance — the SDK adapters this
    ships against (pgvector via psycopg connection pool, qdrant via
    sync client, weaviate via sync client) are documented as such for
    read-only queries.
    """
    n = queries.shape[0]
    latencies_ms = [0.0] * n
    recalls = [0.0] * n

    def _one_query(idx: int) -> tuple[int, float, float]:
        q_start = time.perf_counter()
        hits = backend.query(queries[idx], top_k)
        latency_ms = (time.perf_counter() - q_start) * 1000.0
        predicted = [hit_id for hit_id, _score in hits]
        recall = recall_at_k(predicted, truth[idx], top_k)
        return idx, latency_ms, recall

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one_query, i) for i in range(n)]
        for fut in as_completed(futures):
            idx, latency_ms, recall = fut.result()
            latencies_ms[idx] = latency_ms
            recalls[idx] = recall

    return latencies_ms, recalls


def run_under_load(
    backend: Backend,
    workload: Workload,
    *,
    run_id: str,
    concurrency_levels: tuple[int, ...] = (1, 10, 100),
    results_dir: str | Path = "results/load",
    force: bool = False,
    write_json: bool = True,
) -> LoadMatrix:
    """Run a latency-under-load study for one backend × one workload.

    Ingests `workload.n_vectors` once; then issues `workload.n_queries`
    queries at each level in `concurrency_levels`, recording per-cell
    latency stats. The backend's `close()` is called once at the end —
    not per-cell — so ingest cost is paid once.

    Output:
      - One `LoadMatrix` returned in memory.
      - One JSON per cell under ``<results_dir>/<run_id>/c<NN>.json``
        plus a `matrix.json` summary in the same directory.
      - Refuses to overwrite an existing matrix.json without ``force=True``,
        same idempotency contract as `harness.run_benchmark` (D-007).

    Mirrors `harness.run_benchmark` for the per-cell shape so consumers
    can use either output with the same downstream JSON-parsing.
    """
    if not concurrency_levels:
        raise ValueError("concurrency_levels must contain at least one value")
    for c in concurrency_levels:
        if c <= 0:
            raise ValueError(f"concurrency must be positive, got {c}")

    out_dir = Path(results_dir) / run_id
    matrix_path = out_dir / "matrix.json"
    if not force and matrix_path.exists():
        raise FileExistsError(
            f"matrix already exists at {matrix_path}; pass force=True to overwrite"
        )

    corpus, queries, corpus_ids, _ = generate_corpus(workload)
    truth = ground_truth_topk(corpus, queries, corpus_ids, workload.top_k)

    started_at = _utc_now_iso()
    sha = _git_sha()

    cells: list[LoadCell] = []
    try:
        ingest_start = time.perf_counter()
        backend.ingest(corpus, corpus_ids)
        ingest_seconds = time.perf_counter() - ingest_start

        for c in concurrency_levels:
            latencies_ms, recalls = _execute_at_concurrency(
                backend, queries, truth, workload.top_k, c
            )
            total_ms = sum(latencies_ms)
            throughput_qps = (
                workload.n_queries / (total_ms / 1000.0 / max(c, 1))
                if total_ms > 0
                else float("inf")
            )
            cell = LoadCell(
                run_id=run_id,
                backend=backend.name,
                workload=workload,
                concurrency=c,
                ingest_seconds=ingest_seconds,
                query_latency=LatencyStats(
                    p50_ms=_percentile(latencies_ms, 50.0),
                    p95_ms=_percentile(latencies_ms, 95.0),
                    p99_ms=_percentile(latencies_ms, 99.0),
                    max_ms=max(latencies_ms) if latencies_ms else 0.0,
                ),
                mean_recall_at_k=sum(recalls) / len(recalls) if recalls else 0.0,
                throughput_qps=throughput_qps,
                started_at=started_at,
                git_sha=sha,
            )
            cells.append(cell)
    finally:
        backend.close()

    matrix = LoadMatrix(run_id=run_id, backend=backend.name, workload=workload, cells=tuple(cells))

    if write_json:
        for cell in cells:
            atomic_write_text(
                out_dir / f"c{cell.concurrency:03d}.json",
                json.dumps(cell.to_json(), indent=2, sort_keys=True, default=_json_default),
            )
        atomic_write_text(
            matrix_path,
            json.dumps(matrix.to_json(), indent=2, sort_keys=True, default=_json_default),
        )

    return matrix


def _json_default(obj: Any) -> Any:  # noqa: ANN401
    if hasattr(obj, "to_json"):
        return obj.to_json()
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"unserializable: {type(obj).__name__}")


def render_table(matrices: list[LoadMatrix]) -> str:
    """Markdown table summarizing latency under load across backends.

    Rows: concurrency level. Columns: per-backend p50 / p95 / p99 ms.
    Designed to drop into a README. Header is one row per
    backend-column (Markdown doesn't support stacked headers cleanly).
    """
    if not matrices:
        return "_(no matrices to render)_"

    concurrencies = sorted({cell.concurrency for m in matrices for cell in m.cells})

    header = ["concurrency"]
    for m in matrices:
        header.append(f"{m.backend} p50 ms")
        header.append(f"{m.backend} p95 ms")
        header.append(f"{m.backend} p99 ms")

    lines: list[str] = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")

    for c in concurrencies:
        row = [str(c)]
        for m in matrices:
            cell = next((cell for cell in m.cells if cell.concurrency == c), None)
            if cell is None:
                row.extend(["—", "—", "—"])
                continue
            row.append(f"{cell.query_latency.p50_ms:.3f}")
            row.append(f"{cell.query_latency.p95_ms:.3f}")
            row.append(f"{cell.query_latency.p99_ms:.3f}")
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"
