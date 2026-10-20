"""`hnsw_grid.py` reports a backend setup error at exit 2, not a traceback (#197).

#196 made `vector-bench run` and `load` print `error: <message>` and exit 2
when a backend's constructor raises `BackendError` (a missing extra, an unset
URL). The grid script calls `make_backend` inside `run_grid`, and `main` caught
only ValueError and OSError, so on `main` `--backend qdrant` without the extra
was a traceback at exit 1, after `--out-dir` had already been created.

A `BackendError` raised while a cell runs is a run failure, and #209 gave it the
same `error:` / exit 2 in `run` and `load`; the grid got it in #252.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from vector_bench.types import BackendError  # noqa: E402

# 60 x 64 float32 is 15,360 B: over Qdrant's 10 KiB corpus floor, which the
# grid checks before `make_backend` (#245), so the qdrant arms reach the
# constructor error they test.
SMALL = ["--n-vectors", "60", "--n-queries", "5", "--dim", "64", "--top-k", "5"]
AXES = ["--M", "16", "--ef-construction", "100", "--ef-search", "64"]

# The SDK module each adapter imports lazily. Setting it to None in
# `sys.modules` makes the import raise ImportError whether or not the extra is
# installed, so the arm does not depend on the machine's environment.
_SDK_MODULE = {"qdrant": "qdrant_client", "weaviate": "weaviate", "pgvector": "psycopg"}


@pytest.mark.parametrize("backend", sorted(_SDK_MODULE))
def test_a_missing_extra_is_exit_2_with_one_error_line_and_no_out_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    monkeypatch.setitem(sys.modules, _SDK_MODULE[backend], None)
    out = tmp_path / "grid"

    rc = hnsw_grid.main([*SMALL, *AXES, "--backend", backend, "--out-dir", str(out)])

    err = capsys.readouterr().err
    assert rc == 2
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("error: ")
    assert "extra" in lines[0]
    assert "Traceback" not in err
    assert not out.exists(), "a refused setup must not leave an --out-dir behind"


def test_an_unset_url_is_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_make_backend(name: str, **kwargs: object) -> object:
        raise BackendError("QdrantBackend: pass url or set QDRANT_URL")

    monkeypatch.setattr(hnsw_grid, "make_backend", fake_make_backend)
    out = tmp_path / "grid"

    rc = hnsw_grid.main([*SMALL, *AXES, "--backend", "qdrant", "--out-dir", str(out)])

    assert rc == 2
    assert capsys.readouterr().err == "error: QdrantBackend: pass url or set QDRANT_URL\n"
    assert not out.exists()


def test_a_backend_error_while_a_cell_runs_is_exit_2_with_one_error_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure mid-run is reported the way `vector-bench run`/`load` report it (#209, #252).

    This arm used to pin the traceback: `main` caught only ValueError and
    OSError, so the `BackendError` escaped at exit 1.
    """

    def failing_run_benchmark(*args: object, **kwargs: object) -> object:
        raise BackendError("collection 'bench' not fully indexed within 0s")

    monkeypatch.setattr(hnsw_grid, "run_benchmark", failing_run_benchmark)
    out = tmp_path / "grid"

    rc = hnsw_grid.main([*SMALL, *AXES, "--out-dir", str(out)])

    assert rc == 2
    assert capsys.readouterr().err == "error: collection 'bench' not fully indexed within 0s\n"
    assert not (out / "grid.json").exists()


def test_a_backend_error_in_a_later_cell_writes_no_grid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cell 1 succeeds, cell 2 fails: exit 2 and no `grid.json` claiming a full grid (#252)."""
    real = hnsw_grid.run_benchmark
    calls = []

    def second_cell_fails(*args: object, **kwargs: object) -> object:
        calls.append(kwargs["run_id"])
        if len(calls) == 2:
            raise BackendError("connection reset during query")
        return real(*args, **kwargs)

    monkeypatch.setattr(hnsw_grid, "run_benchmark", second_cell_fails)
    out = tmp_path / "grid"

    rc = hnsw_grid.main(
        [
            *SMALL,
            "--M",
            "8,16",
            "--ef-construction",
            "100",
            "--ef-search",
            "64",
            "--out-dir",
            str(out),
        ]
    )

    assert rc == 2
    assert calls == ["M8_efc100_efs64", "M16_efc100_efs64"]
    assert "error: connection reset during query" in capsys.readouterr().err
    assert not (out / "grid.json").exists()


def test_a_hnsw_sim_grid_still_exits_0(tmp_path: Path) -> None:
    out = tmp_path / "grid"

    rc = hnsw_grid.main([*SMALL, *AXES, "--out-dir", str(out)])

    assert rc == 0
    assert (out / "grid.json").is_file()
    assert (out / "M16_efc100_efs64.json").is_file()
