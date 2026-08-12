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
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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

    def __post_init__(self) -> None:
        # Finiteness/range guards (#57) — the load-matrix sibling of the
        # BenchmarkResult guard (#55). LoadCell had no __post_init__ while
        # Workload (#29), InstancePrice/EbsGp3Price (#53), and BenchmarkResult
        # (#55) all guard their numerics. A non-finite throughput_qps (e.g. the
        # inf a zero-time query phase produced) reaches dump_* -> json.dumps
        # (default allow_nan=True) and serializes as the bare token `Infinity` in
        # matrix.json / the per-cell JSON plot_latency.py reads — invalid JSON and
        # a fabricated number (handoff §10). Fail loud at construction.
        for name, value in [
            ("ingest_seconds", self.ingest_seconds),
            ("throughput_qps", self.throughput_qps),
        ]:
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")
        if not math.isfinite(self.mean_recall_at_k) or not (0.0 <= self.mean_recall_at_k <= 1.0):
            raise ValueError(
                f"mean_recall_at_k must be a finite number in [0, 1], got {self.mean_recall_at_k!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        # Ten-field contract (#39). Nests `workload.to_dict()` +
        # `query_latency.to_dict()` so a future internal-only field on
        # `Workload` or `LatencyStats` can't silently leak into the
        # per-cell JSON consumed by `scripts/plot_latency.py`.
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "workload": self.workload.to_dict(),
            "concurrency": self.concurrency,
            "ingest_seconds": self.ingest_seconds,
            "query_latency": self.query_latency.to_dict(),
            "mean_recall_at_k": self.mean_recall_at_k,
            "throughput_qps": self.throughput_qps,
            "started_at": self.started_at,
            "git_sha": self.git_sha,
        }

    def to_json(self) -> dict[str, Any]:
        # Back-compat alias (#39).
        return self.to_dict()


@dataclass(frozen=True)
class LoadMatrix:
    """All cells for one `(backend, workload)` pair across concurrency levels."""

    run_id: str
    backend: str
    workload: Workload
    cells: tuple[LoadCell, ...]

    def to_dict(self) -> dict[str, Any]:
        # Four-field contract (#39). `cells` is a list of dicts in
        # original order (NOT a tuple — JSON has no tuple type) so
        # consumers reading `matrix.json` see a stable list shape.
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "workload": self.workload.to_dict(),
            "cells": [c.to_dict() for c in self.cells],
        }

    def to_json(self) -> dict[str, Any]:
        # Back-compat alias (#39).
        return self.to_dict()


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
    # Levels must be DISTINCT: the per-cell dump keys each file on
    # `c{concurrency:03d}.json`, so two cells sharing a concurrency both write
    # the same filename (last-writer-wins) and one measured cell is silently
    # lost — the on-disk files then no longer round-trip the in-memory
    # `LoadMatrix`, violating the D-007 "one JSON per run cell, no silent
    # clobber" idempotency contract while the run still reports success.
    if len(set(concurrency_levels)) != len(concurrency_levels):
        raise ValueError(
            f"concurrency_levels must be distinct; got {list(concurrency_levels)} "
            "(duplicate levels collide on the per-cell c<NNN>.json filename)"
        )

    out_dir = Path(results_dir) / run_id
    matrix_path = out_dir / "matrix.json"
    if write_json and not force and matrix_path.exists():
        # Pre-flight the force-check before paying ingest + sweep cost
        # (same pattern as `run_benchmark`); the dump wrapper re-runs
        # the check at write time but `force=True` from us skips it.
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
            # Throughput is measured from the wall-clock the concurrent query
            # phase actually took, not derived from the per-query latencies.
            # The old `n_queries / (sum(latencies)/c)` baked in perfect linear
            # scaling: it divided the *sum* of overlapping per-query service
            # times by `c`, so QPS grew with concurrency by construction even
            # for a backend that gains nothing from it — and could report a
            # throughput above the backend's physical serialization ceiling
            # (#47). `_execute_at_concurrency` runs the queries for real, so the
            # honest number is queries-served / wall-clock-elapsed.
            query_start = time.perf_counter()
            latencies_ms, recalls = _execute_at_concurrency(
                backend, queries, truth, workload.top_k, c
            )
            query_elapsed_s = time.perf_counter() - query_start
            # A non-positive query-phase wall-clock is a degenerate measurement
            # (a clock that didn't advance): there is no meaningful QPS. The
            # previous `else float("inf")` fabricated an infinite throughput that
            # serialized as the invalid-JSON token `Infinity` (#57). Fail loud at
            # the measurement site instead, where the cause is locatable.
            if query_elapsed_s <= 0:
                raise ValueError(
                    f"query phase finished in a non-positive {query_elapsed_s:.6g}s at "
                    f"concurrency={c}; a degenerate query-phase wall-clock can't yield a "
                    "meaningful throughput_qps — check the timer/backend"
                )
            throughput_qps = workload.n_queries / query_elapsed_s
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
        # Idempotency contract belongs to `dump_load_matrix_json` (#39).
        dump_load_matrix_json(out_dir, matrix=matrix, force=True)

    return matrix


def dump_load_matrix_json(
    out_dir: str | Path,
    *,
    matrix: LoadMatrix,
    force: bool = False,
) -> Path:
    """Atomically write `matrix.json` + one `c<NNN>.json` per cell into ``out_dir`` (#39).

    Pulls the inline per-cell + matrix.json writes out of
    `run_under_load` so callers that build a `LoadMatrix` outside the
    runner (cross-run aggregation, ad-hoc analysis) can materialize one
    through the same idempotency contract.

    Refuses to overwrite an existing ``out_dir/matrix.json`` unless
    ``force=True`` — same D-007 idempotency posture as
    `dump_benchmark_json`. Routes through `vector_bench.io_utils
    .atomic_write_text` per D-012.

    Returns the resolved `matrix.json` path.
    """
    out_dir_path = Path(out_dir)
    matrix_path = out_dir_path / "matrix.json"
    if not force and matrix_path.exists():
        raise FileExistsError(
            f"matrix already exists at {matrix_path}; pass force=True to overwrite"
        )
    for cell in matrix.cells:
        atomic_write_text(
            out_dir_path / f"c{cell.concurrency:03d}.json",
            json.dumps(cell.to_dict(), indent=2, sort_keys=True),
        )
    atomic_write_text(
        matrix_path,
        json.dumps(matrix.to_dict(), indent=2, sort_keys=True),
    )
    return matrix_path


def _column_labels(matrices: list[LoadMatrix]) -> list[str]:
    """One label per matrix, disambiguated by ``run_id`` only when needed (#123).

    Headers were built from ``m.backend`` alone, so rendering two matrices of
    the *same* backend — a baseline and a re-tune, which is the workload the
    per-run_id results tree exists for — produced six identically-labelled
    columns. No data was lost; both series were present and correct in input
    order. But the table's docstring says it is "designed to drop into a
    README", and once it is in a README the input order that disambiguated it
    is no longer visible to the reader.

    ``run_id`` is the disambiguator rather than a positional index because the
    repo already answers "which run is this?" that way: D-007 keys the results
    tree one JSON file per run_id, and #121 keyed the chart filenames by
    run_id for this exact baseline-vs-re-tune case. This follows that identity
    instead of inventing a competing one.

    The fallback is *conditional* — a backend appearing once keeps its short
    label. That keeps every existing invocation byte-identical (notably the
    committed README "Latency under load" table and `cli.py --render-table`,
    which passes a single matrix), so the new label appears only in the case
    that is ambiguous today.
    """
    counts = Counter(m.backend for m in matrices)
    return [f"{m.backend} @ {m.run_id}" if counts[m.backend] > 1 else m.backend for m in matrices]


def render_table(matrices: list[LoadMatrix]) -> str:
    """Markdown table summarizing latency under load across backends.

    Rows: concurrency level. Columns: per-backend p50 / p95 / p99 ms.
    Designed to drop into a README. Header is one row per
    backend-column (Markdown doesn't support stacked headers cleanly).

    When one backend appears more than once in ``matrices``, that backend's
    columns are labelled ``{backend} @ {run_id}`` so the reader can tell the
    runs apart; backends appearing once keep the short label (#123).
    """
    if not matrices:
        return "_(no matrices to render)_"

    concurrencies = sorted({cell.concurrency for m in matrices for cell in m.cells})

    labels = _column_labels(matrices)
    header = ["concurrency"]
    for label in labels:
        header.append(f"{label} p50 ms")
        header.append(f"{label} p95 ms")
        header.append(f"{label} p99 ms")

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
