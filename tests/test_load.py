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
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import (
    LoadCell,
    LoadMatrix,
    dump_load_matrix_json,
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


def test_run_under_load_rejects_duplicate_concurrency_levels(tmp_path: Path):
    # #81: duplicate levels both write the same per-cell c<NNN>.json
    # (last-writer-wins), silently losing a measured cell so the on-disk files
    # no longer round-trip the matrix (D-007 "no silent clobber"). Reject them
    # like the non-positive guard rather than clobber.
    with pytest.raises(ValueError, match="distinct"):
        run_under_load(
            StubBackend(),
            _wl(),
            run_id="dup",
            concurrency_levels=(1, 10, 10),
            results_dir=tmp_path,
        )
    # Nothing was written for the rejected run (fail before the per-cell dump).
    assert not (tmp_path / "dup").exists()


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


# ----------------------------------------------------------------------
# #39: observability-parity — `.to_dict()` field-by-field contract pins
# (no `dataclasses.asdict`) + `dump_load_matrix_json` package-level
# wrapper. Same shape as python-async-llm-pipelines #45.
# ----------------------------------------------------------------------


class TestLoadCellToDictContract:
    def test_field_set_is_pinned(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="cell_contract",
            concurrency_levels=(1,),
            results_dir=tmp_path,
            write_json=False,
        )
        cell = matrix.cells[0]
        d = cell.to_dict()
        assert sorted(d.keys()) == [
            "backend",
            "concurrency",
            "git_sha",
            "ingest_seconds",
            "mean_recall_at_k",
            "query_latency",
            "run_id",
            "started_at",
            "throughput_qps",
            "workload",
        ]
        # Nested shapes are owned by the nested classes' to_dict() pins.
        assert sorted(d["workload"].keys()) == [
            "concurrency",
            "dim",
            "n_queries",
            "n_vectors",
            "seed",
            "top_k",
        ]
        assert sorted(d["query_latency"].keys()) == ["max_ms", "p50_ms", "p95_ms", "p99_ms"]

    def test_to_json_alias_returns_same_payload(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="cell_alias",
            concurrency_levels=(1,),
            results_dir=tmp_path,
            write_json=False,
        )
        cell = matrix.cells[0]
        assert cell.to_json() == cell.to_dict()


class TestLoadMatrixToDictContract:
    def test_field_set_is_pinned(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="matrix_contract",
            concurrency_levels=(1, 4),
            results_dir=tmp_path,
            write_json=False,
        )
        d = matrix.to_dict()
        assert sorted(d.keys()) == ["backend", "cells", "run_id", "workload"]
        # `cells` is a list (not a tuple) so it round-trips through JSON.
        assert isinstance(d["cells"], list)
        assert len(d["cells"]) == 2
        # And each cell is the same shape as LoadCell.to_dict pins.
        assert sorted(d["cells"][0].keys()) == [
            "backend",
            "concurrency",
            "git_sha",
            "ingest_seconds",
            "mean_recall_at_k",
            "query_latency",
            "run_id",
            "started_at",
            "throughput_qps",
            "workload",
        ]

    def test_to_json_alias_returns_same_payload(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="matrix_alias",
            concurrency_levels=(1,),
            results_dir=tmp_path,
            write_json=False,
        )
        assert matrix.to_json() == matrix.to_dict()


class TestDumpLoadMatrixJson:
    def test_writes_round_trippable_per_cell_and_matrix(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="dump",
            concurrency_levels=(1, 2),
            results_dir=tmp_path,
            write_json=False,
        )
        out_dir = tmp_path / "explicit_dump"
        returned = dump_load_matrix_json(out_dir, matrix=matrix)
        assert returned == out_dir / "matrix.json"

        # All four files present.
        assert (out_dir / "matrix.json").exists()
        assert (out_dir / "c001.json").exists()
        assert (out_dir / "c002.json").exists()

        # Plain JSON round-trip — no `default=` fallback needed because
        # `to_dict()` returns native types only.
        m = json.loads((out_dir / "matrix.json").read_text(encoding="utf-8"))
        assert m["backend"] == "stub"
        assert len(m["cells"]) == 2
        cell = json.loads((out_dir / "c001.json").read_text(encoding="utf-8"))
        assert cell["concurrency"] == 1

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="ow",
            concurrency_levels=(1,),
            results_dir=tmp_path,
            write_json=False,
        )
        out_dir = tmp_path / "guarded"
        dump_load_matrix_json(out_dir, matrix=matrix)
        with pytest.raises(FileExistsError, match="already exists"):
            dump_load_matrix_json(out_dir, matrix=matrix)

    def test_force_overwrites(self, tmp_path: Path) -> None:
        matrix = run_under_load(
            StubBackend(),
            _wl(),
            run_id="force",
            concurrency_levels=(1,),
            results_dir=tmp_path,
            write_json=False,
        )
        out_dir = tmp_path / "force_dump"
        dump_load_matrix_json(out_dir, matrix=matrix)
        dump_load_matrix_json(out_dir, matrix=matrix, force=True)

    def test_run_under_load_routes_through_dump_wrapper(self, tmp_path: Path, monkeypatch) -> None:
        # Same proof-of-routing pattern as TestDumpBenchmarkJson —
        # ensures the inline per-cell + matrix.json writes were really
        # replaced (not duplicated alongside the wrapper call).
        calls: list[tuple[Path, LoadMatrix]] = []

        def fake_dump(out_dir, *, matrix, force=False):
            calls.append((Path(out_dir), matrix))
            return Path(out_dir) / "matrix.json"

        monkeypatch.setattr("vector_bench.load.dump_load_matrix_json", fake_dump)
        run_under_load(
            StubBackend(),
            _wl(),
            run_id="routed",
            concurrency_levels=(1, 2),
            results_dir=tmp_path,
        )
        assert len(calls) == 1
        assert calls[0][0] == tmp_path / "routed"
        assert isinstance(calls[0][1], LoadMatrix)


# ----------------------------------------------------------------------
# Throughput is measured from wall-clock, not assumed-linear (#47)
# ----------------------------------------------------------------------
# The old `throughput_qps = n_queries / (sum(latencies)/c)` divided the *sum*
# of overlapping per-query service times by the concurrency, baking in perfect
# linear scaling: QPS grew with `c` by construction even for a backend that
# gains nothing from it, and could exceed the backend's physical serialization
# ceiling. A backend that serializes every query behind one lock with a fixed
# service time can serve at most ~1/service_time QPS at ANY concurrency; the
# reported throughput must never exceed that ceiling.

_SERVICE_S = 0.005  # 5 ms fixed, fully serialized → ~200 QPS hard ceiling


class _SerializedBackend(StubBackend):
    """Stub whose queries are serialized behind one lock with a fixed cost.

    Models a single-connection engine that gets zero benefit from client
    concurrency, so its real throughput is bounded by ``1 / _SERVICE_S``
    regardless of how many workers drive it.
    """

    name = "serialized"

    def __init__(self) -> None:
        super().__init__()
        self._gate = threading.Lock()

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self._gate:
            time.sleep(_SERVICE_S)
            return super().query(vector, k)


def test_run_under_load_throughput_respects_serialization_ceiling(tmp_path: Path):
    workload = Workload(n_vectors=64, dim=16, n_queries=16, top_k=5, seed=1)
    ceiling_qps = 1.0 / _SERVICE_S
    matrix = run_under_load(
        _SerializedBackend(),
        workload,
        run_id="serialized_ceiling",
        concurrency_levels=(1, 4, 16),
        results_dir=tmp_path,
        write_json=False,
    )
    for cell in matrix.cells:
        assert cell.throughput_qps > 0.0
        # 15% headroom for perf_counter resolution / scheduling jitter. The old
        # assume-linear formula reported well above the ceiling at c=16
        # (a physically-impossible QPS); the measured wall-clock number cannot,
        # because the lock forces at least n_queries * _SERVICE_S of wall time.
        assert cell.throughput_qps <= ceiling_qps * 1.15, (
            f"c={cell.concurrency}: reported {cell.throughput_qps:.1f} QPS exceeds "
            f"the {ceiling_qps:.0f} QPS serialization ceiling"
        )


# ----------------------------------------------------------------------
# #57: LoadCell finiteness/range guard + no invalid-JSON Infinity token
# (load-matrix sibling of the BenchmarkResult guard in #55)
# ----------------------------------------------------------------------


def _make_cell(**overrides: object) -> LoadCell:
    base: dict[str, object] = {
        "run_id": "r",
        "backend": "stub",
        "workload": Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1),
        "concurrency": 4,
        "ingest_seconds": 0.5,
        "query_latency": LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0),
        "mean_recall_at_k": 0.9,
        "throughput_qps": 40.0,
        "started_at": "2026-06-26T00:00:00Z",
        "git_sha": None,
    }
    base.update(overrides)
    return LoadCell(**base)  # type: ignore[arg-type]


class TestLoadCellFinitenessGuard:
    def test_valid_cell_dumps_to_strict_json(self) -> None:
        cell = _make_cell(throughput_qps=1234.0)
        parsed = json.loads(json.dumps(cell.to_dict()))  # strict: raises on Infinity/NaN
        assert parsed["throughput_qps"] == 1234.0

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), -1.0])
    def test_rejects_non_finite_or_negative_throughput(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"throughput_qps must be a finite number >= 0"):
            _make_cell(throughput_qps=bad)

    @pytest.mark.parametrize("bad", [float("inf"), float("nan"), -0.001])
    def test_rejects_non_finite_or_negative_ingest_seconds(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"ingest_seconds must be a finite number >= 0"):
            _make_cell(ingest_seconds=bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.5])
    def test_rejects_out_of_range_mean_recall(self, bad: float) -> None:
        with pytest.raises(
            ValueError, match=r"mean_recall_at_k must be a finite number in \[0, 1\]"
        ):
            _make_cell(mean_recall_at_k=bad)


class TestRunUnderLoadDegenerateQueryPhase:
    def test_non_positive_query_elapsed_raises(self, tmp_path: Path, monkeypatch) -> None:
        # Pin perf_counter to a constant so the query-phase wall-clock is 0: the
        # guard must fail loud instead of fabricating inf throughput.
        import vector_bench.load as load_mod

        monkeypatch.setattr(load_mod.time, "perf_counter", lambda: 500.0)
        w = Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1)
        with pytest.raises(
            ValueError,
            match=r"non-positive .* query-phase wall-clock|query phase finished in a non-positive",
        ):
            run_under_load(
                StubBackend(),
                w,
                run_id="degenerate-load",
                concurrency_levels=(1,),
                results_dir=tmp_path,
                write_json=False,
            )
