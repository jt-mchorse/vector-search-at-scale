"""`plot_latency._load_matrix` judges the numbers the file holds (#241).

It used to coerce each cell's numerics with ``int()`` / ``float()`` before
`LoadCell` saw them, so `LoadCell`'s guards checked a laundered value: a string
or fractional ``concurrency`` loaded as an int, and a string ``throughput_qps``
loaded as a float. It also never asked whether two cells share a level, which
`run_under_load` refuses on the write side (#81) -- `render_table` takes the
first cell per level, so the second vanished from the table at exit 0.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, dump_load_matrix_json

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "plot_latency_raw_numerics", REPO_ROOT / "scripts" / "plot_latency.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_latency)

WORKLOAD = Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1)


def _cell(concurrency: int) -> LoadCell:
    return LoadCell(
        run_id="demo",
        backend="stub",
        workload=WORKLOAD,
        concurrency=concurrency,
        ingest_seconds=1.0,
        query_latency=LatencyStats(
            p50_ms=float(concurrency),
            p95_ms=float(concurrency * 2),
            p99_ms=float(concurrency * 3),
            max_ms=float(concurrency * 4),
        ),
        mean_recall_at_k=0.9,
        throughput_qps=float(concurrency * 10),
        started_at="2026-10-15T00:00:00Z",
        git_sha=None,
    )


def _write(tmp_path: Path, mutate) -> Path:
    matrix = LoadMatrix(
        run_id="demo", backend="stub", workload=WORKLOAD, cells=(_cell(1), _cell(10), _cell(20))
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)
    path = tmp_path / "matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data["cells"][1])  # the concurrency-10 cell
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("concurrency", "10"),  # loaded as 10
        ("concurrency", "1"),  # loaded as 1: collided with the c=1 cell and was dropped
        ("concurrency", 10.9),  # truncated to 10
        ("concurrency", 10.0),
        ("throughput_qps", "100"),
        ("ingest_seconds", "1.0"),
        ("mean_recall_at_k", "0.9"),
    ],
)
def test_a_value_the_writer_would_refuse_exits_2(
    tmp_path: Path, capsys, field: str, value: object
) -> None:
    path = _write(tmp_path, lambda c: c.__setitem__(field, value))
    rc = plot_latency.main([str(path), "--out-dir", str(tmp_path / "out")])
    out, err = capsys.readouterr()
    assert rc == 2, out
    assert "not a valid load matrix" in err
    assert field in err
    assert "| concurrency |" not in out  # no table implying the run is fine


def test_a_repeated_level_exits_2_instead_of_dropping_a_cell(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path, lambda c: c.__setitem__("concurrency", 1))
    rc = plot_latency.main([str(path), "--out-dir", str(tmp_path / "out")])
    out, err = capsys.readouterr()
    assert rc == 2, out
    assert "not a valid load matrix" in err
    assert "[1] appear more than once" in err
    assert not (tmp_path / "out").exists()


def test_a_written_matrix_still_loads_every_cell(tmp_path: Path) -> None:
    # The negative control: what `dump_load_matrix_json` writes is unchanged.
    path = _write(tmp_path, lambda c: None)
    loaded = plot_latency._load_matrix(path)
    assert [c.concurrency for c in loaded.cells] == [1, 10, 20]
    assert all(type(c.concurrency) is int for c in loaded.cells)
    assert [c.throughput_qps for c in loaded.cells] == [10.0, 100.0, 200.0]


def test_the_committed_matrix_renders_unchanged() -> None:
    matrix = plot_latency._load_matrix(REPO_ROOT / "results/load/stub-10k/matrix.json")
    from vector_bench.load import render_table

    assert render_table([matrix]) == (
        "| concurrency | stub p50 ms | stub p95 ms | stub p99 ms |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 0.612 | 0.675 | 0.730 |\n"
        "| 10 | 0.844 | 1.320 | 2.143 |\n"
        "| 100 | 1.740 | 4.297 | 5.472 |\n"
    )
