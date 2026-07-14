"""Tests for ``scripts/plot_latency.py``.

The script isn't an importable package module, so we load it by path. The
focus here is ``_load_matrix``'s canonicalization contract: cells must come
back ascending by concurrency regardless of on-disk order, so the plot path
(which consumes them in array order on a log x-axis) draws a monotone curve
rather than a backtracking zig-zag (#49).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, dump_load_matrix_json

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "plot_latency", REPO_ROOT / "scripts" / "plot_latency.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_latency)


def _cell(concurrency: int) -> LoadCell:
    return LoadCell(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        concurrency=concurrency,
        ingest_seconds=1.0,
        # Latency rises with concurrency so an unsorted plot is visibly wrong.
        query_latency=LatencyStats(
            p50_ms=float(concurrency),
            p95_ms=float(concurrency * 2),
            p99_ms=float(concurrency * 3),
            max_ms=float(concurrency * 4),
        ),
        mean_recall_at_k=0.9,
        throughput_qps=float(concurrency * 10),
        started_at="2026-06-23T00:00:00Z",
        git_sha=None,
    )


def test_load_matrix_orders_cells_by_concurrency(tmp_path: Path) -> None:
    # Build a matrix whose cells are in DESCENDING concurrency order (the shape
    # produced by `--concurrency 100,10,1`), persist it through the real
    # serializer, then reload via the script's `_load_matrix`.
    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(100), _cell(10), _cell(1)),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)

    loaded = plot_latency._load_matrix(tmp_path / "matrix.json")

    # Pre-fix the cells came back [100, 10, 1] (on-disk order); they must be
    # canonicalized ascending so the log-x plot line doesn't backtrack.
    assert [c.concurrency for c in loaded.cells] == [1, 10, 100]
    # The p50 series the plotter derives is then monotonically non-decreasing.
    assert [c.query_latency.p50_ms for c in loaded.cells] == [1.0, 10.0, 100.0]


def test_main_missing_matrix_path_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A missing operator-supplied matrix.json must translate to a clean exit 2
    # with a stderr message, matching the sibling `plot_hnsw_frontier.py` — not
    # escape as a raw FileNotFoundError traceback (exit 1). (#83/#84 contract.)
    missing = tmp_path / "does_not_exist" / "matrix.json"
    rc = plot_latency.main([str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err
    assert str(missing) in err


def test_main_reports_all_missing_paths(tmp_path: Path, capsys) -> None:
    # nargs="+" accepts several paths; every missing one is named before exit 2.
    m1 = tmp_path / "a.json"
    m2 = tmp_path / "b.json"
    rc = plot_latency.main([str(m1), str(m2)])
    assert rc == 2
    err = capsys.readouterr().err
    assert str(m1) in err
    assert str(m2) in err


def test_main_non_utf8_matrix_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A present-but-non-UTF-8 matrix file is bad operator input, the same class
    # as the missing-file case. `_load_matrix` does json.loads(read_text()), so a
    # non-UTF-8 byte raises UnicodeDecodeError (a ValueError subclass, NOT a
    # FileNotFoundError), which escaped the pre-check and leaked a raw traceback
    # at exit 1. It must translate to a clean exit 2 (sibling of llm-eval-harness#174).
    bad = tmp_path / "matrix.json"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")
    rc = plot_latency.main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid UTF-8" in err
    assert str(bad) in err


def test_main_malformed_json_matrix_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A present, valid-UTF-8-but-not-valid-JSON matrix file is bad operator input
    # at the same json.loads seam: it raises json.JSONDecodeError, which also
    # escaped the pre-check. It must translate to a clean exit 2, not leak.
    bad = tmp_path / "matrix.json"
    bad.write_text("{not valid json", encoding="utf-8")
    rc = plot_latency.main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert str(bad) in err


def test_main_unwritable_out_dir_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    import pytest

    pytest.importorskip("matplotlib")  # the write seam is only reached when plotting runs
    # The output dir is operator input too: an unwritable --out-dir must land as a
    # clean exit 2, not a raw OSError traceback after the table prints (write-seam
    # sibling of the input guards above and llm-eval-harness#158/#159). A path whose
    # parent component is a FILE makes _maybe_plot's mkdir raise NotADirectoryError.
    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(1), _cell(10)),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = plot_latency.main([str(tmp_path / "matrix.json"), "--out-dir", str(blocker / "sub")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not write" in err
    assert "Traceback" not in err
