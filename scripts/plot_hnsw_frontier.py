#!/usr/bin/env python3
"""Render the recall-vs-latency Pareto frontier from `hnsw_grid.py` output (#3).

Reads `<grid-dir>/grid.json`, computes the non-dominated frontier on
(p95_ms, mean_recall_at_k) — *lower* latency and *higher* recall are
both good — and renders PNG + SVG with the full grid as grey points and
the frontier in red.

Matplotlib is lazy-imported so this script is safe to run on a fresh CI
box without the chart dep — it degrades to "matplotlib not installed;
chart skipped" and still prints the recommended-defaults table.

Usage:
    python scripts/plot_hnsw_frontier.py results/hnsw-grid/grid.json \\
        --out-png docs/hnsw/frontier.png \\
        --out-svg docs/hnsw/frontier.svg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _dominates(a: dict, b: dict) -> bool:
    """`a` dominates `b` iff a is no-worse on both axes and strictly-better on one.

    Lower latency = better. Higher recall = better.
    """
    a_lat = a["p95_ms"]
    b_lat = b["p95_ms"]
    a_rec = a["mean_recall_at_k"]
    b_rec = b["mean_recall_at_k"]
    no_worse = a_lat <= b_lat and a_rec >= b_rec
    strictly_better = a_lat < b_lat or a_rec > b_rec
    return no_worse and strictly_better


def pareto_frontier(cells: list[dict]) -> list[dict]:
    """Return the non-dominated cells, sorted by p95_ms ascending."""
    frontier: list[dict] = []
    for cand in cells:
        if any(_dominates(other, cand) for other in cells if other is not cand):
            continue
        frontier.append(cand)
    frontier.sort(key=lambda c: (c["p95_ms"], -c["mean_recall_at_k"]))
    return frontier


def recommended_defaults(cells: list[dict], recall_floor: float = 0.95) -> dict | None:
    """Pick the cell with the lowest p95 latency that still clears `recall_floor`.

    Returns None if no cell meets the floor. This is the "knee" of the
    frontier — the cheapest config that still gives you the recall you
    asked for.
    """
    qualifiers = [c for c in cells if c["mean_recall_at_k"] >= recall_floor]
    if not qualifiers:
        return None
    return min(qualifiers, key=lambda c: c["p95_ms"])


def _import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    return plt


def render(
    grid: dict,
    *,
    out_png: Path | None = None,
    out_svg: Path | None = None,
    title: str | None = None,
) -> tuple[list[dict], Path | None, Path | None]:
    cells = grid["cells"]
    if not cells:
        raise ValueError("grid has no cells; nothing to plot")
    frontier = pareto_frontier(cells)

    plt = _import_matplotlib()
    if plt is None:
        sys.stderr.write("matplotlib not installed; chart skipped (table-only output)\n")
        return frontier, None, None

    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    frontier_ids = {c["run_id"] for c in frontier}
    for c in cells:
        x = c["p95_ms"]
        y = c["mean_recall_at_k"]
        if c["run_id"] in frontier_ids:
            ax.scatter(x, y, s=80, color="#d62728", zorder=3, edgecolor="black", linewidth=0.5)
        else:
            ax.scatter(x, y, s=40, color="#7f7f7f", zorder=2, alpha=0.6)

    # Polyline through the frontier if there are at least two points.
    distinct_frontier = {(c["p95_ms"], c["mean_recall_at_k"]) for c in frontier}
    if len(distinct_frontier) >= 2:
        xs = [c["p95_ms"] for c in frontier]
        ys = [c["mean_recall_at_k"] for c in frontier]
        ax.plot(xs, ys, color="#d62728", linewidth=1.6, linestyle="--", alpha=0.8, zorder=2)

    ax.set_xlabel("p95 query latency (ms)")
    ax.set_ylabel(f"mean recall@{grid['workload']['top_k']}")
    if title is None:
        backend = grid.get("backend", "hnsw-sim")
        title = (
            f"HNSW parameter frontier · backend={backend} · "
            f"n_vectors={grid['workload']['n_vectors']}, dim={grid['workload']['dim']}, "
            f"n_queries={grid['workload']['n_queries']}"
        )
    ax.set_title(title)
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.6)
    ax.set_ylim(0, 1.05)
    fig.tight_layout()

    png_path = Path(out_png) if out_png else None
    svg_path = Path(out_svg) if out_svg else None
    for p in (png_path, svg_path):
        if p is None:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(p, dpi=150)
    plt.close(fig)
    return frontier, png_path, svg_path


def _print_table(cells: list[dict], top_k: int, label: str) -> None:
    sys.stdout.write(f"\n## {label}\n")
    sys.stdout.write("| M | ef_construction | ef_search | recall@k | p50 (ms) | p95 (ms) |\n")
    sys.stdout.write("|---|---|---|---:|---:|---:|\n")
    for c in cells:
        sys.stdout.write(
            f"| {c['M']} | {c['ef_construction']} | {c['ef_search']} "
            f"| {c['mean_recall_at_k']:.3f} | {c['p50_ms']:.2f} | {c['p95_ms']:.2f} |\n"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("grid_json", type=Path, help="Path to grid.json from hnsw_grid.py")
    p.add_argument("--out-png", type=Path, default=None)
    p.add_argument("--out-svg", type=Path, default=None)
    p.add_argument("--recall-floor", type=float, default=0.95)
    p.add_argument("--title", default=None)
    args = p.parse_args(argv)

    if not args.grid_json.is_file():
        sys.stderr.write(f"{args.grid_json} not found\n")
        return 2
    grid = json.loads(args.grid_json.read_text(encoding="utf-8"))

    frontier, png, svg = render(grid, out_png=args.out_png, out_svg=args.out_svg, title=args.title)
    top_k = grid["workload"]["top_k"]
    _print_table(frontier, top_k=top_k, label="Pareto frontier")

    knee = recommended_defaults(grid["cells"], recall_floor=args.recall_floor)
    if knee is not None:
        sys.stdout.write(
            f"\nRecommended defaults (knee at recall ≥ {args.recall_floor:.2f}): "
            f"M={knee['M']} ef_construction={knee['ef_construction']} "
            f"ef_search={knee['ef_search']}  →  "
            f"recall={knee['mean_recall_at_k']:.3f}  p95={knee['p95_ms']:.2f}ms\n"
        )
    else:
        sys.stdout.write(
            f"\nNo grid cell achieves recall ≥ {args.recall_floor:.2f}; "
            "expand the grid (higher ef_search) before claiming a default.\n"
        )
    if png is not None:
        sys.stdout.write(f"\nwrote {png}\n")
    if svg is not None:
        sys.stdout.write(f"wrote {svg}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
