"""`hnsw_grid.py` refuses a repeated axis value (#169).

Measured on `main`: `--M 8,8 --ef-construction 50 --ef-search 16,32` exited 0
with a 4-cell `grid.json` (two pairs sharing a run_id) over 2 per-cell files --
the second run of each pair overwrote the first. `vector-bench load` already
refuses the same collision for its `c<NNN>.json` cells.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from hnsw_grid import main as hnsw_grid_main  # noqa: E402
from hnsw_grid import run_grid  # noqa: E402

SMALL = ["--n-vectors", "120", "--n-queries", "10", "--dim", "8"]


@pytest.mark.parametrize(
    ("flag", "values", "axis"),
    [
        ("--M", "8,8", "M"),
        ("--ef-construction", "50,50", "ef_construction"),
        ("--ef-search", "16,32,16", "ef_search"),
    ],
)
def test_a_repeated_axis_value_is_exit_2_and_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, values: str, axis: str
) -> None:
    out = tmp_path / "grid"
    argv = [
        *SMALL,
        "--M",
        "8",
        "--ef-construction",
        "50",
        "--ef-search",
        "16",
        "--out-dir",
        str(out),
    ]
    argv[argv.index(flag) + 1] = values
    assert hnsw_grid_main(argv) == 2
    err = capsys.readouterr().err
    assert f"{axis} values must be distinct" in err
    assert not out.exists()


def test_run_grid_refuses_before_running_a_cell(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"repeated: \[8\]"):
        run_grid(
            n_vectors=120,
            n_queries=10,
            dim=8,
            top_k=5,
            seed=1,
            M_values=[8, 8],
            ef_construction_values=[50],
            ef_search_values=[16, 32],
            out_dir=tmp_path / "g",
        )
    assert not (tmp_path / "g").exists()


def test_distinct_values_still_run_one_file_per_cell(tmp_path: Path) -> None:
    out = tmp_path / "grid"
    argv = [
        *SMALL,
        "--M",
        "8,16",
        "--ef-construction",
        "50",
        "--ef-search",
        "16,32",
        "--out-dir",
        str(out),
    ]
    assert hnsw_grid_main(argv) == 0
    assert len([p for p in out.glob("M*_efc*_efs*.json")]) == 4
