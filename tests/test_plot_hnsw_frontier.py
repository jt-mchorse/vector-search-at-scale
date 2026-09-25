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


# ---------------------------------------------------------------------------
# #119: `--recall-floor` is operator input; `type=float` is not validation.
#
# The script guarded its file, its JSON, every consumed cell value and its
# output paths — all exit 2 — but not the one number those cell values are
# *compared against*. A NaN in the grid JSON exited 2 ("would render as `nan`
# in the published table"); a NaN on the command line silently changed the
# published recommendation and exited 0.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "floor",
    ["nan", "inf", "-inf", "-1", "-0.0001", "1.0001", "5"],
)
def test_out_of_domain_recall_floor_exits_2(tmp_path: Path, capsys, floor: str) -> None:
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    # `--recall-floor=-1` (the `=` form) so argparse doesn't read a leading
    # `-` as an option prefix and reject it before the guard is reached.
    rc = plot_hnsw_frontier.main([str(grid_path), f"--recall-floor={floor}"])
    assert rc == 2, f"--recall-floor={floor} should exit 2"
    captured = capsys.readouterr()
    assert "--recall-floor must be a finite number in [0, 1]" in captured.err
    assert "Recommended defaults" not in captured.out
    assert "No grid cell achieves" not in captured.out


def test_negative_floor_no_longer_publishes_the_worst_cell(tmp_path: Path, capsys) -> None:
    """The concrete harm, not just the exit code.

    A negative floor admits *every* cell, so `min(p95)` picked the fastest and
    therefore worst-recall one. Against this fixture that is the 0.90 cell; in
    the committed `results/hnsw-grid/grid.json` it was `recall=0.102` — a
    "Recommended default" that finds the right neighbour one time in ten,
    reachable from a typo of `-0.95` for `0.95`.
    """
    grid_path = _write_grid_with(tmp_path, lambda g: None)

    # Pre-fix behaviour, reconstructed from the untouched library function so
    # the claim is measured rather than asserted: a negative floor really does
    # select the worst-recall cell.
    worst = plot_hnsw_frontier.recommended_defaults(_grid()["cells"], recall_floor=-1.0)
    best = plot_hnsw_frontier.recommended_defaults(_grid()["cells"], recall_floor=0.95)
    assert worst is not None
    assert best is not None
    assert worst["mean_recall_at_k"] < best["mean_recall_at_k"], (
        "fixture must contain a fast/low-recall cell for this test to mean anything"
    )

    rc = plot_hnsw_frontier.main([str(grid_path), "--recall-floor=-1"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "Recommended defaults" not in out, (
        f"a negative floor must not publish a recommendation "
        f"(it used to publish recall={worst['mean_recall_at_k']:.3f})"
    )


def test_unsatisfiable_floor_no_longer_advises_expanding_the_grid(tmp_path: Path, capsys) -> None:
    """The other harm. The else-branch told the operator to "expand the grid
    (higher ef_search)" — advice they can act on by spending real benchmark
    compute, and which no grid at any size can satisfy: every comparison
    against NaN is False, and recall is a proportion so nothing clears 5.0.
    """
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    for floor in ("nan", "5"):
        rc = plot_hnsw_frontier.main([str(grid_path), f"--recall-floor={floor}"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "expand the grid" not in out


@pytest.mark.parametrize("floor", ["0", "0.0", "0.5", "0.95", "1", "1.0"])
def test_in_domain_recall_floor_is_accepted(tmp_path: Path, capsys, floor: str) -> None:
    """The domain is `mean_recall_at_k`'s own, so both endpoints are valid:
    `0.0` is an explicit "no floor" and `1.0` a legitimate "exact recall only"
    request (which this fixture can't satisfy, and which correctly reports so).
    """
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    rc = plot_hnsw_frontier.main([str(grid_path), f"--recall-floor={floor}"])
    assert rc == 0, f"--recall-floor={floor} is in-domain and must be accepted"
    capsys.readouterr()


def test_default_floor_path_is_unchanged(tmp_path: Path, capsys) -> None:
    """Passing the default explicitly must be indistinguishable from omitting it.

    The original form of this arm also pinned the literal `≥ 0.95`, on the
    stated grounds that "the committed artifacts and the README's quoted knee
    depend on it". #150 checked that premise: **no committed artifact contains
    this sentence.** `results/` holds grid JSON, and the README's dependency is
    on the *knee row* (M / ef_construction / ef_search / recall / p95), which
    this fix does not touch. The README's prose "knee at recall ≥ 0.95" states
    the floor's value, which is still `0.95` and still correct.

    What the sentence now prints is `≥ 0.950`, because the floor renders at the
    same precision as the recall it is compared against — that is D-014's whole
    point, and rendering the two sides at `.2f` and `.3f` is what let a correct
    selection read backwards.

    #78 (which knee value to publish) remains a separate, maintainer-gated
    question, and this arm still guards the part that matters for it: the
    recommended cell itself is unchanged.
    """
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    rc_default = plot_hnsw_frontier.main([str(grid_path)])
    out_default = capsys.readouterr().out
    rc_explicit = plot_hnsw_frontier.main([str(grid_path), "--recall-floor=0.95"])
    out_explicit = capsys.readouterr().out
    assert rc_default == rc_explicit == 0
    assert out_default == out_explicit
    assert "Recommended defaults (knee at recall ≥ 0.950)" in out_default
    # The floor moved a decimal place; the selected cell did not. This is the
    # half #78 cares about. The values are this module's synthetic fixture's,
    # not the committed grid's — `_write_grid_with` builds its own.
    assert "M=32 ef_construction=200 ef_search=128" in out_default
    assert "recall=0.990" in out_default


# Values spanning `mean_recall_at_k`'s domain and its complement, as *floats*,
# because that is the only currency the two sides share: `--recall-floor` is
# `type=float`, so argparse has already coerced by the time the flag's guard
# runs. The bool and non-number rows are asserted separately below, on the
# dataclass alone, since a command line cannot express them.
_RECALL_DOMAIN_PROBES: tuple[float, ...] = (
    0.0,
    0.5,
    0.95,
    1.0,
    -0.0,
    -0.1,
    1.0000001,
    1.1,
    float("nan"),
    float("inf"),
    float("-inf"),
)


def _dataclass_accepts_recall(value: float) -> bool:
    """Does `BenchmarkResult.__post_init__` accept *value* for `mean_recall_at_k`?"""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from vector_bench.harness import BenchmarkResult, LatencyStats, Workload

    try:
        BenchmarkResult(
            run_id="r",
            backend="stub",
            workload=Workload(n_vectors=10, dim=4, n_queries=2, top_k=3),
            ingest_seconds=1.0,
            ingest_rows_per_sec=1.0,
            query_latency=LatencyStats(1.0, 2.0, 3.0, 4.0),
            mean_recall_at_k=value,
            started_at="t",
            git_sha=None,
            cost_per_query_usd=None,
        )
    except (ValueError, TypeError):
        return False
    return True


def _cli_accepts_recall_floor(tmp_path: Path, capsys, value: float) -> bool:
    """Does `--recall-floor=<value>` survive the flag's own domain guard?

    `=` rather than a space: argparse consumes `--recall-floor -0.1` as a flag.
    """
    grid_path = _write_grid_with(tmp_path, lambda g: None)
    rc = plot_hnsw_frontier.main([str(grid_path), f"--recall-floor={value!r}"])
    capsys.readouterr()
    return rc == 0


@pytest.mark.parametrize("value", _RECALL_DOMAIN_PROBES, ids=repr)
def test_the_cli_domain_matches_the_dataclass_contract(
    tmp_path: Path, capsys, value: float
) -> None:
    """Derive, don't restate: `--recall-floor`'s accepted domain must equal the
    one `BenchmarkResult.__post_init__` enforces for `mean_recall_at_k`, which
    is the field it is compared against.

    This used to be four `inspect.getsource` greps for the literal strings
    `"0.0 <= self.mean_recall_at_k <= 1.0"` and `"math.isfinite(...)"`, under a
    docstring that says "derive, don't restate" -- which is what a text grep
    cannot do. It pinned the guard's *spelling*, not its *domain*, so it went
    red the moment the identical rule moved behind `is_valid_number` (#139) and
    it could never have seen the hole that motivated the move: the literal
    `math.isfinite(x)` it required is exactly the expression that accepts
    `True`.

    Running both sides over one table is strictly stronger. It is also the only
    form that stays green across a refactor of either side and red across a
    change to either *domain*.
    """
    assert _cli_accepts_recall_floor(tmp_path, capsys, value) == _dataclass_accepts_recall(value), (
        f"--recall-floor={value!r} and BenchmarkResult(mean_recall_at_k={value!r}) "
        "disagree; the flag is compared against that field, so their domains "
        "must be identical"
    )


def test_the_recall_domain_probe_table_covers_both_sides() -> None:
    """Anti-vacuous: a table that were all-accepted or all-rejected would make
    the equality above hold while testing nothing.
    """
    accepted = [v for v in _RECALL_DOMAIN_PROBES if _dataclass_accepts_recall(v)]
    rejected = [v for v in _RECALL_DOMAIN_PROBES if not _dataclass_accepts_recall(v)]
    assert len(accepted) >= 4, f"expected several in-domain probes, got {accepted}"
    assert len(rejected) >= 4, f"expected several out-of-domain probes, got {rejected}"
    # Both endpoints are in-domain: `0.0` is an explicit "no floor", `1.0` an
    # "exact recall only" request. Just outside each is not.
    assert _dataclass_accepts_recall(0.0)
    assert _dataclass_accepts_recall(1.0)
    assert not _dataclass_accepts_recall(-0.1)
    assert not _dataclass_accepts_recall(1.0000001)


@pytest.mark.parametrize("value", [True, False, "0.5", None, b"0.5"], ids=repr)
def test_the_dataclass_rejects_what_a_command_line_cannot_express(value: object) -> None:
    """The rows the shared table above cannot carry, asserted on the dataclass.

    `--recall-floor` is `type=float`, so argparse rejects a non-number before
    the flag's guard ever runs -- the CLI side of these is closed by
    construction. The dataclass has no such coercion in front of it, and until
    #139 `math.isfinite(True)` let `mean_recall_at_k=True` through as a
    fabricated perfect recall that `to_dict()` then serialized as the JSON
    token `true`.
    """
    assert not _dataclass_accepts_recall(value)  # type: ignore[arg-type]
