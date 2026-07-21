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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from vector_bench.harness import LatencyStats, Workload  # noqa: E402
from vector_bench.load import LoadCell, LoadMatrix, render_table  # noqa: E402


def _reject_bool_numeric(value: Any, field: str) -> Any:
    """Reject a JSON boolean at a numeric field before `int()`/`float()` coercion.

    `bool` subclasses `int`, so `int(True)`/`float(True)` silently coerce a JSON
    `true`/`false` to `1`/`0` — a fabricated benchmark number that `LoadCell`'s
    finiteness guards never see (they run *after* the coercion here). A recall of
    `true` becomes a fabricated perfect `1.0` on the published latency table/plot
    (handoff §10). The `ValueError` is caught by `main`'s `(TypeError, ValueError,
    KeyError)` handler and mapped to the documented exit 2, the boolean sibling of
    the structural/decode guards landed in #108.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number, not a bool; got {value!r}")
    return value


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
                concurrency=int(_reject_bool_numeric(c["concurrency"], "concurrency")),
                ingest_seconds=float(_reject_bool_numeric(c["ingest_seconds"], "ingest_seconds")),
                query_latency=LatencyStats(**c["query_latency"]),
                mean_recall_at_k=float(
                    _reject_bool_numeric(c["mean_recall_at_k"], "mean_recall_at_k")
                ),
                throughput_qps=float(_reject_bool_numeric(c["throughput_qps"], "throughput_qps")),
                started_at=c["started_at"],
                git_sha=c.get("git_sha"),
            )
        )
    # Canonicalize cells ascending by concurrency. They're stored in whatever
    # order `--concurrency` was passed; the plot path consumes them in array
    # order on a log x-axis, so an unsorted (e.g. 100,10,1) matrix would draw a
    # backtracking latency line. `render_table` already sorts, so this is
    # idempotent there and fixes the chart for free.
    cells.sort(key=lambda c: c.concurrency)
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

    # Operator-supplied paths (README/architecture-doc name `results/load/<id>/
    # matrix.json`). A missing one must translate to a clean exit 2, like the
    # sibling `plot_hnsw_frontier.py` guards its `grid_json` — not escape as a
    # raw FileNotFoundError traceback at exit 1 (#83/#84 exit-code contract).
    paths = [Path(p) for p in args.matrices]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            sys.stderr.write(f"{p} not found\n")
        return 2

    # A present-but-unreadable matrix file (non-UTF-8 bytes, or valid UTF-8 that
    # isn't valid JSON) is bad operator input, the same class the missing-file
    # pre-check handles. `_load_matrix` does `json.loads(path.read_text())`, so
    # such a file raises UnicodeDecodeError or json.JSONDecodeError — neither of
    # which is a FileNotFoundError — and used to leak a raw traceback at exit 1.
    # Translate to a clean exit 2 per the #83/#84 exit-code contract (sibling of
    # llm-eval-harness#174).
    matrices = []
    for p in paths:
        try:
            matrices.append(_load_matrix(p))
        except UnicodeDecodeError as exc:
            sys.stderr.write(f"{p} is not valid UTF-8: {exc}\n")
            return 2
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"{p} is not valid JSON: {exc}\n")
            return 2
        except (TypeError, ValueError, KeyError) as exc:
            # A file that is valid JSON but not a valid load matrix is the same
            # class of bad operator input as the decode failures above: `_load_matrix`
            # does `int(...)`/`float(...)` coercion, dataclass construction
            # (`LatencyStats(**...)` whose `__post_init__` runs `math.isfinite` on
            # each field), and required-key indexing — so a bad-typed field
            # (`p50_ms: "fast"` -> TypeError), a non-numeric scalar
            # (`concurrency: "x"` -> ValueError), or a missing key (KeyError) used
            # to leak a raw traceback at exit 1. Translate to a clean exit 2, the
            # deserialization sibling of the decode guards above and the `load`
            # subcommand's invalid-workload exit-2 (#105/#106), per #83/#84.
            sys.stderr.write(f"{p} is not a valid load matrix: {exc}\n")
            return 2
    print(render_table(matrices))
    # The output dir is operator input too: an unwritable `--out-dir` (a read-only
    # filesystem, a permission-denied dir, or a path component that is a file)
    # makes `_maybe_plot`'s mkdir/savefig raise OSError, which without this guard
    # escaped as a raw traceback at exit 1 — the write-seam sibling of the input
    # guards above (#96) per the #83/#84 exit-code contract.
    try:
        written = _maybe_plot(matrices, Path(args.out_dir))
    except OSError as exc:
        sys.stderr.write(f"could not write to {args.out_dir}: {exc}\n")
        return 2
    for p in written:
        print(f"# wrote {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
