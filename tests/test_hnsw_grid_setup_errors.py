"""`hnsw_grid.py` reports a backend setup error at exit 2, not a traceback (#197).

#196 made `vector-bench run` and `load` print `error: <message>` and exit 2
when a backend's constructor raises `BackendError` (a missing extra, an unset
URL). The grid script calls `make_backend` inside `run_grid`, and `main` caught
only ValueError and OSError, so on `main` `--backend qdrant` without the extra
was a traceback at exit 1, after `--out-dir` had already been created.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from vector_bench.types import BackendError  # noqa: E402

SMALL = ["--n-vectors", "60", "--n-queries", "5", "--dim", "8", "--top-k", "5"]
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


def test_a_backend_error_while_a_cell_runs_is_not_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only construction is operator input; a failure mid-run stays a failure."""

    def failing_run_benchmark(*args: object, **kwargs: object) -> object:
        raise BackendError("connection reset during query")

    monkeypatch.setattr(hnsw_grid, "run_benchmark", failing_run_benchmark)

    with pytest.raises(BackendError, match="connection reset"):
        hnsw_grid.main([*SMALL, *AXES, "--out-dir", str(tmp_path / "grid")])


def test_a_hnsw_sim_grid_still_exits_0(tmp_path: Path) -> None:
    out = tmp_path / "grid"

    rc = hnsw_grid.main([*SMALL, *AXES, "--out-dir", str(out)])

    assert rc == 0
    assert (out / "grid.json").is_file()
    assert (out / "M16_efc100_efs64.json").is_file()
