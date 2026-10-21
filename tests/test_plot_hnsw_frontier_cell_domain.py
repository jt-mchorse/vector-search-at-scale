"""`plot_hnsw_frontier.py` range-checks each grid cell on read with the writer's rule (#257).

The reader checked type and finiteness only. A hand-edited cell with
`mean_recall_at_k: 9.7` was tabulated and published as the recommended default
at exit 0 (`recall=9.700`), and a negative `p95_ms` or an `M` of 0 went through
the same way. The writer refuses all of them: `BenchmarkResult` and
`LatencyStats` through `is_valid_number`, the knobs through each backend's HNSW
bounds. The comment that claimed a read-path check named `_coerce_number`, a
function that never existed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from vector_bench.harness import BenchmarkResult, LatencyStats, Workload  # noqa: E402

_SCRIPT = REPO_ROOT / "scripts" / "plot_hnsw_frontier.py"
_SPEC = importlib.util.spec_from_file_location("plot_hnsw_frontier_cell_domain", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
frontier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frontier)


def _grid() -> dict:
    # Cell "a" is the faster one, so a bad value placed on it is the knee the
    # script would publish: the case the issue reproduced.
    return {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 64, "dim": 16, "n_queries": 20, "top_k": 5, "seed": 1},
        "cells": [
            {
                "run_id": "a",
                "M": 16,
                "ef_construction": 50,
                "ef_search": 16,
                "mean_recall_at_k": 0.96,
                "p50_ms": 1.0,
                "p95_ms": 1.2,
            },
            {
                "run_id": "b",
                "M": 32,
                "ef_construction": 200,
                "ef_search": 128,
                "mean_recall_at_k": 0.99,
                "p50_ms": 2.0,
                "p95_ms": 2.2,
            },
        ],
    }


def _run(tmp_path: Path, capsys, field: str, value: object) -> tuple[int, str, str]:
    grid = copy.deepcopy(_grid())
    grid["cells"][0][field] = value
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    rc = frontier.main([str(path)])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mean_recall_at_k", 9.7),
        ("mean_recall_at_k", -0.1),
        ("mean_recall_at_k", 1.0000001),
        ("p50_ms", -3.0),
        ("p95_ms", -2.0),
        ("M", 0),
        ("ef_construction", 0),
        ("ef_search", -16),
    ],
)
def test_out_of_domain_cell_exits_2_and_publishes_nothing(
    tmp_path: Path, capsys, field: str, value: object
) -> None:
    rc, out, err = _run(tmp_path, capsys, field, value)
    assert rc == 2
    assert "has an invalid grid cell" in err
    assert field in err
    assert "Traceback" not in err
    assert "Recommended defaults" not in out


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mean_recall_at_k", 0.0),
        ("mean_recall_at_k", 1.0),
        ("p50_ms", 0.0),
        ("p95_ms", 0),
        ("M", 1),
    ],
)
def test_the_domain_endpoints_are_still_accepted(
    tmp_path: Path, capsys, field: str, value: object
) -> None:
    """Control: the closed endpoints are in the writer's domain, so they stay legal."""
    rc, _, err = _run(tmp_path, capsys, field, value)
    assert rc == 0, err


def test_a_valid_grid_still_publishes_its_knee(tmp_path: Path, capsys) -> None:
    rc, out, _ = _run(tmp_path, capsys, "mean_recall_at_k", 0.96)
    assert rc == 0
    assert "M=16 ef_construction=50 ef_search=16" in out
    assert "recall=0.960" in out


# One table, run through both the reader and the writer, so the reader's domain
# is derived rather than restated (the shape `test_plot_hnsw_frontier.py` uses for
# `--recall-floor`). Bools and non-numbers are pinned by that file already.
_PROBES: tuple[float, ...] = (
    0.0,
    -0.0,
    0.5,
    1.0,
    1.0000001,
    2.0,
    9.7,
    -0.1,
    -2.0,
    float("nan"),
    float("inf"),
)


def _reader_accepts(field: str, value: float) -> bool:
    grid = copy.deepcopy(_grid())
    grid["cells"][0][field] = value
    try:
        frontier._validate_cells(grid)
    except ValueError:
        return False
    return True


def _writer_accepts(field: str, value: float) -> bool:
    latency = {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0, "max_ms": 4.0}
    recall = 0.5
    if field == "mean_recall_at_k":
        recall = value
    else:
        latency[field] = value
    try:
        BenchmarkResult(
            run_id="r",
            backend="stub",
            workload=Workload(n_vectors=10, dim=4, n_queries=2, top_k=3),
            ingest_seconds=1.0,
            ingest_rows_per_sec=1.0,
            query_latency=LatencyStats(**latency),
            mean_recall_at_k=recall,
            started_at="t",
            git_sha=None,
            cost_per_query_usd=None,
        )
    except (ValueError, TypeError):
        return False
    return True


@pytest.mark.parametrize("field", frontier._NUMERIC_CELL_FIELDS)
@pytest.mark.parametrize("value", _PROBES, ids=repr)
def test_the_reader_domain_matches_the_writer(field: str, value: float) -> None:
    assert _reader_accepts(field, value) == _writer_accepts(field, value), (
        f"grid cell {field}={value!r}: plot_hnsw_frontier and BenchmarkResult/LatencyStats "
        "disagree; the reader must accept exactly what the writer can produce"
    )


@pytest.mark.parametrize("field", frontier._NUMERIC_CELL_FIELDS)
def test_the_probe_table_covers_both_sides(field: str) -> None:
    """Anti-vacuous: an all-accepted or all-rejected table would make the parity hold vacuously."""
    accepted = [v for v in _PROBES if _writer_accepts(field, v)]
    rejected = [v for v in _PROBES if not _writer_accepts(field, v)]
    assert len(accepted) >= 4, accepted
    assert len(rejected) >= 3, rejected
    # The arms the issue reproduced: each is in-range for the type guard and
    # out of range for the writer.
    assert not _writer_accepts(field, -2.0)
    if field == "mean_recall_at_k":
        assert not _writer_accepts(field, 9.7)


def test_the_read_path_check_the_comment_names_exists() -> None:
    """The `--recall-floor` comment names the read-path check; it must be real (#257).

    It used to say "re-checked by `_coerce_number` on the read path". No such
    function existed, and nothing on the read path checked the range.
    """
    comments = " ".join(
        line.strip().lstrip("#").strip()
        for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("#")
    )
    match = re.search(r"re-checked (?:on the read path )?by `(\w+)`", comments)
    assert match is not None, "the comment no longer names its read-path check"
    name = match.group(1)
    assert callable(getattr(frontier, name, None)), f"`{name}` is named but not defined"
    # And `_validate_cells` (the load-boundary guard `main` calls) really calls it.
    assert name in frontier._validate_cells.__code__.co_names
