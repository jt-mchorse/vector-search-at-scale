"""`hnsw_grid.py` speaks each backend's own knob names (#160).

`run_grid` called `make_backend(name, M=..., ef_construction=..., ef_search=...,
seed=...)` -- `HnswSimBackend`'s argument names -- for every backend. The README
and D-009 promise `--backend qdrant` (or pgvector / weaviate) regenerates the
grid against a real engine; measured on `874c90d`, every one of them raised
`TypeError: ... unexpected keyword argument 'M'` at exit 1, before importing an
SDK or opening a connection.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
hnsw_grid = importlib.import_module("hnsw_grid")

_ADAPTERS = {
    "hnsw-sim": ("vector_bench.backends.hnsw_sim", "HnswSimBackend"),
    "qdrant": ("vector_bench.backends.qdrant", "QdrantBackend"),
    "pgvector": ("vector_bench.backends.pgvector", "PgVectorBackend"),
    "weaviate": ("vector_bench.backends.weaviate", "WeaviateBackend"),
}


def _adapter(name: str) -> type:
    module, cls = _ADAPTERS[name]
    return getattr(importlib.import_module(module), cls)


@pytest.mark.parametrize("name", sorted(_ADAPTERS))
def test_the_mapped_knobs_bind_to_the_adapters_real_signature(name: str) -> None:
    """No service, no SDK: `bind` checks the names against `__init__` itself."""
    kwargs = hnsw_grid._backend_kwargs(name, M=8, efc=50, efs=16, seed=0)
    inspect.signature(_adapter(name)).bind(**kwargs)


@pytest.mark.parametrize("name", ["qdrant", "pgvector", "weaviate"])
def test_the_old_hnsw_sim_spelling_does_not_bind_to_a_real_adapter(name: str) -> None:
    """Control: the call `run_grid` used to make really is refused, so the arm
    above is testing the mapping and not a permissive signature."""
    with pytest.raises(TypeError):
        inspect.signature(_adapter(name)).bind(M=8, ef_construction=50, ef_search=16, seed=0)


@pytest.mark.parametrize("name", sorted(_ADAPTERS))
def test_each_knob_lands_on_a_distinct_argument(name: str) -> None:
    kwargs = hnsw_grid._backend_kwargs(name, M=8, efc=50, efs=16, seed=7)
    knob_values = [v for k, v in kwargs.items() if k != "seed"]
    assert sorted(knob_values) == [8, 16, 50]
    assert ("seed" in kwargs) == (name == "hnsw-sim")


def _make_backend_names() -> set[str]:
    """Every name `make_backend` dispatches on, read from its source."""
    import vector_bench.backends as backends

    tree = ast.parse(inspect.getsource(backends.make_backend))
    return {
        node.comparators[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "name"
        and isinstance(node.comparators[0], ast.Constant)
    }


def test_every_backend_is_mapped_or_explicitly_refused() -> None:
    names = _make_backend_names()
    assert len(names) >= 5, names  # non-zero control on the walk
    assert names == set(hnsw_grid._HNSW_KNOBS) | set(hnsw_grid._NO_HNSW_KNOBS)
    assert not set(hnsw_grid._HNSW_KNOBS) & set(hnsw_grid._NO_HNSW_KNOBS)


def test_a_backend_without_hnsw_knobs_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = hnsw_grid.main(
        [
            "--backend",
            "stub",
            "--M",
            "8",
            "--ef-construction",
            "50",
            "--ef-search",
            "16",
            "--n-vectors",
            "200",
            "--n-queries",
            "10",
            "--out-dir",
            str(tmp_path / "g"),
        ]
    )
    assert rc == 2
    assert "has no HNSW parameters" in capsys.readouterr().err


def test_hnsw_sim_still_receives_every_knob_and_the_seed(tmp_path: Path) -> None:
    """The default path is unchanged: the cell's parameters reach the sim."""
    payload = hnsw_grid.run_grid(
        n_vectors=200,
        n_queries=10,
        dim=8,
        top_k=5,
        seed=3,
        M_values=[8],
        ef_construction_values=[50],
        ef_search_values=[16],
        out_dir=tmp_path / "g",
    )
    (cell,) = payload["cells"]
    assert (cell["M"], cell["ef_construction"], cell["ef_search"]) == (8, 50, 16)
    again = hnsw_grid.run_grid(
        n_vectors=200,
        n_queries=10,
        dim=8,
        top_k=5,
        seed=3,
        M_values=[8],
        ef_construction_values=[50],
        ef_search_values=[16],
        out_dir=tmp_path / "g2",
    )
    assert json.dumps(again["cells"][0]["mean_recall_at_k"]) == json.dumps(cell["mean_recall_at_k"])


class _Stop(Exception):
    pass


@pytest.mark.parametrize("name", ["qdrant", "pgvector", "weaviate"])
def test_run_grid_hands_make_backend_kwargs_the_real_adapter_accepts(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the orchestrator, not around it: a test of `_backend_kwargs`
    alone stays green if `run_grid` stops calling it."""
    seen: list[dict] = []

    def fake_make_backend(backend_name: str, **kwargs: object) -> object:
        seen.append(kwargs)
        raise _Stop

    monkeypatch.setattr(hnsw_grid, "make_backend", fake_make_backend)
    with pytest.raises(_Stop):
        hnsw_grid.run_grid(
            n_vectors=50,
            n_queries=5,
            dim=4,
            top_k=3,
            seed=1,
            M_values=[8],
            ef_construction_values=[50],
            ef_search_values=[16],
            out_dir=tmp_path / "g",
            backend_name=name,
        )
    (kwargs,) = seen
    inspect.signature(_adapter(name)).bind(**kwargs)
