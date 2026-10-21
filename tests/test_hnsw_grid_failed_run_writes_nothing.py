"""A grid run that fails mid-grid writes no cell and keeps the previous grid (#258).

`run_grid` wrote each cell's JSON as it ran and `grid.json` last, so a rerun
that failed at cell N left the previous `grid.json` beside cells 1..N-1 holding
the new run's numbers. `plot_hnsw_frontier` reads only `grid.json`, so it
plotted the old grid while the cell files disagreed with it. Every cell is now
written after the last one succeeds -- `run_under_load`'s order.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from vector_bench.types import BackendError  # noqa: E402

SMALL = ["--n-vectors", "60", "--n-queries", "5", "--dim", "16", "--top-k", "5"]
AXES = ["--ef-construction", "50", "--ef-search", "16"]


def _fail_query_when(monkeypatch: pytest.MonkeyPatch, *, M: int, seed: int) -> None:
    """The cell with this `M`, in a run with this `seed`, raises from `query`."""
    real = hnsw_grid.make_backend

    def make(name: str, **kwargs: object) -> object:
        backend = real(name, **kwargs)
        if kwargs.get("M") == M and kwargs.get("seed") == seed:

            def query(*_a: object, **_k: object) -> object:
                raise BackendError("simulated server timeout")

            backend.query = query  # type: ignore[method-assign]
        return backend

    monkeypatch.setattr(hnsw_grid, "make_backend", make)


def _run(out: Path, M: str, seed: int) -> int:
    return hnsw_grid.main([*SMALL, *AXES, "--M", M, "--seed", str(seed), "--out-dir", str(out)])


def _snapshot(out: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def _seeds(out: Path) -> dict[str, int]:
    return {
        p.name: json.loads(p.read_text())["workload"]["seed"] for p in sorted(out.glob("*.json"))
    }


def test_the_issue_repro_keeps_the_previous_grid_and_its_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "g"
    assert _run(out, "8,16", seed=1) == 0
    before = _snapshot(out)

    _fail_query_when(monkeypatch, M=16, seed=2)
    assert _run(out, "8,16", seed=2) == 2

    assert _seeds(out) == {"M8_efc50_efs16.json": 1, "M16_efc50_efs16.json": 1, "grid.json": 1}
    assert _snapshot(out) == before


def test_a_failed_wider_rerun_leaves_no_new_cell_beside_the_old_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "g"
    assert _run(out, "8", seed=1) == 0
    before = _snapshot(out)

    _fail_query_when(monkeypatch, M=32, seed=2)
    assert _run(out, "8,16,32", seed=2) == 2

    assert _snapshot(out) == before


def test_a_first_run_that_fails_mid_grid_writes_no_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "g"
    _fail_query_when(monkeypatch, M=16, seed=1)

    assert _run(out, "8,16", seed=1) == 2

    assert list(out.glob("*.json")) == []


# --- controls: green before and after the fix ---------------------------------


def test_a_successful_rerun_replaces_every_cell_and_the_grid(tmp_path: Path) -> None:
    out = tmp_path / "g"
    assert _run(out, "8,16", seed=1) == 0
    assert _run(out, "8,16", seed=2) == 0

    assert _seeds(out) == {"M8_efc50_efs16.json": 2, "M16_efc50_efs16.json": 2, "grid.json": 2}


def test_a_failure_in_the_first_cell_keeps_the_previous_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "g"
    assert _run(out, "8,16", seed=1) == 0
    before = _snapshot(out)

    _fail_query_when(monkeypatch, M=8, seed=2)
    assert _run(out, "8,16", seed=2) == 2

    assert _snapshot(out) == before


def test_an_unwritable_cell_path_fails_before_any_workload_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deferring the writes keeps #207's pre-flight: no ingest pays for a refused write."""
    out = tmp_path / "g"
    (out / "M8_efc50_efs16.json").mkdir(parents=True)
    ingests: list[str] = []
    real = hnsw_grid.make_backend

    def make(name: str, **kwargs: object) -> object:
        backend = real(name, **kwargs)
        real_ingest = backend.ingest

        def ingest(*a: object, **k: object) -> object:
            ingests.append(name)
            return real_ingest(*a, **k)

        backend.ingest = ingest  # type: ignore[method-assign]
        return backend

    monkeypatch.setattr(hnsw_grid, "make_backend", make)

    assert _run(out, "8,16", seed=1) == 2

    assert ingests == []
    assert "could not write under" in capsys.readouterr().err
