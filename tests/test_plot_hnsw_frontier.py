"""Tests for ``scripts/plot_hnsw_frontier.py``'s grid-cell numeric guard (#111).

The script isn't an importable package module, so we load it by path (same as
``test_plot_latency.py``). Unlike ``plot_latency``, this plotter reads
``grid.json`` cells as plain dicts and consumes their numeric fields directly —
so a boolean/non-numeric/non-finite cell value would otherwise fabricate a
benchmark row, crash ``_dominates`` with a raw ``TypeError``, or render ``nan``.
This is the value-guard sibling of ``plot_latency``'s ``_reject_bool_numeric``
(#109/#110); the checks below pin the exit-2 bad-input contract for it.
"""

from __future__ import annotations

import copy
import importlib.util
import json as _json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "plot_hnsw_frontier", REPO_ROOT / "scripts" / "plot_hnsw_frontier.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_hnsw_frontier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_hnsw_frontier)


def _grid() -> dict:
    """A minimal but valid grid.json shape: two cells + a workload block."""
    return {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 64, "dim": 16, "n_queries": 20, "top_k": 5, "seed": 1},
        "cells": [
            {
                "run_id": "a",
                "M": 8,
                "ef_construction": 50,
                "ef_search": 16,
                "ingest_seconds": 0.1,
                "mean_recall_at_k": 0.90,
                "p50_ms": 0.08,
                "p95_ms": 0.09,
                "p99_ms": 0.10,
            },
            {
                "run_id": "b",
                "M": 32,
                "ef_construction": 200,
                "ef_search": 128,
                "ingest_seconds": 0.5,
                "mean_recall_at_k": 0.99,
                "p50_ms": 2.0,
                "p95_ms": 2.2,
                "p99_ms": 2.4,
            },
        ],
    }


def _write_grid_with(tmp_path: Path, mutate) -> Path:
    grid = copy.deepcopy(_grid())
    mutate(grid)
    grid_path = tmp_path / "grid.json"
    grid_path.write_text(_json.dumps(grid), encoding="utf-8")
    return grid_path


def test_main_valid_grid_renders_frontier_and_knee(tmp_path: Path, capsys) -> None:
    # Sanity: a clean grid still prints the frontier table + a recommended knee.
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    rc = plot_hnsw_frontier.main([str(grid_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Pareto frontier" in out
    assert "Recommended defaults" in out


@pytest.mark.parametrize("field", ["mean_recall_at_k", "p50_ms", "p95_ms"])
def test_main_boolean_cell_numeric_exits_2_not_fabricated(
    tmp_path: Path, capsys, field: str
) -> None:
    # A JSON `true` at a consumed numeric cell field passes `float(True)==1.0`
    # (bool subclasses int) and would fabricate a perfect `1.000`/`1.00` on the
    # published frontier table — and can hijack the recommended-defaults knee.
    # The sibling of plot_latency's `_reject_bool_numeric` (#110); must exit 2.
    grid_path = _write_grid_with(tmp_path, lambda g: g["cells"][0].__setitem__(field, True))
    rc = plot_hnsw_frontier.main([str(grid_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid grid cell" in err
    assert "not a bool" in err
    assert "Traceback" not in err


def test_main_non_numeric_string_cell_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A non-numeric string at `p95_ms` reaches `_dominates`' `a_lat <= b_lat`
    # comparison and raised a raw `TypeError` (exit 1) before the guard. It must
    # translate to a clean exit 2 like every other bad-input class in this file.
    grid_path = _write_grid_with(tmp_path, lambda g: g["cells"][0].__setitem__("p95_ms", "abc"))
    rc = plot_hnsw_frontier.main([str(grid_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid grid cell" in err
    assert "must be a number" in err
    assert "Traceback" not in err


def test_main_non_finite_cell_exits_2_not_nan(tmp_path: Path, capsys) -> None:
    # A bare `NaN` token parses natively via json.loads and would render `nan` in
    # the published table — the non-finite sibling `LatencyStats.__post_init__`
    # rejects on the plot_latency side. Must exit 2, not print a fabricated `nan`.
    grid_path = tmp_path / "grid.json"
    grid = copy.deepcopy(_grid())
    grid["cells"][0]["p50_ms"] = float("nan")
    # json.dumps emits the bare `NaN` token by default, which json.loads accepts.
    grid_path.write_text(_json.dumps(grid), encoding="utf-8")
    rc = plot_hnsw_frontier.main([str(grid_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid grid cell" in err
    assert "must be finite" in err
    assert "Traceback" not in err


def test_main_missing_numeric_field_exits_2_not_keyerror(tmp_path: Path, capsys) -> None:
    # A cell missing a consumed numeric field reached `c["p95_ms"]` and raised a
    # raw `KeyError` (exit 1); the load-boundary guard turns it into a clean
    # exit 2, same bad-input contract.
    grid_path = _write_grid_with(tmp_path, lambda g: g["cells"][0].pop("mean_recall_at_k"))
    rc = plot_hnsw_frontier.main([str(grid_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid grid cell" in err
    assert "missing required numeric field" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("bad", [True, False])
def test_require_finite_number_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValueError, match=r"must be a number, not a bool"):
        plot_hnsw_frontier._require_finite_number(bad, "p95_ms")
