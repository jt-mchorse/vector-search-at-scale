"""Every backend the grid can sweep has an `_HNSW_BOUNDS` row (#227, #228).

#188 (pgvector) and #222 (qdrant) checked the whole grid before the first cell.
hnsw-sim (#227) and weaviate (#228, the row #225 deferred until #223 merged)
checked only in their constructors, which run once per cell, so a bad value on
a later cell stopped the grid after the earlier cells were written. Measured
on `main` 8f2fd76 against embedded Weaviate 1.34.0:

    --backend weaviate --M 16,2 --ef-construction 64 --ef-search 16
        error: weaviate hnsw_max_connections must be an int in [4, 2047]; got 2
        M16_efc64_efs16.json on disk, no grid.json

These arms need no server: a spy stands in for `make_backend`, so "nothing
was built" is observed directly rather than inferred from a missing file.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")

#: Per backend, a good first cell and an out-of-bounds second value on the M axis.
M_AXIS = {
    "hnsw-sim": [8, 0],
    "pgvector": [16, 0],
    "qdrant": [16, 0],
    "weaviate": [16, 2],
}


def test_every_sweepable_backend_has_a_bounds_row() -> None:
    # The lock that would have caught both: a backend added to `_HNSW_KNOBS`
    # without a row is checked per cell, mid-grid.
    assert set(hnsw_grid._HNSW_BOUNDS) == set(hnsw_grid._HNSW_KNOBS)
    assert set(M_AXIS) == set(hnsw_grid._HNSW_KNOBS)


@pytest.mark.parametrize("backend", sorted(M_AXIS))
def test_a_bad_later_cell_is_refused_before_any_backend_is_built(
    backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict] = []

    def spy(name: str, **kwargs: object) -> object:
        built.append(kwargs)
        raise AssertionError(f"make_backend({name!r}) reached; the grid was not pre-checked")

    monkeypatch.setattr(hnsw_grid, "make_backend", spy)
    out = tmp_path / "grid"
    with pytest.raises(ValueError, match=rf"^1 grid cell\(s\) are outside {backend}'s HNSW"):
        hnsw_grid.run_grid(
            out_dir=out,
            backend_name=backend,
            M_values=M_AXIS[backend],
            ef_construction_values=[64],
            ef_search_values=[16],
            n_vectors=50,
            n_queries=5,
            dim=8,
            top_k=3,
            seed=0,
        )
    assert built == []
    assert not out.exists()


def test_weaviate_cli_exits_2_without_the_sdk_or_a_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-fix this reached the M16 constructor first: with no SDK it exited 2
    # about the missing extra, and against a server it wrote the M16 cell.
    monkeypatch.delenv("WEAVIATE_HOST", raising=False)
    monkeypatch.setitem(sys.modules, "weaviate", None)
    out = tmp_path / "grid"
    rc = hnsw_grid.main(
        ["--backend", "weaviate", "--n-vectors", "50", "--n-queries", "5", "--dim", "8"]
        + ["--M", "16,2", "--ef-construction", "64", "--ef-search", "16", "--out-dir", str(out)]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "1 grid cell(s) are outside weaviate's HNSW bounds" in err
    assert "M=2 efc=64 efs=16: weaviate hnsw_max_connections must be an int in [4, 2047]" in err
    assert not out.exists()
