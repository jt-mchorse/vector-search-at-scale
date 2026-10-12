"""`run` and `load` refuse an unwritable results directory before any work (#207).

Both pre-flighted a results COLLISION "so the operator [doesn't] pay the
wall-clock of the workload only to discover the destination is locked", and
neither pre-flighted an UNWRITABLE destination. Measured on main with a counting
`StubBackend` (2,000 x 32, 200 queries):

    run  results_dir=<a file>          FileExistsError     after ingest=1 queries=200
    load results_dir=<a file>/sub      NotADirectoryError  after ingest=1 queries=400
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import Workload, run_benchmark
from vector_bench.load import run_under_load

ROOT = Path(__file__).resolve().parent.parent
CALLS: dict[str, int] = {}


class _Counting(StubBackend):
    def ingest(self, vectors, ids):  # type: ignore[no-untyped-def]
        CALLS["ingest"] += 1
        return super().ingest(vectors, ids)

    def query(self, vector, k):  # type: ignore[no-untyped-def]
        CALLS["query"] += 1
        return super().query(vector, k)


@pytest.fixture(autouse=True)
def _reset() -> None:
    CALLS.update(ingest=0, query=0)


W = Workload(n_vectors=500, dim=16, n_queries=20, top_k=5, seed=1, concurrency=1)


def _bad_dirs(tmp_path: Path) -> list[Path]:
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    return [blocker, blocker / "sub"]


@pytest.mark.parametrize("which", [0, 1], ids=["a-file", "under-a-file"])
def test_run_refuses_before_any_ingest(tmp_path: Path, which: int) -> None:
    with pytest.raises((NotADirectoryError, FileExistsError)):
        run_benchmark(
            _Counting(), W, run_id="r1", results_dir=_bad_dirs(tmp_path)[which], force=True
        )
    assert CALLS == {"ingest": 0, "query": 0}


@pytest.mark.parametrize("which", [0, 1], ids=["a-file", "under-a-file"])
def test_load_refuses_before_any_ingest(tmp_path: Path, which: int) -> None:
    with pytest.raises((NotADirectoryError, FileExistsError)):
        run_under_load(
            _Counting(),
            W,
            run_id="r1",
            concurrency_levels=(1, 4),
            results_dir=_bad_dirs(tmp_path)[which],
            force=True,
        )
    assert CALLS == {"ingest": 0, "query": 0}


def test_a_good_directory_still_runs_and_writes(tmp_path: Path) -> None:
    run_benchmark(_Counting(), W, run_id="r1", results_dir=tmp_path / "res")
    assert CALLS["ingest"] == 1
    assert json.loads((tmp_path / "res" / "r1.json").read_text(encoding="utf-8"))["run_id"] == "r1"


def test_write_json_false_never_checks_the_path(tmp_path: Path) -> None:
    run_benchmark(_Counting(), W, run_id="r1", results_dir=_bad_dirs(tmp_path)[0], write_json=False)
    assert CALLS["ingest"] == 1


def test_the_cli_exits_2_naming_the_path(tmp_path: Path) -> None:
    bad = _bad_dirs(tmp_path)[0]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "vector_bench.cli",
            "run",
            "--backend",
            "stub",
            "--n",
            "200",
            "--run-id",
            "r1",
            "--results-dir",
            str(bad),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 2, proc.stderr
    assert str(bad) in proc.stderr


def test_check_writable_leaves_nothing_behind(tmp_path: Path) -> None:
    from vector_bench.io_utils import check_writable

    target = tmp_path / "sub" / "r.json"
    check_writable(target)
    assert not target.exists()
    assert list((tmp_path / "sub").iterdir()) == []
    with pytest.raises(IsADirectoryError):
        check_writable(tmp_path)
