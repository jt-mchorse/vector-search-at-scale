"""`--run-id` must be one path component under `--results-dir` (#199).

On `main`, `load --run-id ..` wrote `matrix.json` and `c001.json` into the
parent of `--results-dir`, `run --run-id ''` wrote a hidden `.json`, and
`load --run-id team/baseline` exited 0 with a matrix that `plot_latency` then
could not chart (its `{run_id}_...png` named a missing directory).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.cli import main as cli_main
from vector_bench.harness import Workload, run_benchmark
from vector_bench.load import run_under_load

BAD_IDS = ["", ".", "..", "../escaped", "team/baseline", "a/../b"]
SMALL = ["--backend", "stub", "--n", "40", "--queries", "4", "--dim", "8", "--top-k", "3"]


def _files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("run_id", BAD_IDS)
@pytest.mark.parametrize("command", ["run", "load"])
def test_cli_refuses_a_run_id_that_is_not_one_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run_id: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    results = tmp_path / "nest" / "results"
    argv = [command, *SMALL, "--run-id", run_id, "--results-dir", str(results)]
    if command == "load":
        argv += ["--concurrency", "1"]

    rc = cli_main(argv)

    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: run_id must be a single path component"), err
    assert "--concurrency invalid" not in err
    assert _files_under(tmp_path) == [], "a refused run_id must write nothing anywhere"


@pytest.mark.parametrize("run_id", BAD_IDS)
def test_library_writers_raise(tmp_path: Path, run_id: str) -> None:
    workload = Workload(n_vectors=40, dim=8, n_queries=4, top_k=3, seed=1)
    with pytest.raises(ValueError, match="single path component"):
        run_benchmark(StubBackend(), workload, run_id=run_id, results_dir=tmp_path / "r")
    with pytest.raises(ValueError, match="single path component"):
        run_under_load(
            StubBackend(),
            workload,
            run_id=run_id,
            concurrency_levels=(1,),
            results_dir=tmp_path / "l",
        )
    assert _files_under(tmp_path) == []


def test_no_write_means_no_path_so_no_check(tmp_path: Path) -> None:
    """`write_json=False` never turns the run_id into a path."""
    workload = Workload(n_vectors=40, dim=8, n_queries=4, top_k=3, seed=1)
    result = run_benchmark(
        StubBackend(), workload, run_id="a/b", results_dir=tmp_path, write_json=False
    )
    assert result.run_id == "a/b"
    matrix = run_under_load(
        StubBackend(),
        workload,
        run_id="a/b",
        concurrency_levels=(1,),
        results_dir=tmp_path,
        write_json=False,
    )
    assert matrix.run_id == "a/b"
    assert _files_under(tmp_path) == []


@pytest.mark.parametrize("run_id", ["smoke-001", "x y", "v1.2", "...", ".hidden-ok"])
def test_ordinary_run_ids_still_run(tmp_path: Path, run_id: str) -> None:
    rc = cli_main(["run", *SMALL, "--run-id", run_id, "--results-dir", str(tmp_path / "r")])
    assert rc == 0
    assert (tmp_path / "r" / f"{run_id}.json").is_file()
    rc = cli_main(
        ["load", *SMALL, "--concurrency", "1", "--run-id", run_id]
        + ["--results-dir", str(tmp_path / "l")]
    )
    assert rc == 0
    assert (tmp_path / "l" / run_id / "matrix.json").is_file()
