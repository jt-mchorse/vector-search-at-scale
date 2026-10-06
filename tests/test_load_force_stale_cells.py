"""A `load --force` rerun replaces the run's cells, and cost_table refuses a stale c001 (#182).

`dump_load_matrix_json` wrote only the new run's cells, so a rerun at
`--concurrency 10,20` over one at `1,10` left the old `c001.json` (n=500,
dim=64) beside a matrix listing [10, 20] (n=2000, dim=32), and
`cost_table.py` amortized all nine rows over the superseded run's qps.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import cost_table  # noqa: E402


def _load(results: Path, *args: str) -> None:
    r = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "vector_bench.cli",
            "load",
            "--backend",
            "stub",
            "--queries",
            "50",
            "--run-id",
            "r1",
            "--results-dir",
            str(results),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stderr[-800:]


def _cost_table(results: Path, out: Path) -> tuple[int, str]:
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = cost_table.main(["--results-dir", str(results), "--run-id", "r1", "--out", str(out)])
    return rc, err.getvalue()


def test_a_force_rerun_leaves_only_its_own_cells(tmp_path: Path) -> None:
    _load(tmp_path, "--n", "500", "--concurrency", "1,10")
    (tmp_path / "r1" / "notes.txt").write_text("not a cell")
    _load(tmp_path, "--n", "2000", "--dim", "32", "--concurrency", "10,20", "--force")
    names = sorted(p.name for p in (tmp_path / "r1").iterdir())
    assert names == ["c010.json", "c020.json", "matrix.json", "notes.txt"]


def test_cost_table_refuses_a_c001_the_matrix_does_not_list(tmp_path: Path) -> None:
    # A directory polluted before the fix: a stale c001.json, a matrix without 1.
    run = tmp_path / "r1"
    _load(tmp_path, "--n", "500", "--concurrency", "1,10")
    stale = (run / "c001.json").read_text()
    _load(tmp_path, "--n", "2000", "--dim", "32", "--concurrency", "10,20", "--force")
    (run / "c001.json").write_text(stale)
    rc, err = _cost_table(tmp_path, tmp_path / "cost.md")
    assert rc == 2
    assert "is not part of this run" in err
    assert "[10, 20]" in err
    assert not (tmp_path / "cost.md").exists()


def test_a_run_with_concurrency_one_still_builds_the_table(tmp_path: Path) -> None:
    _load(tmp_path, "--n", "500", "--concurrency", "1,10")
    rc, err = _cost_table(tmp_path, tmp_path / "cost.md")
    assert rc == 0, err
    qps = json.loads((tmp_path / "r1" / "c001.json").read_text())["throughput_qps"]
    assert f"{qps:.1f}" in (tmp_path / "cost.md").read_text()
