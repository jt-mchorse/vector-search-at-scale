"""pgvector's HNSW bounds are checked before anything runs (#188).

pgvector refuses `ef_construction < 2 * m` (and m outside [2, 100],
ef_construction outside [4, 1000], ef_search outside [1, 1000]; v0.8.0
`src/hnsw.h`, `src/hnswbuild.c`) only when it BUILDS the index, i.e. at the
first `ingest`. The README's grid axes include M=32 with ef_construction=50, so
`hnsw_grid.py --backend pgvector` died at cell 7 of 36 with
`InvalidParameterValue: ef_construction must be greater than or equal to 2 * m`,
six cell JSONs on disk and no grid.json (measured by a hunt agent on a private
Postgres 17 + pgvector 0.8.0). These arms need no database.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vector_bench.backends.pgvector import PgVectorBackend, validate_hnsw_params

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")

README_AXES = {
    "M_values": [8, 16, 32],
    "ef_construction_values": [50, 100, 200],
    "ef_search_values": [16],
}


@pytest.mark.parametrize(
    ("m", "efc", "efs"),
    [(16, 64, 40), (2, 4, 1), (100, 1000, 1000), (32, 64, 16), (8, 16, 128)],
)
def test_values_pgvector_accepts(m: int, efc: int, efs: int) -> None:
    validate_hnsw_params(m=m, ef_construction=efc, ef_search=efs)


@pytest.mark.parametrize(
    ("m", "efc", "efs", "match"),
    [
        (32, 50, 16, "ef_construction >= 2 \\* m"),
        (32, 63, 16, "needs >= 64"),
        (1, 64, 40, "hnsw_m"),
        (101, 1000, 40, "hnsw_m"),
        (16, 1001, 40, "hnsw_ef_construction"),
        (16, 64, 0, "hnsw_ef_search"),
        (16, 64, 1001, "hnsw_ef_search"),
        (True, 64, 40, "hnsw_m"),
        (16, 64.0, 40, "hnsw_ef_construction"),
    ],
)
def test_values_pgvector_refuses(m: object, efc: object, efs: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_hnsw_params(m=m, ef_construction=efc, ef_search=efs)  # type: ignore[arg-type]


def test_the_backend_refuses_before_it_needs_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    with pytest.raises(ValueError, match="2 \\* m"):
        PgVectorBackend(hnsw_m=32, hnsw_ef_construction=50)


def test_a_non_hnsw_index_does_not_check_hnsw_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stub module stands in for psycopg: the constructor only imports it.
    import types

    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://unused")
    PgVectorBackend(index_method="ivfflat", hnsw_m=32, hnsw_ef_construction=50)


def test_the_readme_grid_on_pgvector_fails_before_writing_anything(tmp_path: Path) -> None:
    out = tmp_path / "grid"
    with pytest.raises(ValueError, match="M=32 efc=50"):
        hnsw_grid.run_grid(
            n_vectors=50,
            dim=8,
            n_queries=5,
            top_k=5,
            seed=0,
            out_dir=out,
            backend_name="pgvector",
            **README_AXES,
        )
    assert not out.exists() or not any(out.iterdir())


def test_the_cli_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = hnsw_grid.main(
        [
            "--backend",
            "pgvector",
            "--n-vectors",
            "50",
            "--n-queries",
            "5",
            "--dim",
            "8",
            "--M",
            "8,16,32",
            "--ef-construction",
            "50,100,200",
            "--ef-search",
            "16",
            "--out-dir",
            str(tmp_path / "grid"),
        ]
    )
    assert rc == 2
    assert "outside pgvector's HNSW bounds" in capsys.readouterr().err


def test_hnsw_sim_runs_the_same_axes(tmp_path: Path) -> None:
    # Control: the bounds are pgvector's, not the grid's.
    grid = hnsw_grid.run_grid(
        n_vectors=50,
        dim=8,
        n_queries=5,
        top_k=5,
        seed=0,
        out_dir=tmp_path,
        backend_name="hnsw-sim",
        **README_AXES,
    )
    assert len(grid["cells"]) == 9
