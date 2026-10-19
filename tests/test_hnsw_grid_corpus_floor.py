"""`hnsw_grid.py` refuses a Qdrant corpus under the 10 KiB floor before the
grid runs (#245).

`QdrantBackend.ingest` refuses a corpus under `MIN_CORPUS_KB` (#209, #220),
where every query is a brute-force scan. The grid reached that refusal inside
cell 1's ingest, after `out_dir.mkdir`, so `--backend qdrant --n-vectors 30
--dim 64` was a `BackendError` traceback at exit 1 with an empty `--out-dir`
left behind, while `vector-bench run` on the same corpus exited 2 with an
`error:` line. The size is decidable from `--n-vectors` x `--dim` alone, so it
joins the `_HNSW_BOUNDS` pre-flight (#222).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from vector_bench.backends import qdrant as qdrant_mod  # noqa: E402

AXES = ["--M", "16", "--ef-construction", "64", "--ef-search", "32"]


def _argv(n: int, dim: int, out: Path, backend: str = "qdrant") -> list[str]:
    return [
        "--n-vectors",
        str(n),
        "--n-queries",
        "5",
        "--dim",
        str(dim),
        "--top-k",
        "3",
        *AXES,
        "--backend",
        backend,
        "--out-dir",
        str(out),
    ]


@pytest.fixture
def make_backend_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every `make_backend` call and stop there, so no arm connects."""
    calls: list[str] = []

    def fake_make_backend(name: str, **kwargs: object) -> object:
        calls.append(name)
        raise hnsw_grid.BackendError("stopped at make_backend")

    monkeypatch.setattr(hnsw_grid, "make_backend", fake_make_backend)
    return calls


# (3, 64) is under 1 KiB: never indexed. (30, 64) is the measured #219 case,
# indexed but plain-scanned. (2559, 1) is one component short of 10 KiB.
@pytest.mark.parametrize(("n", "dim"), [(3, 64), (30, 64), (2559, 1)])
def test_an_under_floor_qdrant_corpus_is_exit_2_before_connecting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make_backend_calls: list[str],
    n: int,
    dim: int,
) -> None:
    out = tmp_path / "grid"

    rc = hnsw_grid.main(_argv(n, dim, out))

    err = capsys.readouterr().err
    assert rc == 2
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("error: qdrant answers every query by brute-force scan")
    assert f"(this one is {n * dim * 4} B: {n} x {dim} float32)" in lines[0]
    assert make_backend_calls == [], "the corpus floor must be checked before make_backend"
    assert not out.exists(), "a refused corpus must not leave an --out-dir behind"


def test_an_under_floor_corpus_is_refused_for_a_multi_cell_grid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_backend_calls: list[str]
) -> None:
    out = tmp_path / "grid"
    argv = _argv(30, 64, out)
    argv[argv.index("--M") + 1] = "8,16"

    assert hnsw_grid.main(argv) == 2
    assert "brute-force scan" in capsys.readouterr().err
    assert make_backend_calls == []
    assert not out.exists()


# Controls: green before and after the fix.
@pytest.mark.parametrize(("n", "dim"), [(40, 64), (2560, 1)])
def test_exactly_ten_kib_reaches_make_backend(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make_backend_calls: list[str],
    n: int,
    dim: int,
) -> None:
    rc = hnsw_grid.main(_argv(n, dim, tmp_path / "grid"))

    assert rc == 2
    assert capsys.readouterr().err == "error: stopped at make_backend\n"
    assert make_backend_calls == ["qdrant"]


def test_hnsw_sim_has_no_corpus_floor(tmp_path: Path) -> None:
    out = tmp_path / "grid"

    assert hnsw_grid.main(_argv(30, 8, out, backend="hnsw-sim")) == 0
    assert (out / "grid.json").is_file()


def test_validate_corpus_is_pure_and_matches_the_floor() -> None:
    # Looked up at call time so a missing function is a failure, not a
    # collection error.
    validate_corpus = getattr(qdrant_mod, "validate_corpus", None)
    assert validate_corpus is not None, "qdrant.validate_corpus is missing"
    floor = qdrant_mod.MIN_CORPUS_KB * 256
    validate_corpus(n_vectors=floor, dim=1)
    with pytest.raises(ValueError, match=r"under 10 KiB \(full_scan_threshold\)"):
        validate_corpus(n_vectors=floor - 1, dim=1)
