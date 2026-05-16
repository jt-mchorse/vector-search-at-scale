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
    main(args)
    with pytest.raises(FileExistsError):
        main(args)


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
