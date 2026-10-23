"""`plot_hnsw_frontier.py` rechecks the grid's `workload` as the writer did (#271).

#266 imported `_HNSW_BOUNDS` from `hnsw_grid.py` so each cell's knobs are
rechecked on read, but not the grid's other pre-flight table, `_CORPUS_BOUNDS`,
and not the workload at all. Measured on `main` 42d6736 with a real grid
(`--n-vectors 2000 --n-queries 50 --dim 64 --M 8,16 --ef-construction 64
--ef-search 16,64`) hand-edited to `backend: "qdrant"`, `n_vectors: 30`: the
reader printed the Pareto table and wrote the PNG at exit 0, while the writer
with the same flags refuses at exit 2 ("qdrant answers every query by
brute-force scan ..."). `n_vectors: -3`, `dim: 0`, `n_queries: 0` and
`top_k: 10 > n_vectors: 5` rendered at exit 0 too, though
`Workload.__post_init__` refuses each.

The refusals below are red against that `main`; the renders are controls.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

_SCRIPT = REPO_ROOT / "scripts" / "plot_hnsw_frontier.py"
_SPEC = importlib.util.spec_from_file_location("plot_hnsw_frontier_workload_bounds", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
frontier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frontier)

# Imported after the script, which put `scripts/` on `sys.path`.
from hnsw_grid import run_grid  # noqa: E402


def _grid(backend: str | None = "hnsw-sim", **workload: int) -> dict:
    # Knobs inside every backend's HNSW bounds, so only the workload can refuse.
    grid: dict = {
        "workload": {"n_vectors": 2000, "dim": 64, "n_queries": 50, "top_k": 10, "seed": 1},
        "cells": [
            {
                "run_id": "a",
                "M": 16,
                "ef_construction": 64,
                "ef_search": 40,
                "mean_recall_at_k": 0.96,
                "p50_ms": 0.1,
                "p95_ms": 0.16,
            },
            {
                "run_id": "b",
                "M": 16,
                "ef_construction": 64,
                "ef_search": 100,
                "mean_recall_at_k": 0.99,
                "p50_ms": 0.3,
                "p95_ms": 0.37,
            },
        ],
    }
    grid["workload"].update(workload)
    if backend is not None:
        grid["backend"] = backend
    return grid


def _run(tmp_path: Path, grid: dict, *extra: str) -> int:
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    return frontier.main([str(path), "--out-png", str(tmp_path / "o.png"), *extra])


def _assert_refused(tmp_path: Path, capsys, grid: dict, needle: str) -> None:
    assert _run(tmp_path, grid) == 2
    captured = capsys.readouterr()
    assert "is not a valid grid" in captured.err
    assert needle in captured.err
    # Refused before anything is drawn or tabulated.
    assert not (tmp_path / "o.png").exists()
    assert "Pareto frontier" not in captured.out


def test_a_qdrant_grid_under_the_corpus_floor_is_refused(tmp_path: Path, capsys) -> None:
    # The issue's repro: 30 x 64 float32 = 7680 B, under Qdrant's 10 KiB.
    _assert_refused(tmp_path, capsys, _grid("qdrant", n_vectors=30), "brute-force scan")


def test_a_qdrant_grid_one_vector_under_the_floor_is_refused(tmp_path: Path, capsys) -> None:
    # 39 x 64 = 2496 components, 64 short of the 2560 the floor needs.
    _assert_refused(tmp_path, capsys, _grid("qdrant", n_vectors=39), "brute-force scan")


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("n_vectors", -3, "n_vectors must be positive"),
        ("n_vectors", 0, "n_vectors must be positive"),
        ("dim", 0, "dim must be positive"),
        ("n_queries", 0, "n_queries must be positive"),
        ("top_k", 0, "top_k must be positive"),
        ("seed", -1, "seed must be a valid numpy seed"),
    ],
)
def test_a_workload_the_writer_refuses_is_refused(
    tmp_path: Path, capsys, field: str, value: int, needle: str
) -> None:
    _assert_refused(tmp_path, capsys, _grid(**{field: value}), needle)


def test_top_k_above_n_vectors_is_refused(tmp_path: Path, capsys) -> None:
    _assert_refused(
        tmp_path, capsys, _grid(n_vectors=5, top_k=10), "top_k (10) exceeds n_vectors (5)"
    )


def test_a_non_positive_workload_is_refused_without_a_backend_key(tmp_path: Path, capsys) -> None:
    # The `Workload` rule is every grid's, not a per-backend bound.
    _assert_refused(tmp_path, capsys, _grid(None, dim=0), "dim must be positive")


# --- controls -------------------------------------------------------------


def test_a_grid_the_writer_produced_still_renders(tmp_path: Path, capsys) -> None:
    run_grid(
        n_vectors=200,
        n_queries=20,
        dim=16,
        top_k=5,
        seed=1,
        M_values=[8, 16],
        ef_construction_values=[50],
        ef_search_values=[16, 32],
        out_dir=tmp_path / "grid",
    )
    capsys.readouterr()
    grid = json.loads((tmp_path / "grid" / "grid.json").read_text(encoding="utf-8"))
    assert _run(tmp_path, grid) == 0
    assert "Pareto frontier" in capsys.readouterr().out
    assert (tmp_path / "o.png").exists()


def test_a_small_corpus_renders_on_a_backend_with_no_corpus_floor(tmp_path: Path, capsys) -> None:
    # The floor is Qdrant's alone: hnsw-sim writes a 30-vector grid.
    assert _run(tmp_path, _grid("hnsw-sim", n_vectors=30)) == 0
    assert "Pareto frontier" in capsys.readouterr().out


def test_a_qdrant_grid_exactly_at_the_floor_renders(tmp_path: Path, capsys) -> None:
    # 40 x 64 float32 = 10240 B, the floor itself.
    assert _run(tmp_path, _grid("qdrant", n_vectors=40)) == 0
    assert "Pareto frontier" in capsys.readouterr().out


def test_a_title_only_workload_still_renders_under_title(tmp_path: Path, capsys) -> None:
    # #226: under `--title` only `top_k` is required, so there is no complete
    # workload to rebuild, and none is invented.
    grid = _grid()
    grid["workload"] = {"top_k": 10}
    assert _run(tmp_path, grid, "--title", "mine") == 0
    assert "Pareto frontier" in capsys.readouterr().out
