"""A seed numpy rejects is refused when the `Workload` is built, naming the seed (#231).

`Workload` checked that `seed` is an int and left the value to
`np.random.default_rng`, which first saw it inside `generate_corpus` mid-run.
`load --seed -1` then printed "--concurrency invalid: expected non-negative
integer" for a valid `--concurrency`, and `run` / `hnsw_grid.py` printed the
same bare numpy message without naming any flag.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vector_bench.cli import main
from vector_bench.harness import Workload

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import hnsw_grid  # noqa: E402


def test_a_negative_seed_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match=r"seed must be a valid numpy seed, got -1"):
        Workload(n_vectors=10, dim=4, n_queries=2, top_k=1, seed=-1)


@pytest.mark.parametrize("seed", [0, 1, 2**64, 2**200])
def test_seeds_numpy_accepts_still_construct(seed: int) -> None:
    assert Workload(n_vectors=10, dim=4, n_queries=2, top_k=1, seed=seed).seed == seed


def test_load_reports_the_seed_not_the_concurrency(tmp_path: Path, capsys) -> None:
    rc = main(
        [
            "load",
            "--backend",
            "stub",
            "--n",
            "50",
            "--queries",
            "20",
            "--concurrency",
            "1,10",
            "--seed",
            "-1",
            "--run-id",
            "s",
            "--results-dir",
            str(tmp_path),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: seed must be a valid numpy seed, got -1")
    assert "--concurrency" not in err
    assert not (tmp_path / "s" / "matrix.json").exists()


def test_run_names_the_seed(tmp_path: Path, capsys) -> None:
    rc = main(
        [
            "run",
            "--backend",
            "stub",
            "--n",
            "50",
            "--queries",
            "5",
            "--seed",
            "-1",
            "--run-id",
            "s",
            "--results-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err.startswith("error: seed must be a valid numpy seed, got -1")
    assert not (tmp_path / "s.json").exists()


def test_hnsw_grid_names_the_seed(tmp_path: Path, capsys) -> None:
    rc = hnsw_grid.main(
        [
            "--n-vectors",
            "100",
            "--n-queries",
            "5",
            "--dim",
            "8",
            "--seed",
            "-1",
            "--M",
            "8",
            "--ef-construction",
            "16",
            "--ef-search",
            "16",
            "--out-dir",
            str(tmp_path / "g"),
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err.startswith("error: seed must be a valid numpy seed, got -1")
    assert not (tmp_path / "g" / "grid.json").exists()
