"""Every key `plot_hnsw_frontier.py` reads is checked before it draws (#226).

#217's shape guard required `workload.top_k` and the three numeric cell fields.
The default title also reads `workload.{n_vectors,dim,n_queries}`, the frontier
is keyed by each cell's `run_id`, and the table and the knee line print `M`,
`ef_construction` and `ef_search`. A grid missing any of them was a `KeyError`
at exit 1 (for the knobs, after the PNG was saved), and `M: true` printed
`| True |` in the published table.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontier = _load("plot_hnsw_frontier")
hnsw_grid = _load("hnsw_grid")

_CELL = {
    "run_id": "a",
    "M": 8,
    "ef_construction": 50,
    "ef_search": 16,
    "mean_recall_at_k": 0.9,
    "p50_ms": 1.0,
    "p95_ms": 2.0,
}
_GRID = {
    "workload": {"n_vectors": 100, "dim": 8, "n_queries": 5, "top_k": 10, "seed": 1},
    "cells": [_CELL],
}


def _write(tmp_path: Path, mutate) -> Path:
    grid = copy.deepcopy(_GRID)
    mutate(grid)
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    return path


def _run(tmp_path: Path, grid: Path, *extra: str) -> int:
    return frontier.main([str(grid), "--out-png", str(tmp_path / "o.png"), *extra])


@pytest.mark.parametrize("key", ["n_vectors", "dim", "n_queries"])
def test_a_workload_without_a_title_key_exits_2(tmp_path: Path, capsys, key: str) -> None:
    grid = _write(tmp_path, lambda g: g["workload"].pop(key))
    assert _run(tmp_path, grid) == 2
    err = capsys.readouterr().err
    assert "is not a valid grid" in err
    assert key in err
    assert not (tmp_path / "o.png").exists()


def test_a_supplied_title_does_not_need_the_title_keys(tmp_path: Path, capsys) -> None:
    grid = _write(tmp_path, lambda g: g.__setitem__("workload", {"top_k": 10}))
    assert _run(tmp_path, grid, "--title", "mine") == 0
    assert "Pareto frontier" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", None),
        ("run_id", ["a"]),
        ("M", None),
        ("M", True),
        ("ef_construction", None),
        ("ef_construction", "50"),
        ("ef_search", None),
        ("ef_search", 16.0),
    ],
)
def test_a_bad_cell_key_exits_2_and_leaves_no_png(
    tmp_path: Path, capsys, field: str, value: object
) -> None:
    def mutate(g: dict) -> None:
        if value is None:
            g["cells"][0].pop(field)
        else:
            g["cells"][0][field] = value

    grid = _write(tmp_path, mutate)
    assert _run(tmp_path, grid) == 2
    err = capsys.readouterr().err
    assert "has an invalid grid cell" in err
    assert field in err
    assert "Traceback" not in err
    assert not (tmp_path / "o.png").exists()


def test_the_grid_the_writer_emits_still_renders(tmp_path: Path, capsys) -> None:
    out = tmp_path / "g"
    rc = hnsw_grid.main(
        [
            "--n-vectors",
            "100",
            "--n-queries",
            "5",
            "--dim",
            "8",
            "--M",
            "8",
            "--ef-construction",
            "16",
            "--ef-search",
            "16,32",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0
    assert _run(tmp_path, out / "grid.json") == 0
    assert "Pareto frontier" in capsys.readouterr().out
