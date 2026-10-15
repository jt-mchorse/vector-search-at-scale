"""hnsw-sim's knobs are checked over the whole grid before any cell runs (#227).

#188 (pgvector) and #222 (qdrant) gave `hnsw_grid` a per-backend bounds table.
The default backend, hnsw-sim, had no row: its constructor refused a knob <= 0,
but once per cell, inside the loop. Measured on `main` 6509dd0:

    --M 8,0 --ef-construction 50 --ef-search 16
        error: M must be positive, got 0      exit 2
        M8_efc50_efs16.json on disk, no grid.json

and, run into a directory that already held a grid, the refused run overwrote
that grid's M8 cell with its own workload (dim 32, n 400) while the old
grid.json (dim 64, n 200) stayed -- the two files then disagreed.

The single-valued `--M 0` / `--ef-search 0` cases in test_hnsw_grid.py stayed
green throughout: there the bad cell is the first one.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vector_bench.backends.hnsw_sim import HnswSimBackend, validate_hnsw_params

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")

SMALL = ["--n-vectors", "50", "--n-queries", "5", "--dim", "8", "--top-k", "3"]


def _argv(out: Path, *, M: str = "8", efc: str = "20", efs: str = "16") -> list[str]:
    return SMALL + ["--M", M, "--ef-construction", efc, "--ef-search", efs, "--out-dir", str(out)]


@pytest.mark.parametrize(
    ("axes", "needle"),
    [
        ({"M": "8,0"}, "M=0 efc=20 efs=16: M must be positive, got 0"),
        ({"efc": "20,-1"}, "M=8 efc=-1 efs=16: ef_construction must be positive, got -1"),
        ({"efs": "16,0"}, "M=8 efc=20 efs=0: ef_search must be positive, got 0"),
    ],
)
def test_a_bad_value_on_a_later_cell_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], axes: dict, needle: str
) -> None:
    out = tmp_path / "grid"
    rc = hnsw_grid.main(_argv(out, **axes))
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: 1 grid cell(s) are outside hnsw-sim's HNSW bounds")
    assert needle in err
    # Pre-fix: the good first cell's JSON was here, and no grid.json.
    assert not out.exists()


def test_a_refused_grid_leaves_an_existing_grid_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "grid"
    assert hnsw_grid.main(_argv(out)) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert set(before) == {"M8_efc20_efs16.json", "grid.json"}
    capsys.readouterr()

    rc = hnsw_grid.main(
        ["--n-vectors", "60", "--n-queries", "5", "--dim", "4", "--top-k", "3"]
        + ["--M", "8,0", "--ef-construction", "20", "--ef-search", "16", "--out-dir", str(out)]
    )
    assert rc == 2
    # Pre-fix: M8_efc20_efs16.json now held the dim-4 workload, grid.json dim 8.
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_the_message_counts_every_bad_cell(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^2 grid cell\(s\) are outside hnsw-sim's") as e:
        hnsw_grid.run_grid(
            out_dir=tmp_path / "grid",
            backend_name="hnsw-sim",
            M_values=[8, 0],
            ef_construction_values=[20],
            ef_search_values=[16, 32],
            n_vectors=50,
            n_queries=5,
            dim=8,
            top_k=3,
            seed=0,
        )
    assert "M=0 efc=20 efs=16" in str(e.value)
    assert "M=0 efc=20 efs=32" in str(e.value)


@pytest.mark.parametrize(("m", "efc", "efs"), [(1, 1, 1), (16, 100, 50), (64, 400, 10_000)])
def test_values_hnsw_sim_accepts(m: int, efc: int, efs: int) -> None:
    validate_hnsw_params(M=m, ef_construction=efc, ef_search=efs)
    HnswSimBackend(M=m, ef_construction=efc, ef_search=efs)


@pytest.mark.parametrize(
    ("m", "efc", "efs", "match"),
    [
        (0, 100, 50, "M must be positive, got 0"),
        (16, -1, 50, "ef_construction must be positive, got -1"),
        (16, 100, 0, "ef_search must be positive, got 0"),
        (True, 100, 50, "M must be an int, got True"),
        (16, 100.0, 50, "ef_construction must be an int, got 100.0"),
        (16, 100, "50", "ef_search must be an int, got '50'"),
    ],
)
def test_validator_and_constructor_refuse_the_same_values(
    m: object, efc: object, efs: object, match: str
) -> None:
    # One rule, two callers: the grid's pre-pass and the constructor.
    with pytest.raises(ValueError, match=match):
        validate_hnsw_params(M=m, ef_construction=efc, ef_search=efs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=match):
        HnswSimBackend(M=m, ef_construction=efc, ef_search=efs)  # type: ignore[arg-type]
