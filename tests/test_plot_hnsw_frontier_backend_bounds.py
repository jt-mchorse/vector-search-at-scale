"""`plot_hnsw_frontier.py` checks each cell's knobs against the grid's backend bounds (#266).

#257 floored every knob at 1 on read, the one bound all four backends share.
The per-backend upper and relative bounds that `hnsw_grid.py` runs before any
cell (`_HNSW_BOUNDS`) were not rechecked. Measured on `main` 2985ed0 with a
real pgvector grid (PG 17 + pgvector 0.8.7) whose fastest cell was hand-edited
to `M=5000, ef_search=99999`: exit 0 and

    Recommended defaults (knee at recall ≥ 0.950): M=5000 ef_construction=64 ef_search=99999

Each case below passes the floor of 1, so all are red against that `main`.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

_SCRIPT = REPO_ROOT / "scripts" / "plot_hnsw_frontier.py"
_SPEC = importlib.util.spec_from_file_location("plot_hnsw_frontier_backend_bounds", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
frontier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frontier)


def _grid(backend: str | None, *, M: int = 16, efc: int = 64, efs: int = 40) -> dict:
    # Cell 0 is the faster one and clears the floor, so it is the knee the
    # script would publish: the case the issue reproduced.
    grid: dict = {
        "workload": {"n_vectors": 2000, "dim": 32, "n_queries": 50, "top_k": 10, "seed": 1},
        "cells": [
            {
                "run_id": "a",
                "M": M,
                "ef_construction": efc,
                "ef_search": efs,
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
    if backend is not None:
        grid["backend"] = backend
    return grid


def _run(tmp_path: Path, grid: dict) -> int:
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    return frontier.main([str(path), "--out-png", str(tmp_path / "o.png")])


# One cell per violated bound. Every value is >= 1, so the #257 floor admits it.
OUT_OF_BOUNDS = [
    # pgvector: M in [2, 100], ef_construction in [4, 1000] and >= 2 * M,
    # ef_search in [1, 1000] (`backends/pgvector.py`, #188).
    ("pgvector", {"M": 5000, "efs": 99999}, "hnsw_m must be an int in [2, 100]"),
    ("pgvector", {"M": 1}, "hnsw_m must be an int in [2, 100]"),
    ("pgvector", {"efs": 1001}, "hnsw_ef_search must be an int in [1, 1000]"),
    ("pgvector", {"M": 8, "efc": 1001}, "hnsw_ef_construction must be an int in [4, 1000]"),
    ("pgvector", {"M": 50, "efc": 64}, "ef_construction >= 2 * m"),
    # qdrant: ef_construct >= 4 (#222).
    ("qdrant", {"efc": 3}, "hnsw_ef_construct must be an int >= 4"),
    # weaviate: maxConnections in [4, 2047], efConstruction >= 4 (#224).
    ("weaviate", {"M": 3}, "hnsw_max_connections must be an int in [4, 2047]"),
    ("weaviate", {"M": 2048}, "hnsw_max_connections must be an int in [4, 2047]"),
    ("weaviate", {"efc": 3}, "hnsw_ef_construction must be an int >= 4"),
]


@pytest.mark.parametrize(
    ("backend", "knobs", "message"),
    OUT_OF_BOUNDS,
    ids=[f"{b}-{'-'.join(f'{k}{v}' for k, v in kn.items())}" for b, kn, _ in OUT_OF_BOUNDS],
)
def test_a_cell_outside_its_backends_bounds_exits_2_and_is_not_recommended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str, knobs: dict, message: str
) -> None:
    assert _run(tmp_path, _grid(backend, **knobs)) == 2
    out, err = capsys.readouterr()
    assert "Recommended defaults" not in out
    assert "has an invalid grid cell: grid cell 0" in err
    assert f"outside {backend}'s HNSW bounds" in err
    assert message in err
    assert not (tmp_path / "o.png").exists()


@pytest.mark.parametrize("backend", [1, "stub", "PGVECTOR"])
def test_a_backend_hnsw_grid_cannot_write_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: object
) -> None:
    grid = _grid(None)
    grid["backend"] = backend
    assert _run(tmp_path, grid) == 2
    assert "is not one hnsw_grid.py writes" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["pgvector", "qdrant", "weaviate", "hnsw-sim"])
def test_an_in_bounds_grid_still_recommends_its_knee(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str
) -> None:
    """Control: the knobs a real grid of each backend uses are accepted unchanged."""
    assert _run(tmp_path, _grid(backend)) == 0
    assert "M=16 ef_construction=64 ef_search=40" in capsys.readouterr().out


def test_a_grid_without_a_backend_keeps_the_floor_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control: no `backend` names the bounds, so only the shared floor of 1 applies."""
    assert _run(tmp_path, _grid(None, M=5000, efs=99999)) == 0
    assert "M=5000" in capsys.readouterr().out


def test_hnsw_sim_has_no_bound_beyond_the_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control: hnsw-sim's validator asks only for positive ints, so a large M is its own."""
    assert _run(tmp_path, _grid("hnsw-sim", M=5000, efs=99999)) == 0
    capsys.readouterr()


def test_the_reader_runs_the_writers_bounds_table() -> None:
    """The reader imports `_HNSW_BOUNDS`; a copy would drift from the grid's pre-flight."""
    spec = importlib.util.spec_from_file_location(
        "hnsw_grid", REPO_ROOT / "scripts" / "hnsw_grid.py"
    )
    assert spec is not None
    assert spec.loader is not None
    hnsw_grid = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hnsw_grid)
    assert sorted(frontier._HNSW_BOUNDS) == sorted(hnsw_grid._HNSW_BOUNDS)


def test_a_real_shaped_grid_is_byte_identical_in_its_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control: an in-bounds pgvector grid prints the same table it did before."""
    grid = _grid("pgvector")
    assert _run(tmp_path, copy.deepcopy(grid)) == 0
    first = capsys.readouterr().out
    grid.pop("backend")
    assert _run(tmp_path, grid) == 0
    assert capsys.readouterr().out.replace(str(tmp_path), "") == first.replace(str(tmp_path), "")
