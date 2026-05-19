"""Snapshot test for README's HNSW 'Recommended defaults' row.

The README's HNSW section claims ``M=32, ef_construction=100, ef_search=128,
recall@10=0.998`` is the recommended default from ``scripts/hnsw_grid.py``
against ``HnswSimBackend`` with ``seed=1`` (D-009). The simulation backend
is pure-numpy and the parameter grid output is deterministic *given the
same BLAS impl* — but BLAS varies across platforms (Mac ARM OpenBLAS vs
Linux x86_64 OpenBLAS vs MKL), perturbing ``recall@10`` at the 4th decimal.
That's enough noise to flip a ``min(p95_ms)`` knee selection between
``(32, 100, 128)`` and ``(32, 200, 128)``, since both sit microseconds
apart on any given run.

This test snapshots what is *actually* stable across platforms:

1. The README literally contains the row anchor for the recommended
   defaults cell (catches README rewrites that drop the row).
2. The simulation produces ``recall@10 ≈ 0.998`` at the README's exact
   parameter triple ``(M=32, ef_construction=100, ef_search=128)``.
3. The README's row is in the Pareto-front family of acceptable
   recommended defaults — ``{M=32, ef_search=128, recall@10 ≥ 0.99}`` —
   confirming the simulation still has a usable knee at the README's
   parameters even if a different family member would also qualify on
   another platform.

It deliberately does **not** select a knee via ``min(p95_ms)`` (wall-clock
latency is the dominant source of cross-platform flake) or assert exact
recall to four decimals (BLAS variance is bigger than that tolerance).

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

# Widened from the PR-original 5e-4. Cross-platform OpenBLAS variance in
# float32 dot products perturbs the simulated recall by up to ~0.002 at
# the 0.998 plateau; 5e-3 absorbs that while still failing on actual
# simulation regressions (e.g. a bug that drops recall by >0.5%).
RECALL_TOLERANCE = 5e-3

# README phrases the recommended-defaults section in terms of M=32, ef_search=128
# being the strong-recall corner. Cells in this family are interchangeable
# for the "recommended default" role within platform noise; any of them is
# a defensible row for the README to quote.
RECOMMENDED_FAMILY_M = 32
RECOMMENDED_FAMILY_EF_SEARCH = 128
RECOMMENDED_FAMILY_MIN_RECALL = 0.99

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


def _cell_at(payload: dict, *, M: int, ef_construction: int, ef_search: int) -> dict:
    """Return the grid cell at the given parameter triple."""
    for cell in payload["cells"]:
        if (
            cell["M"] == M
            and cell["ef_construction"] == ef_construction
            and cell["ef_search"] == ef_search
        ):
            return cell
    raise AssertionError(
        f"Grid is missing cell (M={M}, ef_construction={ef_construction}, "
        f"ef_search={ef_search}). The grid axes may have drifted from "
        f"the README's recommended-defaults parameters.\n{REGEN_HINT}"
    )


def test_readme_quotes_recommended_defaults_row() -> None:
    """README must literally contain the recommended-defaults row anchor."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    cell_anchor = (
        f"| {EXPECTED_M} | {EXPECTED_EF_CONSTRUCTION} | {EXPECTED_EF_SEARCH} "
        f"| {EXPECTED_RECALL_AT_10:.3f} |"
    )
    assert cell_anchor in readme, (
        f"README is missing the row anchor `{cell_anchor}` in the "
        f"`Recommended defaults` table. A README rewrite likely dropped or "
        f"reformatted the row.\n{REGEN_HINT}"
    )


def test_live_grid_recall_at_readme_cell(grid_payload: dict) -> None:
    """At the README's exact parameter triple, the live simulation must
    produce recall@10 close to the README's quoted value.

    Tolerance is 5e-3 to absorb cross-platform float32-dot variance.
    A larger drift indicates a real simulation regression at the cell
    the README anchors to.
    """
    cell = _cell_at(
        grid_payload,
        M=EXPECTED_M,
        ef_construction=EXPECTED_EF_CONSTRUCTION,
        ef_search=EXPECTED_EF_SEARCH,
    )
    actual = cell["mean_recall_at_k"]
    assert actual == pytest.approx(EXPECTED_RECALL_AT_10, abs=RECALL_TOLERANCE), (
        f"HNSW recall at README's recommended-defaults cell drifted. "
        f"README says recall@10={EXPECTED_RECALL_AT_10} at (M={EXPECTED_M}, "
        f"ef_construction={EXPECTED_EF_CONSTRUCTION}, ef_search={EXPECTED_EF_SEARCH}); "
        f"live grid produced {actual:.4f} (tolerance ±{RECALL_TOLERANCE}).\n"
        f"{REGEN_HINT}"
    )


def test_readme_row_is_in_pareto_recommended_family(grid_payload: dict) -> None:
    """The README's row must sit inside the family of acceptable
    recommended defaults — ``M=32, ef_search=128, recall@10 ≥ 0.99``.

    This is the test that confirms \"the simulation still has a usable
    knee at the README's parameters.\" If ef_construction=100 stops
    qualifying (recall drops below 0.99 on every platform), the README
    should be regenerated to point at the surviving family member.
    """
    family = [
        c
        for c in grid_payload["cells"]
        if c["M"] == RECOMMENDED_FAMILY_M
        and c["ef_search"] == RECOMMENDED_FAMILY_EF_SEARCH
        and c["mean_recall_at_k"] >= RECOMMENDED_FAMILY_MIN_RECALL
    ]
    assert family, (
        f"No grid cell with M={RECOMMENDED_FAMILY_M}, "
        f"ef_search={RECOMMENDED_FAMILY_EF_SEARCH} achieved "
        f"recall@10 ≥ {RECOMMENDED_FAMILY_MIN_RECALL}. The simulation's "
        f"recall plateau collapsed — README's recommended-defaults row "
        f"is no longer a defensible default.\n{REGEN_HINT}"
    )
    family_efcs = {c["ef_construction"] for c in family}
    assert EXPECTED_EF_CONSTRUCTION in family_efcs, (
        f"README quotes ef_construction={EXPECTED_EF_CONSTRUCTION} as the "
        f"recommended default, but it dropped out of the recall ≥ "
        f"{RECOMMENDED_FAMILY_MIN_RECALL} family on this platform. "
        f"Surviving family members: ef_construction in "
        f"{sorted(family_efcs)}. Update the README to point at one of "
        f"them.\n{REGEN_HINT}"
    )
