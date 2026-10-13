"""Qdrant's HNSW bounds are checked before anything runs (#222).

#188 gave pgvector this check; Qdrant's adapter passed its knobs to the server
unchecked. Measured on real Qdrant 1.14.0 and 1.19.2:

    ef_construct < 4   422 at create_collection -- in the grid, mid-sweep, with
                       earlier cell JSONs on disk, no grid.json, exit 1
    hnsw_ef = 0        1.19.2: 422 at the first query, after the whole ingest;
                       1.14.0: accepted, an `efs=0` cell is published
    m = 0              accepted, and the graph is off: every segment `indexed`,
                       every search `unfiltered_plain`, recall 1.000 where m=16
                       gave 0.584 (n=5,000 x 64, hnsw_ef=4)

These arms need no server.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vector_bench.backends.qdrant import QdrantBackend, validate_hnsw_params

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")

SMALL = {"n_vectors": 50, "dim": 8, "n_queries": 5, "top_k": 5, "seed": 0}


@pytest.mark.parametrize(
    ("m", "efc", "ef"),
    [(16, 64, 40), (1, 4, 1), (100_000, 100_000, 100_000), (8, 50, 16)],
)
def test_values_qdrant_accepts(m: int, efc: int, ef: int) -> None:
    validate_hnsw_params(m=m, ef_construct=efc, ef=ef)


@pytest.mark.parametrize(
    ("m", "efc", "ef", "match"),
    [
        (0, 64, 40, "hnsw_m must be an int >= 1; got 0"),
        (-1, 64, 40, "hnsw_m"),
        (16, 3, 40, "hnsw_ef_construct must be an int >= 4; got 3"),
        (16, 64, 0, "hnsw_ef must be an int >= 1; got 0"),
        (True, 64, 40, "hnsw_m"),
        (16, 64.0, 40, "hnsw_ef_construct"),
        (16, 64, "40", "hnsw_ef"),
    ],
)
def test_values_qdrant_refuses(m: object, efc: object, ef: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_hnsw_params(m=m, ef_construct=efc, ef=ef)  # type: ignore[arg-type]


def test_the_backend_refuses_before_it_needs_the_sdk_or_a_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QDRANT_URL", raising=False)
    # None in sys.modules makes the SDK import raise, so reaching it would be a
    # BackendError about the extra rather than this ValueError.
    monkeypatch.setitem(sys.modules, "qdrant_client", None)
    with pytest.raises(ValueError, match="hnsw_ef_construct"):
        QdrantBackend(hnsw_ef_construct=3)


def test_a_bad_qdrant_axis_fails_the_grid_before_writing_anything(tmp_path: Path) -> None:
    out = tmp_path / "grid"
    with pytest.raises(ValueError, match="outside qdrant's HNSW bounds") as e:
        hnsw_grid.run_grid(
            out_dir=out,
            backend_name="qdrant",
            M_values=[16],
            ef_construction_values=[64, 3],
            ef_search_values=[64],
            **SMALL,
        )
    assert "M=16 efc=3 efs=64" in str(e.value)
    assert not out.exists() or not any(out.iterdir())


def test_the_cli_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "grid"
    rc = hnsw_grid.main(
        [
            "--backend",
            "qdrant",
            "--n-vectors",
            "50",
            "--n-queries",
            "5",
            "--dim",
            "8",
            "--M",
            "0,16",
            "--ef-construction",
            "64",
            "--ef-search",
            "16",
            "--out-dir",
            str(out),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "1 grid cell(s) are outside qdrant's HNSW bounds" in err
    assert not out.exists()


def test_pgvector_keeps_its_own_bounds_and_message(tmp_path: Path) -> None:
    # Control: the table is per backend. M=32/efc=50 is fine for qdrant and
    # outside pgvector's 2*m rule (#188), whose message is unchanged.
    axes = {"M_values": [32], "ef_construction_values": [50], "ef_search_values": [16]}
    with pytest.raises(ValueError, match="outside pgvector's HNSW bounds"):
        hnsw_grid.run_grid(out_dir=tmp_path / "pg", backend_name="pgvector", **axes, **SMALL)
    validate_hnsw_params(m=32, ef_construct=50, ef=16)
