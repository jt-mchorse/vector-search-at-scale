"""A rerun of `hnsw_grid.py` into an existing `--out-dir` replaces the grid (#252).

The previous grid's cell JSONs stayed: `--M 8,16 --ef-search 16` then
`--M 8 --ef-search 32` into the same directory exited 0 with
`M16_efc50_efs16.json` and `M8_efc50_efs16.json` beside a `grid.json` listing
only `M8_efc50_efs32`. That is the grid's version of `load --force`'s stale
`c<NNN>.json` (#182, #230), and gets the same fix: cells this run did not write
are removed, matched on the exact cell-name shape only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

SMALL = ["--n-vectors", "60", "--n-queries", "5", "--dim", "16", "--top-k", "5"]


def _grid(out: Path, M: str, efc: str, efs: str) -> None:
    rc = hnsw_grid.main(
        [*SMALL, "--M", M, "--ef-construction", efc, "--ef-search", efs, "--out-dir", str(out)]
    )
    assert rc == 0


def _cell_files(out: Path) -> set[str]:
    return {p.name for p in out.iterdir() if p.name != "grid.json"}


def _grid_cells(out: Path) -> set[str]:
    grid = json.loads((out / "grid.json").read_text())
    return {f"{c['run_id']}.json" for c in grid["cells"]}


def test_the_issue_repro_leaves_only_the_new_grids_cells(tmp_path: Path) -> None:
    out = tmp_path / "g"
    _grid(out, "8,16", "50", "16")
    _grid(out, "8", "50", "32")

    assert _cell_files(out) == {"M8_efc50_efs32.json"}
    assert _grid_cells(out) == _cell_files(out)


def test_a_narrower_rerun_on_a_shared_cell_drops_the_others(tmp_path: Path) -> None:
    out = tmp_path / "g"
    _grid(out, "8,16", "50,100", "16")
    _grid(out, "8", "50", "16")

    assert _cell_files(out) == {"M8_efc50_efs16.json"}
    assert _grid_cells(out) == _cell_files(out)


def test_a_cell_count_mismatch_is_what_the_frontier_would_read(tmp_path: Path) -> None:
    """Every `M*_efc*_efs*.json` in the directory is one `grid.json` lists."""
    out = tmp_path / "g"
    _grid(out, "8,16,32", "50,100", "16,32")
    _grid(out, "16", "100", "32,64")

    on_disk = {p.name for p in out.glob("M*_efc*_efs*.json")}
    assert on_disk == _grid_cells(out) == {"M16_efc100_efs32.json", "M16_efc100_efs64.json"}


# --- controls: green before and after the fix ---------------------------------


def test_a_same_axes_rerun_keeps_every_cell(tmp_path: Path) -> None:
    out = tmp_path / "g"
    _grid(out, "8,16", "50", "16")
    _grid(out, "8,16", "50", "16")

    assert _cell_files(out) == {"M8_efc50_efs16.json", "M16_efc50_efs16.json"}
    assert _grid_cells(out) == _cell_files(out)


def test_files_that_are_not_the_exact_cell_shape_are_left_alone(tmp_path: Path) -> None:
    out = tmp_path / "g"
    out.mkdir()
    keep = [
        "notes.json",
        "frontier.png",
        "M8_efc50.json",
        "M8_efc50_efs16_old.json",
        "M8_efc50_efs16.json.bak",
        "M-8_efc50_efs16.json",
        "XM8_efc50_efs16.json",
    ]
    for name in keep:
        (out / name).write_text("{}")
    (out / "sub").mkdir()
    (out / "sub" / "M8_efc50_efs16.json").write_text("{}")

    _grid(out, "16", "50", "32")

    assert _cell_files(out) == {*keep, "sub", "M16_efc50_efs32.json"}
    assert (out / "sub" / "M8_efc50_efs16.json").is_file()


def test_a_first_run_into_an_empty_dir_writes_every_cell(tmp_path: Path) -> None:
    out = tmp_path / "g"
    _grid(out, "8,16", "50", "16,32")

    assert _cell_files(out) == _grid_cells(out)
    assert len(_grid_cells(out)) == 4
