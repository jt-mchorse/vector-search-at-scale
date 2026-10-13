"""Weaviate's HNSW bounds are checked before anything runs (#224).

#188 gave pgvector this check and #222 Qdrant; Weaviate's adapter passed its
knobs to the server unchecked. Measured on embedded Weaviate 1.34.0, the
version terraform pins:

    max_connections < 4 or > 2047   422 at collections.create -- inside
                                    ingest, so in the grid a traceback at
                                    exit 1 with earlier cell JSONs on disk
    ef_construction < 4             the same 422
    ef <= 0                         accepted, and read as *dynamic* ef (>= 100):
                                    n=5,000 x 64, M=4, efc=8 gave recall 0.343
                                    at efs=0 and -3, 0.059 at efs=1, 0.065 at
                                    efs=10 -- an `efs=0` cell published the
                                    recall of an ef=100 search

These arms need no server.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vector_bench.backends.weaviate import WeaviateBackend, validate_hnsw_params

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")


@pytest.mark.parametrize(
    ("m", "efc", "ef"),
    [(16, 64, 40), (4, 4, 1), (2047, 100_000, 100_000), (8, 50, 16)],
)
def test_values_weaviate_accepts(m: int, efc: int, ef: int) -> None:
    validate_hnsw_params(max_connections=m, ef_construction=efc, ef=ef)


@pytest.mark.parametrize(
    ("m", "efc", "ef", "match"),
    [
        (3, 64, 40, r"hnsw_max_connections must be an int in \[4, 2047\]; got 3"),
        (2048, 64, 40, r"hnsw_max_connections must be an int in \[4, 2047\]; got 2048"),
        (0, 64, 40, "hnsw_max_connections"),
        (16, 3, 40, "hnsw_ef_construction must be an int >= 4; got 3"),
        (16, 64, 0, "hnsw_ef must be an int >= 1; got 0"),
        (16, 64, -1, "hnsw_ef must be an int >= 1; got -1"),
        (True, 64, 40, "hnsw_max_connections"),
        (16, 64.0, 40, "hnsw_ef_construction"),
        (16, 64, "40", "hnsw_ef"),
    ],
)
def test_values_weaviate_refuses(m: object, efc: object, ef: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_hnsw_params(max_connections=m, ef_construction=efc, ef=ef)  # type: ignore[arg-type]


def test_the_backend_refuses_before_it_needs_the_sdk_or_a_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEAVIATE_HOST", raising=False)
    # None in sys.modules makes the SDK import raise, so reaching it would be a
    # BackendError about the extra rather than this ValueError.
    monkeypatch.setitem(sys.modules, "weaviate", None)
    with pytest.raises(ValueError, match="hnsw_ef must be an int >= 1; got 0"):
        WeaviateBackend(hnsw_ef=0)


def test_the_grid_exits_2_on_a_dynamic_ef_cell_without_writing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cell that used to be published as `efs=0` with an ef=100 recall.
    monkeypatch.setitem(sys.modules, "weaviate", None)
    out = tmp_path / "grid"
    rc = hnsw_grid.main(
        [
            "--backend",
            "weaviate",
            "--n-vectors",
            "50",
            "--n-queries",
            "5",
            "--dim",
            "8",
            "--M",
            "16",
            "--ef-construction",
            "64",
            "--ef-search",
            "0",
            "--out-dir",
            str(out),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "weaviate hnsw_ef must be an int >= 1; got 0" in err
    assert not out.exists()
