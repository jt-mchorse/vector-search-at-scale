#!/usr/bin/env python3
"""HNSW parameter-tuning grid study (#3).

Grids over (M, ef_construction, ef_search) on `HnswSimBackend` and writes
one JSON per grid cell plus an aggregated `grid.json` with
recall + p50/p95/p99 latency per parameter triple. Frontier rendering
lives in `scripts/plot_hnsw_frontier.py`.

The `HnswSimBackend` is a pure-numpy *simulation* of HNSW's tradeoff —
the recall and latency numbers are real for the simulation but do not
claim to match an actual hnswlib / qdrant / weaviate / pgvector run.
The grid script is reusable on any backend with the same knobs: pass
`--backend qdrant` when the real bring-up is done.

Usage:
    python scripts/hnsw_grid.py \\
        --n-vectors 2000 --n-queries 200 --dim 64 \\
        --M 8,16,32 --ef-construction 50,100,200 --ef-search 16,32,64,128 \\
        --out-dir results/hnsw-grid
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vector_bench.backends import make_backend  # noqa: E402
from vector_bench.harness import Workload, run_benchmark  # noqa: E402


def _parse_int_list(s: str) -> list[int]:
    return [int(p.strip()) for p in s.split(",") if p.strip()]


def run_grid(
    *,
    n_vectors: int,
    n_queries: int,
    dim: int,
    top_k: int,
    seed: int,
    M_values: list[int],
    ef_construction_values: list[int],
    ef_search_values: list[int],
    out_dir: Path,
    backend_name: str = "hnsw-sim",
) -> dict:
    """Run the grid, returning the aggregated `grid.json` payload.

    Side effect: writes one BenchmarkResult JSON per cell under `out_dir/`
    plus `grid.json` summarizing the full grid.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workload = Workload(n_vectors=n_vectors, dim=dim, n_queries=n_queries, top_k=top_k, seed=seed)

    cells: list[dict] = []
    for M, efc, efs in itertools.product(M_values, ef_construction_values, ef_search_values):
        run_id = f"M{M}_efc{efc}_efs{efs}"
        backend = make_backend(backend_name, M=M, ef_construction=efc, ef_search=efs, seed=seed)
        result = run_benchmark(backend, workload, run_id=run_id, results_dir=out_dir, force=True)
        cells.append(
            {
                "run_id": run_id,
                "M": M,
                "ef_construction": efc,
                "ef_search": efs,
                "mean_recall_at_k": result.mean_recall_at_k,
                "p50_ms": result.query_latency.p50_ms,
                "p95_ms": result.query_latency.p95_ms,
                "p99_ms": result.query_latency.p99_ms,
                "ingest_seconds": result.ingest_seconds,
            }
        )
        sys.stdout.write(
            f"M={M:3d} efc={efc:4d} efs={efs:4d}  recall@{top_k}={result.mean_recall_at_k:.3f}  "
            f"p50={result.query_latency.p50_ms:.2f}ms  p95={result.query_latency.p95_ms:.2f}ms\n"
        )

    grid_payload = {
        "backend": backend_name,
        "workload": {
            "n_vectors": n_vectors,
            "n_queries": n_queries,
            "dim": dim,
            "top_k": top_k,
            "seed": seed,
        },
        "axes": {
            "M": M_values,
            "ef_construction": ef_construction_values,
            "ef_search": ef_search_values,
        },
        "cells": cells,
    }
    grid_path = out_dir / "grid.json"
    grid_path.write_text(json.dumps(grid_payload, indent=2, sort_keys=True), encoding="utf-8")
    sys.stdout.write(f"\nwrote {grid_path}\n")
    return grid_payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n-vectors", type=int, default=2000)
    p.add_argument("--n-queries", type=int, default=200)
    p.add_argument("--dim", type=int, default=64)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--M", type=_parse_int_list, default=[8, 16, 32])
    p.add_argument("--ef-construction", type=_parse_int_list, default=[50, 100, 200])
    p.add_argument("--ef-search", type=_parse_int_list, default=[16, 32, 64, 128])
    p.add_argument("--out-dir", type=Path, default=Path("results/hnsw-grid"))
    p.add_argument("--backend", default="hnsw-sim")
    args = p.parse_args(argv)

    run_grid(
        n_vectors=args.n_vectors,
        n_queries=args.n_queries,
        dim=args.dim,
        top_k=args.top_k,
        seed=args.seed,
        M_values=args.M,
        ef_construction_values=args.ef_construction,
        ef_search_values=args.ef_search,
        out_dir=args.out_dir,
        backend_name=args.backend,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
