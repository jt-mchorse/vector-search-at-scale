"""Tests for `scripts/hnsw_grid.py` and `scripts/plot_hnsw_frontier.py` (#3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from hnsw_grid import run_grid  # noqa: E402
from plot_hnsw_frontier import (  # noqa: E402
    pareto_frontier,
    recommended_defaults,
    render,
)


def _make_cell(M: int, efc: int, efs: int, recall: float, p95: float) -> dict:
    return {
        "run_id": f"M{M}_efc{efc}_efs{efs}",
        "M": M,
        "ef_construction": efc,
        "ef_search": efs,
        "mean_recall_at_k": recall,
        "p50_ms": p95 / 2.0,
        "p95_ms": p95,
        "p99_ms": p95 * 1.2,
        "ingest_seconds": 0.1,
    }


def test_pareto_frontier_filters_dominated_points():
    cells = [
        _make_cell(8, 50, 16, recall=0.60, p95=1.0),
        _make_cell(8, 50, 32, recall=0.85, p95=2.0),
        _make_cell(8, 50, 64, recall=0.95, p95=3.0),
        # Dominated: same recall as previous, higher latency.
        _make_cell(16, 50, 64, recall=0.95, p95=4.0),
        # Dominated: lower recall AND higher latency than (recall=0.95, p95=3.0).
        _make_cell(32, 200, 16, recall=0.80, p95=5.0),
    ]
    frontier = pareto_frontier(cells)
    triples = {(c["M"], c["ef_construction"], c["ef_search"]) for c in frontier}
    assert (16, 50, 64) not in triples
    assert (32, 200, 16) not in triples
    assert (8, 50, 16) in triples
    assert (8, 50, 32) in triples
    assert (8, 50, 64) in triples


def test_pareto_frontier_single_cell():
    cells = [_make_cell(8, 50, 16, recall=0.9, p95=2.0)]
    assert pareto_frontier(cells) == cells


def test_recommended_defaults_picks_lowest_latency_meeting_floor():
    cells = [
        _make_cell(8, 50, 16, recall=0.60, p95=1.0),
        _make_cell(8, 100, 32, recall=0.96, p95=2.5),
        _make_cell(16, 100, 64, recall=0.97, p95=5.0),
        _make_cell(32, 200, 128, recall=0.99, p95=10.0),
    ]
    pick = recommended_defaults(cells, recall_floor=0.95)
    assert pick is not None
    assert pick["ef_search"] == 32  # lowest p95 among those clearing 0.95


def test_recommended_defaults_returns_none_when_floor_unmet():
    cells = [_make_cell(8, 50, 16, recall=0.7, p95=1.0)]
    assert recommended_defaults(cells, recall_floor=0.95) is None


def test_run_grid_end_to_end_tiny_workload(tmp_path: Path):
    payload = run_grid(
        n_vectors=200,
        n_queries=20,
        dim=16,
        top_k=5,
        seed=1,
        M_values=[8, 16],
        ef_construction_values=[50],
        ef_search_values=[16, 32],
        out_dir=tmp_path,
    )
    # 2 M × 1 efc × 2 efs = 4 cells.
    assert len(payload["cells"]) == 4
    # Each cell wrote its BenchmarkResult JSON.
    for cell in payload["cells"]:
        assert (tmp_path / f"{cell['run_id']}.json").exists()
    # And grid.json was written.
    grid_path = tmp_path / "grid.json"
    assert grid_path.exists()
    on_disk = json.loads(grid_path.read_text())
    assert on_disk["axes"]["M"] == [8, 16]
    assert on_disk["axes"]["ef_search"] == [16, 32]
    # Recall is monotone-ish in ef_search at fixed (M, efc) — the simulation's
    # core claim. Allow a small tolerance for stochastic ties; assert that the
    # larger ef_search row is at least as good as the smaller.
    for M in (8, 16):
        small = next(c for c in payload["cells"] if c["M"] == M and c["ef_search"] == 16)
        large = next(c for c in payload["cells"] if c["M"] == M and c["ef_search"] == 32)
        assert large["mean_recall_at_k"] >= small["mean_recall_at_k"]


@pytest.mark.parametrize("svg", [False, True])
def test_render_writes_png_and_optionally_svg(tmp_path: Path, svg: bool):
    pytest.importorskip("matplotlib")
    grid = {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 1000, "n_queries": 100, "dim": 32, "top_k": 5, "seed": 1},
        "axes": {"M": [8], "ef_construction": [50], "ef_search": [16, 64]},
        "cells": [
            _make_cell(8, 50, 16, recall=0.7, p95=1.5),
            _make_cell(8, 50, 64, recall=0.95, p95=3.0),
        ],
    }
    out_png = tmp_path / "frontier.png"
    out_svg = tmp_path / "frontier.svg" if svg else None
    frontier, png, svg_path = render(grid, out_png=out_png, out_svg=out_svg)
    assert out_png.exists()
    assert png == out_png
    if svg:
        assert out_svg.exists()
        assert svg_path == out_svg
    assert len(frontier) == 2  # both non-dominated


def test_render_rejects_empty_grid(tmp_path: Path):
    grid = {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 0, "n_queries": 0, "dim": 0, "top_k": 0, "seed": 1},
        "axes": {"M": [], "ef_construction": [], "ef_search": []},
        "cells": [],
    }
    with pytest.raises(ValueError, match="no cells"):
        render(grid, out_png=tmp_path / "out.png")
