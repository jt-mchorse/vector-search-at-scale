"""Hermetic tests for the latency-under-load study.

Exercises the stub backend across concurrency levels, asserts that:
- Per-cell latency aggregates exist for every level requested.
- Recall is 1.0 against the stub (it IS the ground truth).
- One JSON per concurrency cell is written plus a matrix.json index.
- Idempotency: re-running with the same run_id refuses unless force=True.
- Concurrency validation rejects empties and non-positives.
- `render_table` produces a markdown table with the expected shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import Workload
from vector_bench.load import (
    LoadMatrix,
    render_table,
    run_under_load,
)


def _wl() -> Workload:
    return Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1)


def test_run_under_load_emits_one_cell_per_concurrency_level(tmp_path: Path):
    matrix = run_under_load(
        StubBackend(),
        _wl(),
        run_id="cells_smoke",
        concurrency_levels=(1, 4, 16),
        results_dir=tmp_path,
    )
    assert isinstance(matrix, LoadMatrix)
    assert len(matrix.cells) == 3
    assert [c.concurrency for c in matrix.cells] == [1, 4, 16]
    for cell in matrix.cells:
        assert cell.backend == "stub"
        assert cell.workload.n_vectors == 64
        assert cell.query_latency.p50_ms >= 0.0
        assert cell.query_latency.p95_ms >= cell.query_latency.p50_ms
        assert cell.query_latency.p99_ms >= cell.query_latency.p95_ms
        assert cell.mean_recall_at_k == pytest.approx(1.0)


def test_run_under_load_writes_per_cell_json_and_matrix(tmp_path: Path):
    run_under_load(
        StubBackend(),
        _wl(),
        run_id="files",
        concurrency_levels=(1, 2),
        results_dir=tmp_path,
    )
    out_dir = tmp_path / "files"
    assert (out_dir / "c001.json").exists()
    assert (out_dir / "c002.json").exists()
    assert (out_dir / "matrix.json").exists()

    matrix = json.loads((out_dir / "matrix.json").read_text())
    assert matrix["backend"] == "stub"
    assert len(matrix["cells"]) == 2
    cell_data = json.loads((out_dir / "c001.json").read_text())
    assert cell_data["concurrency"] == 1


def test_run_under_load_idempotency_refuses_overwrite(tmp_path: Path):
    run_under_load(
        StubBackend(),
        _wl(),
        run_id="overwrite",
        concurrency_levels=(1,),
        results_dir=tmp_path,
    )
    with pytest.raises(FileExistsError):
        run_under_load(
            StubBackend(),
            _wl(),
            run_id="overwrite",
            concurrency_levels=(1,),
            results_dir=tmp_path,
        )


def test_run_under_load_force_overwrites(tmp_path: Path):
    run_under_load(
        StubBackend(),
        _wl(),
        run_id="force",
        concurrency_levels=(1,),
        results_dir=tmp_path,
    )
    # Re-run with --force should not raise.
    run_under_load(
        StubBackend(),
        _wl(),
        run_id="force",
        concurrency_levels=(1, 4),  # different shape; should work
        results_dir=tmp_path,
        force=True,
    )
    # Re-read; new shape persisted.
    matrix = json.loads((tmp_path / "force" / "matrix.json").read_text())
    assert len(matrix["cells"]) == 2


def test_run_under_load_rejects_empty_concurrency_levels(tmp_path: Path):
    with pytest.raises(ValueError, match="at least one"):
        run_under_load(
            StubBackend(),
            _wl(),
            run_id="bad",
            concurrency_levels=(),
            results_dir=tmp_path,
        )


def test_run_under_load_rejects_non_positive_concurrency(tmp_path: Path):
    with pytest.raises(ValueError, match="positive"):
        run_under_load(
            StubBackend(),
            _wl(),
            run_id="bad2",
            concurrency_levels=(1, 0),
            results_dir=tmp_path,
        )


def test_run_under_load_write_json_false_skips_filesystem(tmp_path: Path):
    matrix = run_under_load(
        StubBackend(),
        _wl(),
        run_id="no_write",
        concurrency_levels=(1,),
        results_dir=tmp_path,
        write_json=False,
    )
    assert isinstance(matrix, LoadMatrix)
    assert not (tmp_path / "no_write").exists()


def test_render_table_has_per_concurrency_row(tmp_path: Path):
    matrix = run_under_load(
        StubBackend(),
        _wl(),
        run_id="table",
        concurrency_levels=(1, 10),
        results_dir=tmp_path,
    )
    table = render_table([matrix])
    assert "concurrency" in table
    assert "stub p50 ms" in table
    assert "stub p95 ms" in table
    assert table.splitlines()[2].startswith("| 1 |")
    assert any(line.startswith("| 10 |") for line in table.splitlines())


def test_render_table_handles_no_matrices():
    assert "no matrices" in render_table([]).lower()


def test_cli_load_subcommand_runs_against_stub(tmp_path: Path, capsys):
    from vector_bench.cli import main as cli_main

    rc = cli_main(
        [
            "load",
            "--backend",
            "stub",
            "--n",
            "32",
            "--dim",
            "8",
            "--queries",
            "10",
            "--top-k",
            "5",
            "--concurrency",
            "1,2",
            "--run-id",
            "cli_smoke",
            "--results-dir",
            str(tmp_path),
            "--render-table",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # The CLI dumps the matrix JSON, then a blank line, then the markdown table.
    json_blob = out.split("\n\n", 1)[0]
    parsed = json.loads(json_blob)
    assert parsed["backend"] == "stub"
    assert len(parsed["cells"]) == 2
    assert (tmp_path / "cli_smoke" / "matrix.json").exists()


def test_cli_load_rejects_bad_concurrency_string(capsys):
    from vector_bench.cli import main as cli_main

    rc = cli_main(
        [
            "load",
            "--backend",
            "stub",
            "--n",
            "32",
            "--queries",
            "10",
            "--concurrency",
            "a,b",
            "--run-id",
            "nope",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "comma-separated" in err
