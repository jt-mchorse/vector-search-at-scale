"""`vector-bench` CLI.

Subcommands:
- `run`  — one workload, one concurrency, one JSON.
- `load` — one workload, a list of concurrencies, one matrix.json (#4).

Backend selection is by name (`--backend stub|pgvector|qdrant|weaviate`); each
real backend needs its own extra installed and its own env-var DSN/URL/host
set. The hermetic flow is:

    vector-bench run --backend stub --n 100 --queries 20 --run-id smoke-001

which writes `results/smoke-001.json` and prints the same payload to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vector_bench.backends import make_backend
from vector_bench.harness import Workload, run_benchmark
from vector_bench.load import render_table, run_under_load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vector-bench",
        description="Reproducible benchmark harness for pgvector / Qdrant / Weaviate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a benchmark and write results JSON.")
    run_p.add_argument("--backend", required=True, choices=["stub", "pgvector", "qdrant", "weaviate"])
    run_p.add_argument("--n", type=int, required=True, help="Number of corpus vectors.")
    run_p.add_argument("--dim", type=int, default=64)
    run_p.add_argument("--queries", type=int, default=50)
    run_p.add_argument("--top-k", type=int, default=10)
    run_p.add_argument("--concurrency", type=int, default=1)
    run_p.add_argument("--seed", type=int, default=1)
    run_p.add_argument("--run-id", required=True, help="Unique id; results land at results/<run_id>.json.")
    run_p.add_argument("--results-dir", default="results")
    run_p.add_argument("--force", action="store_true",
                       help="Overwrite an existing results/<run_id>.json.")

    load_p = sub.add_parser(
        "load",
        help="Run a latency-under-load study across multiple concurrency levels (#4).",
    )
    load_p.add_argument("--backend", required=True, choices=["stub", "pgvector", "qdrant", "weaviate"])
    load_p.add_argument("--n", type=int, required=True, help="Number of corpus vectors.")
    load_p.add_argument("--dim", type=int, default=64)
    load_p.add_argument("--queries", type=int, default=200)
    load_p.add_argument("--top-k", type=int, default=10)
    load_p.add_argument("--seed", type=int, default=1)
    load_p.add_argument(
        "--concurrency",
        type=str,
        default="1,10,100",
        help="Comma-separated concurrency levels (default: 1,10,100).",
    )
    load_p.add_argument("--run-id", required=True, help="Unique id; results land at results/load/<run_id>/.")
    load_p.add_argument("--results-dir", default="results/load")
    load_p.add_argument("--force", action="store_true",
                       help="Overwrite an existing matrix at results/load/<run_id>/.")
    load_p.add_argument(
        "--render-table",
        action="store_true",
        help="After running, print a markdown table summarizing latency per concurrency.",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return _do_run(args)
    if args.command == "load":
        return _do_load(args)
    parser.error(f"unknown command {args.command!r}")
    return 2  # unreachable


def _do_run(args: argparse.Namespace) -> int:
    backend_kwargs: dict[str, Any] = {}
    backend = make_backend(args.backend, **backend_kwargs)
    workload = Workload(
        n_vectors=args.n,
        dim=args.dim,
        n_queries=args.queries,
        top_k=args.top_k,
        seed=args.seed,
        concurrency=args.concurrency,
    )
    result = run_benchmark(
        backend,
        workload,
        run_id=args.run_id,
        results_dir=args.results_dir,
        force=args.force,
    )
    json.dump(result.to_json(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _do_load(args: argparse.Namespace) -> int:
    try:
        levels = tuple(int(s.strip()) for s in args.concurrency.split(",") if s.strip())
    except ValueError as e:
        print(f"--concurrency must be a comma-separated list of integers: {e}", file=sys.stderr)
        return 2
    if not levels:
        print("--concurrency must contain at least one value", file=sys.stderr)
        return 2

    backend = make_backend(args.backend)
    workload = Workload(
        n_vectors=args.n,
        dim=args.dim,
        n_queries=args.queries,
        top_k=args.top_k,
        seed=args.seed,
        concurrency=max(levels),  # records the max; per-cell concurrency is on the cell itself
    )
    matrix = run_under_load(
        backend,
        workload,
        run_id=args.run_id,
        concurrency_levels=levels,
        results_dir=args.results_dir,
        force=args.force,
    )
    json.dump(matrix.to_json(), sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    if args.render_table:
        print()
        print(render_table([matrix]))
    matrix_path = Path(args.results_dir) / args.run_id / "matrix.json"
    print(f"# matrix.json -> {matrix_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
