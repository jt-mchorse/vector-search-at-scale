"""A backend setup error is a clean exit 2, not a traceback at exit 1 (#195).

`make_backend` sat outside every `try` in `run` and `load`, and a backend's
constructor raises `BackendError` (a `RuntimeError`) for a setup problem that is
the operator's input. Measured on `main`:

    vector-bench run --backend qdrant ...  (no extra / no QDRANT_URL)
      -> Traceback ... vector_bench.types.BackendError: QdrantBackend requires ...   exit 1
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vector_bench.cli as cli
from vector_bench.types import BackendError


@pytest.fixture
def failing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def make_backend(name: str, **_kw: object) -> object:
        raise BackendError(f"{name}Backend: pass url or set ITS_URL")

    monkeypatch.setattr(cli, "make_backend", make_backend)


def test_run_exits_2_with_one_error_line(
    failing_backend: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        ["run", "--backend", "qdrant", "--n", "10", "--run-id", "x", "--results-dir", str(tmp_path)]
    )
    assert rc == 2
    assert capsys.readouterr().err.strip() == "error: qdrantBackend: pass url or set ITS_URL"


def test_load_exits_2_with_one_error_line(
    failing_backend: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        [
            "load",
            "--backend",
            "weaviate",
            "--n",
            "10",
            "--run-id",
            "y",
            "--results-dir",
            str(tmp_path),
            "--concurrency",
            "1",
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err.strip() == "error: weaviateBackend: pass url or set ITS_URL"


def test_an_unknown_backend_name_is_also_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # argparse restricts --backend's choices; the library path is what this pins.
    assert cli._make_backend_or_none("no-such-backend") is None
    assert capsys.readouterr().err.startswith("error: ")


def test_the_stub_backend_still_runs(tmp_path: Path) -> None:  # control
    assert (
        cli.main(
            [
                "run",
                "--backend",
                "stub",
                "--n",
                "20",
                "--queries",
                "5",
                "--run-id",
                "ok",
                "--results-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
