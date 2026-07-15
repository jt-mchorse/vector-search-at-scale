"""Smoke test for the `vector-bench run` CLI against the stub backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vector_bench.cli import main


def test_cli_run_stub_writes_json(tmp_path: Path, capsys) -> None:
    results = tmp_path / "results"
    rc = main(
        [
            "run",
            "--backend",
            "stub",
            "--n",
            "30",
            "--dim",
            "8",
            "--queries",
            "5",
            "--top-k",
            "3",
            "--run-id",
            "smoke-cli",
            "--results-dir",
            str(results),
        ]
    )
    assert rc == 0
    out_path = results / "smoke-cli.json"
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "stub"
    assert payload["mean_recall_at_k"] == pytest.approx(1.0)

    stdout = capsys.readouterr().out
    assert "stub" in stdout


def test_cli_run_rejects_duplicate_run_id(tmp_path: Path, capsys) -> None:
    # #83: a run-id collision must surface as a clean exit 2 (like the `load`
    # sibling #81), not the raw FileExistsError traceback at exit 1 this test
    # previously locked in.
    results = tmp_path / "results"
    args = [
        "run",
        "--backend",
        "stub",
        "--n",
        "10",
        "--queries",
        "3",
        "--top-k",
        "2",
        "--run-id",
        "dupcli",
        "--results-dir",
        str(results),
    ]
    assert main(args) == 0
    capsys.readouterr()
    rc = main(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "Traceback" not in err


def test_cli_run_invalid_workload_exits_two(tmp_path: Path, capsys) -> None:
    # #83: an invalid workload dimension (--n 0 -> Workload.__post_init__
    # ValueError) must surface as a clean exit 2, not a raw traceback at exit 1.
    rc = main(
        [
            "run",
            "--backend",
            "stub",
            "--n",
            "0",
            "--queries",
            "5",
            "--run-id",
            "badn",
            "--results-dir",
            str(tmp_path / "results"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "positive" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("extra_args", "needle"),
    [
        (["--n", "0", "--queries", "5"], "positive"),
        (["--n", "5", "--queries", "10", "--top-k", "20"], "exceeds"),
    ],
)
def test_cli_load_invalid_workload_exits_two(
    extra_args: list[str], needle: str, tmp_path: Path, capsys
) -> None:
    # Sibling of #83: `load` built its `Workload` OUTSIDE the run_under_load
    # try/except, so an invalid dimension (--n 0, or --top-k > --n) leaked a raw
    # traceback at exit 1 while the `run` sibling surfaced the identical
    # ValueError as a clean exit 2. `load` must honor the same contract.
    rc = main(
        [
            "load",
            "--backend",
            "stub",
            *extra_args,
            "--run-id",
            "badload",
            "--results-dir",
            str(tmp_path / "results"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert needle in err
    assert "Traceback" not in err


def test_cli_run_concurrency_over_one_exits_two(tmp_path: Path, capsys) -> None:
    # #83: `run` is single-shot serial (D-011); --concurrency > 1 makes
    # run_benchmark raise ValueError. It must surface as a clean exit 2 (the
    # error message points the operator at `vector-bench load`), not a raw
    # traceback at exit 1.
    rc = main(
        [
            "run",
            "--backend",
            "stub",
            "--n",
            "50",
            "--queries",
            "5",
            "--concurrency",
            "2",
            "--run-id",
            "badconc",
            "--results-dir",
            str(tmp_path / "results"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "concurrency" in err
    assert "Traceback" not in err


def test_cli_force_overwrites(tmp_path: Path) -> None:
    results = tmp_path / "results"
    args = [
        "run",
        "--backend",
        "stub",
        "--n",
        "10",
        "--queries",
        "3",
        "--top-k",
        "2",
        "--run-id",
        "dup-force",
        "--results-dir",
        str(results),
    ]
    main(args)
    main([*args, "--force"])  # should not raise


def _load_args(results: Path, concurrency: str, run_id: str) -> list[str]:
    return [
        "load",
        "--backend",
        "stub",
        "--n",
        "20",
        "--dim",
        "8",
        "--queries",
        "5",
        "--concurrency",
        concurrency,
        "--run-id",
        run_id,
        "--results-dir",
        str(results),
        "--force",
    ]


def test_cli_load_duplicate_concurrency_exits_two(tmp_path: Path, capsys) -> None:
    # #81: duplicate concurrency levels would silently clobber a per-cell
    # c<NNN>.json (D-007). The CLI must reject with a clean exit 2, not run to a
    # lossy exit-0 success, and write nothing for the rejected run.
    results = tmp_path / "results"
    rc = main(_load_args(results, "1,10,10", "dup-load"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "distinct" in err
    assert "Traceback" not in err
    assert not (results / "dup-load").exists()


def test_cli_load_non_positive_concurrency_exits_two(tmp_path: Path, capsys) -> None:
    # #81: a non-positive level made run_under_load raise a ValueError that
    # _do_load did not catch, escaping as a raw traceback at exit 1. It must
    # surface as a clean exit 2 like the other CLI input errors.
    results = tmp_path / "results"
    rc = main(_load_args(results, "0,1", "nonpos-load"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "positive" in err
    assert "Traceback" not in err


def test_cli_load_valid_distinct_levels_runs(tmp_path: Path) -> None:
    # Regression: valid distinct levels still produce one per-cell file each.
    results = tmp_path / "results"
    rc = main(_load_args(results, "1,10", "ok-load"))
    assert rc == 0
    out_dir = results / "ok-load"
    assert (out_dir / "c001.json").exists()
    assert (out_dir / "c010.json").exists()
    assert (out_dir / "matrix.json").exists()


def test_cli_load_rejects_duplicate_run_id(tmp_path: Path, capsys) -> None:
    # #93: a run-id collision (matrix.json already present without --force) made
    # run_under_load raise FileExistsError, which _do_load did NOT catch (only
    # ValueError), escaping as a raw traceback at exit 1. It must surface as a
    # clean exit 2 with "already exists", exactly like the `run` sibling
    # (test_cli_run_rejects_duplicate_run_id, #83) — completing the #81/#83
    # exit-code contract for the `load` collision seam.
    results = tmp_path / "results"
    # NB: _load_args appends --force (which would overwrite, not collide), so
    # build args WITHOUT it to exercise the collision path.
    args = [a for a in _load_args(results, "1,2", "dupload") if a != "--force"]
    assert main(args) == 0
    capsys.readouterr()
    rc = main(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "Traceback" not in err


def test_cli_run_unwritable_results_dir_exits_two(tmp_path: Path, capsys) -> None:
    # #101: `run` writes its result JSON through atomic_write_text, so an
    # unwritable --results-dir (here a path whose parent component is a FILE →
    # NotADirectoryError) raised a general OSError the (ValueError, FileExistsError)
    # clause missed, leaking a raw traceback at exit 1. It must surface as a clean
    # exit 2 (write-seam sibling of #98/#99).
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = main(
        [
            "run",
            "--backend",
            "stub",
            "--n",
            "20",
            "--dim",
            "8",
            "--queries",
            "5",
            "--run-id",
            "x",
            "--results-dir",
            str(blocker / "sub"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_cli_load_unwritable_results_dir_exits_two(tmp_path: Path, capsys) -> None:
    # #101: the `load` sibling of the above — an unwritable --results-dir raises a
    # general OSError from run_under_load's write that the FileExistsError-only
    # clause missed. Must land as a clean exit 2, not a raw traceback.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = main(_load_args(blocker / "sub", "1,2", "x"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err
