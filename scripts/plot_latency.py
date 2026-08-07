#!/usr/bin/env python3
"""Render per-backend p50/p95/p99 latency vs concurrency from `matrix.json` files.

Inputs are one or more `matrix.json` files written by
``vector-bench load --run-id <id>``; one PNG line chart is emitted per
**run_id** — named ``{run_id}_{backend}_n{n_vectors}.png``, mirroring
D-007's one-file-per-run_id results layout — and a single combined
markdown table is printed to stdout.

Two inputs carrying the same ``run_id`` would render to one path, so the
script rejects that with exit 2 rather than overwriting (#121). Charts are
keyed by run_id precisely so that comparing two runs of *one* backend — a
baseline against a re-tune, which is what the per-run_id results tree is
for — yields two charts rather than one.

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


def _chart_name(m: LoadMatrix) -> str:
    """Chart filename for one matrix — keyed by ``run_id`` first (#121).

    This used to be ``{backend}_n{n_vectors}.png``, which omits the one field
    an operator uses to tell two runs apart. Two matrices of the same backend
    at the same scale — a baseline and a re-tune, the workload the per-run_id
    results tree exists for — collided, and the second silently overwrote the
    first while stderr reported both as written.

    ``run_id`` leads, mirroring D-007's "one JSON file per run_id under
    results/", so the chart set matches the markdown table's columns
    one-for-one. ``backend`` and the vector count stay in the name because
    they're what makes a filename readable in a directory listing.
    """
    return f"{m.run_id}_{m.backend}_n{m.workload.n_vectors}.png"


def _duplicate_targets(matrices: list[LoadMatrix]) -> dict[str, list[str]]:
    """Map each colliding chart filename to the run_ids that produced it.

    Empty when every matrix gets its own chart. A non-empty result means two
    *inputs* in this invocation resolve to one path — with ``run_id`` in the
    name that requires the same run_id twice, which is the operator typo
    D-007's rationale calls out by name.

    Deliberately scoped to within-invocation collisions: overwriting a chart
    left by a *previous* run is normal idempotent regeneration. D-007's
    force-check governs the results JSON, not derived plots.
    """
    seen: dict[str, list[str]] = {}
    for m in matrices:
        seen.setdefault(_chart_name(m), []).append(m.run_id)
    return {name: ids for name, ids in seen.items() if len(ids) > 1}


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
        path = out_dir / _chart_name(m)
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
    # Two inputs resolving to one chart path is a collision, and D-007's
    # rationale — "clear failure mode when operator typos run_id collision" —
    # says it must be loud. Checked here, before `render_table` prints and
    # before any `savefig`, so a rejected invocation leaves neither a
    # half-written chart set nor a table implying the run succeeded.
    collisions = _duplicate_targets(matrices)
    if collisions:
        for name, run_ids in sorted(collisions.items()):
            sys.stderr.write(
                f"{len(run_ids)} matrices both render to {name}: "
                f"run_id {sorted(run_ids)[0]!r} appears {len(run_ids)} times. "
                "Charts are keyed by run_id; pass each run once.\n"
            )
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
