"""Snapshot test for README's HNSW 'Recommended defaults' row.

The README's HNSW section claims `M=32, ef_construction=100, ef_search=128,
recall@10=0.998` is the knee at `recall ≥ 0.95` from
``scripts/hnsw_grid.py`` against `HnswSimBackend` with seed=1 (D-009).
The simulation backend is pure-numpy and deterministic given the seed.

This test re-runs the default grid (36 cells, ~10 seconds on a laptop)
and asserts the knee picker still picks the same `(M, ef_construction,
ef_search)` triple and the same recall value the README quotes. It
**does not** lock `p95_ms` — the README's `2.02 ms` is the operator's
first measurement and wall-clock latency varies across machines and
Python versions, so locking it would make this test a CI flake.

Same hygiene pattern as the existing ``test_readme_snapshot.py`` (other
README invariants), ``test_cost_table.py`` (cost table snapshot), and the
snapshot tests landed across the portfolio.

When the snapshot fails, regenerate with::

    python scripts/hnsw_grid.py --out-dir results/hnsw-grid

…then update the README's "Recommended defaults" row from
``results/hnsw-grid/grid.json`` and ``git diff README.md`` before
committing.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_hnsw_grid_module():
    """Import the script as a module without depending on it being on PYTHONPATH."""
    spec = importlib.util.spec_from_file_location("hnsw_grid_snapshot", _SCRIPTS / "hnsw_grid.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# README cell anchored by this snapshot. If the simulation legitimately
# moves these numbers, update both the README's "Recommended defaults" row
# and these constants in the same PR.
EXPECTED_M = 32
EXPECTED_EF_CONSTRUCTION = 100
EXPECTED_EF_SEARCH = 128
EXPECTED_RECALL_AT_10 = 0.998
RECALL_TOLERANCE = 5e-4  # README rounds to 3 decimals.

REGEN_HINT = (
    "Regenerate the HNSW grid:\n"
    "  python scripts/hnsw_grid.py --out-dir results/hnsw-grid\n"
    "Then update the README's `Recommended defaults` row from "
    "results/hnsw-grid/grid.json and inspect with `git diff README.md` "
    "before committing."
)


@pytest.fixture(scope="module")
def grid_payload() -> dict:
    """Run the deterministic 36-cell default grid once for all asserts."""
    mod = _load_hnsw_grid_module()
    with tempfile.TemporaryDirectory() as tmp:
        return mod.run_grid(
            n_vectors=2000,
            n_queries=200,
            dim=64,
            top_k=10,
            seed=1,
            M_values=[8, 16, 32],
            ef_construction_values=[50, 100, 200],
            ef_search_values=[16, 32, 64, 128],
            out_dir=Path(tmp),
        )


def _knee(payload: dict) -> dict:
    """Pick min p95_ms among cells with recall ≥ 0.95 (README's phrasing)."""
    qualifying = [c for c in payload["cells"] if c["mean_recall_at_k"] >= 0.95]
    assert qualifying, (
        "No grid cell achieved recall ≥ 0.95. Either the simulation backend "
        "regressed catastrophically or the workload defaults changed.\n"
        f"{REGEN_HINT}"
    )
    return min(qualifying, key=lambda c: c["p95_ms"])


def test_knee_picks_readme_parameter_triple(grid_payload: dict) -> None:
    """The knee (M, ef_construction, ef_search) must match the README cell."""
    knee = _knee(grid_payload)
    actual = (knee["M"], knee["ef_construction"], knee["ef_search"])
    expected = (EXPECTED_M, EXPECTED_EF_CONSTRUCTION, EXPECTED_EF_SEARCH)
    assert actual == expected, (
        f"HNSW grid knee parameters drifted. README says "
        f"M={EXPECTED_M}, ef_construction={EXPECTED_EF_CONSTRUCTION}, "
        f"ef_search={EXPECTED_EF_SEARCH}; live grid picked "
        f"M={knee['M']}, ef_construction={knee['ef_construction']}, "
        f"ef_search={knee['ef_search']} (recall={knee['mean_recall_at_k']:.3f}, "
        f"p95={knee['p95_ms']:.2f}ms).\n{REGEN_HINT}"
    )


def test_knee_recall_matches_readme_cell(grid_payload: dict) -> None:
    """The knee's `mean_recall_at_k` must match the README cell value."""
    knee = _knee(grid_payload)
    assert knee["mean_recall_at_k"] == pytest.approx(EXPECTED_RECALL_AT_10, abs=RECALL_TOLERANCE), (
        f"HNSW grid knee recall@10 drifted. README says {EXPECTED_RECALL_AT_10}; "
        f"live grid produced {knee['mean_recall_at_k']:.3f}.\n{REGEN_HINT}"
    )


def test_readme_quotes_knee_row(grid_payload: dict) -> None:
    """The README's `Recommended defaults` table row must literally include
    the knee triple + recall the live grid produces today."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    knee = _knee(grid_payload)
    cell_anchor = (
        f"| {knee['M']} | {knee['ef_construction']} | {knee['ef_search']} "
        f"| {knee['mean_recall_at_k']:.3f} |"
    )
    assert cell_anchor in readme, (
        f"README is missing the row anchor `{cell_anchor}` in the "
        f"`Recommended defaults` table. The grid produced this knee but the "
        f"README quotes a different one.\n{REGEN_HINT}"
    )
