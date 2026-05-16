#!/usr/bin/env python3
"""Render per-backend p50/p95/p99 latency vs concurrency from `matrix.json` files.

Inputs are one or more `matrix.json` files written by
``vector-bench load --run-id <id>``; one PNG line chart is emitted per
backend × workload-scale and a single combined markdown table is
printed to stdout.

Matplotlib is lazy-imported so this script is safe to run on a fresh
CI box without the chart dep installed — it degrades to "matplotlib not
installed; chart skipped" and still prints the markdown table.

Usage:
    python scripts/plot_latency.py \\
        results/load/stub-100k/matrix.json \\
        results/load/pgvector-100k/matrix.json \\
        --out-dir docs/latency-under-load
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from vector_bench.harness import LatencyStats, Workload  # noqa: E402
from vector_bench.load import LoadCell, LoadMatrix, render_table  # noqa: E402


def _load_matrix(path: Path) -> LoadMatrix:
    data = json.loads(path.read_text())
    workload = Workload(**data["workload"])
    cells: list[LoadCell] = []
    for c in data["cells"]:
        cells.append(
            LoadCell(
                run_id=c["run_id"],
                backend=c["backend"],
                workload=Workload(**c["workload"]),
                concurrency=int(c["concurrency"]),
                ingest_seconds=float(c["ingest_seconds"]),
                query_latency=LatencyStats(**c["query_latency"]),
                mean_recall_at_k=float(c["mean_recall_at_k"]),
                throughput_qps=float(c["throughput_qps"]),
                started_at=c["started_at"],
                git_sha=c.get("git_sha"),
            )
        )
    return LoadMatrix(
        run_id=data["run_id"],
        backend=data["backend"],
        workload=workload,
        cells=tuple(cells),
    )


def _maybe_plot(matrices: list[LoadMatrix], out_dir: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; chart skipped", file=sys.stderr)
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for m in matrices:
        concs = [c.concurrency for c in m.cells]
        p50 = [c.query_latency.p50_ms for c in m.cells]
        p95 = [c.query_latency.p95_ms for c in m.cells]
        p99 = [c.query_latency.p99_ms for c in m.cells]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(concs, p50, marker="o", label="p50")
        ax.plot(concs, p95, marker="s", label="p95")
        ax.plot(concs, p99, marker="^", label="p99")
        ax.set_xscale("log")
        ax.set_xlabel("concurrency (workers)")
        ax.set_ylabel("query latency (ms)")
        ax.set_title(f"{m.backend} — n={m.workload.n_vectors}, dim={m.workload.dim}")
        ax.grid(True, which="both", linestyle="--", alpha=0.4)
        ax.legend()
        fig.tight_layout()
        path = out_dir / f"{m.backend}_n{m.workload.n_vectors}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrices", nargs="+", help="One or more matrix.json paths.")
    parser.add_argument("--out-dir", default="docs/latency-under-load", help="Where to write PNGs.")
    args = parser.parse_args(argv)

    matrices = [_load_matrix(Path(p)) for p in args.matrices]
    print(render_table(matrices))
    written = _maybe_plot(matrices, Path(args.out_dir))
    for p in written:
        print(f"# wrote {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
