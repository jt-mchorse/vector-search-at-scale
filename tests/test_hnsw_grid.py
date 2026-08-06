"""Tests for `scripts/hnsw_grid.py` and `scripts/plot_hnsw_frontier.py` (#3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from hnsw_grid import main as hnsw_grid_main  # noqa: E402
from hnsw_grid import run_grid  # noqa: E402
from plot_hnsw_frontier import (  # noqa: E402
    main,
    pareto_frontier,
    recommended_defaults,
    render,
)


def _make_cell(M: int, efc: int, efs: int, recall: float, p95: float) -> dict:
    return {
        "run_id": f"M{M}_efc{efc}_efs{efs}",
        "M": M,
        "ef_construction": efc,
        "ef_search": efs,
        "mean_recall_at_k": recall,
        "p50_ms": p95 / 2.0,
        "p95_ms": p95,
        "p99_ms": p95 * 1.2,
        "ingest_seconds": 0.1,
    }


def test_pareto_frontier_filters_dominated_points():
    cells = [
        _make_cell(8, 50, 16, recall=0.60, p95=1.0),
        _make_cell(8, 50, 32, recall=0.85, p95=2.0),
        _make_cell(8, 50, 64, recall=0.95, p95=3.0),
        # Dominated: same recall as previous, higher latency.
        _make_cell(16, 50, 64, recall=0.95, p95=4.0),
        # Dominated: lower recall AND higher latency than (recall=0.95, p95=3.0).
        _make_cell(32, 200, 16, recall=0.80, p95=5.0),
    ]
    frontier = pareto_frontier(cells)
    triples = {(c["M"], c["ef_construction"], c["ef_search"]) for c in frontier}
    assert (16, 50, 64) not in triples
    assert (32, 200, 16) not in triples
    assert (8, 50, 16) in triples
    assert (8, 50, 32) in triples
    assert (8, 50, 64) in triples


def test_pareto_frontier_single_cell():
    cells = [_make_cell(8, 50, 16, recall=0.9, p95=2.0)]
    assert pareto_frontier(cells) == cells


def test_recommended_defaults_picks_lowest_latency_meeting_floor():
    cells = [
        _make_cell(8, 50, 16, recall=0.60, p95=1.0),
        _make_cell(8, 100, 32, recall=0.96, p95=2.5),
        _make_cell(16, 100, 64, recall=0.97, p95=5.0),
        _make_cell(32, 200, 128, recall=0.99, p95=10.0),
    ]
    pick = recommended_defaults(cells, recall_floor=0.95)
    assert pick is not None
    assert pick["ef_search"] == 32  # lowest p95 among those clearing 0.95


def test_recommended_defaults_returns_none_when_floor_unmet():
    cells = [_make_cell(8, 50, 16, recall=0.7, p95=1.0)]
    assert recommended_defaults(cells, recall_floor=0.95) is None


def test_run_grid_end_to_end_tiny_workload(tmp_path: Path):
    payload = run_grid(
        n_vectors=200,
        n_queries=20,
        dim=16,
        top_k=5,
        seed=1,
        M_values=[8, 16],
        ef_construction_values=[50],
        ef_search_values=[16, 32],
        out_dir=tmp_path,
    )
    # 2 M × 1 efc × 2 efs = 4 cells.
    assert len(payload["cells"]) == 4
    # Each cell wrote its BenchmarkResult JSON.
    for cell in payload["cells"]:
        assert (tmp_path / f"{cell['run_id']}.json").exists()
    # And grid.json was written.
    grid_path = tmp_path / "grid.json"
    assert grid_path.exists()
    on_disk = json.loads(grid_path.read_text())
    assert on_disk["axes"]["M"] == [8, 16]
    assert on_disk["axes"]["ef_search"] == [16, 32]
    # Recall is monotone-ish in ef_search at fixed (M, efc) — the simulation's
    # core claim. Allow a small tolerance for stochastic ties; assert that the
    # larger ef_search row is at least as good as the smaller.
    for M in (8, 16):
        small = next(c for c in payload["cells"] if c["M"] == M and c["ef_search"] == 16)
        large = next(c for c in payload["cells"] if c["M"] == M and c["ef_search"] == 32)
        assert large["mean_recall_at_k"] >= small["mean_recall_at_k"]


def test_main_unwritable_out_dir_exits_2_not_traceback(tmp_path: Path, capsys):
    # `--out-dir` is operator input too: an unwritable target must land as a clean
    # exit 2, not a raw OSError traceback (write-seam parity with the three sibling
    # writers guarded in #98). A path whose parent component is a FILE makes
    # run_grid's out_dir.mkdir raise NotADirectoryError. hnsw_grid was the one
    # operator-facing writer #98 left unguarded (#99).
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = hnsw_grid_main(
        [
            "--n-vectors",
            "80",
            "--n-queries",
            "8",
            "--dim",
            "8",
            "--M",
            "8",
            "--ef-construction",
            "50",
            "--ef-search",
            "16",
            "--out-dir",
            str(blocker / "sub"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not write under" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("svg", [False, True])
def test_render_writes_png_and_optionally_svg(tmp_path: Path, svg: bool):
    pytest.importorskip("matplotlib")
    grid = {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 1000, "n_queries": 100, "dim": 32, "top_k": 5, "seed": 1},
        "axes": {"M": [8], "ef_construction": [50], "ef_search": [16, 64]},
        "cells": [
            _make_cell(8, 50, 16, recall=0.7, p95=1.5),
            _make_cell(8, 50, 64, recall=0.95, p95=3.0),
        ],
    }
    out_png = tmp_path / "frontier.png"
    out_svg = tmp_path / "frontier.svg" if svg else None
    frontier, png, svg_path = render(grid, out_png=out_png, out_svg=out_svg)
    assert out_png.exists()
    assert png == out_png
    if svg:
        assert out_svg.exists()
        assert svg_path == out_svg
    assert len(frontier) == 2  # both non-dominated


def test_main_unwritable_out_png_exits_2_not_traceback(tmp_path: Path, capsys):
    pytest.importorskip("matplotlib")  # the write seam is only reached when plotting runs
    # The output path is operator input too: an unwritable --out-png must land as a
    # clean exit 2, not a raw OSError traceback after the table prints (write-seam
    # sibling of the input guards below and llm-eval-harness#158/#159). A path whose
    # parent component is a FILE makes render's mkdir raise NotADirectoryError.
    grid = {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 1000, "n_queries": 100, "dim": 32, "top_k": 5, "seed": 1},
        "axes": {"M": [8], "ef_construction": [50], "ef_search": [16, 64]},
        "cells": [
            _make_cell(8, 50, 16, recall=0.7, p95=1.5),
            _make_cell(8, 50, 64, recall=0.95, p95=3.0),
        ],
    }
    grid_json = tmp_path / "grid.json"
    grid_json.write_text(json.dumps(grid), encoding="utf-8")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = main([str(grid_json), "--out-png", str(blocker / "sub" / "frontier.png")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not write chart" in err
    assert "Traceback" not in err


def test_render_rejects_empty_grid(tmp_path: Path):
    grid = {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 0, "n_queries": 0, "dim": 0, "top_k": 0, "seed": 1},
        "axes": {"M": [], "ef_construction": [], "ef_search": []},
        "cells": [],
    }
    with pytest.raises(ValueError, match="no cells"):
        render(grid, out_png=tmp_path / "out.png")


def test_main_missing_grid_json_exits_2_not_traceback(tmp_path: Path, capsys):
    # A missing operator-supplied grid.json translates to a clean exit 2 (the
    # pre-existing #83/#84 contract) — kept here alongside the bad-content cases.
    missing = tmp_path / "no_such_grid.json"
    rc = main([str(missing)])
    assert rc == 2
    assert str(missing) in capsys.readouterr().err


def test_main_non_utf8_grid_json_exits_2_not_traceback(tmp_path: Path, capsys):
    # A present-but-non-UTF-8 grid.json is bad operator input, the same class as
    # the missing-file case. read_text(encoding="utf-8") raises UnicodeDecodeError
    # (a ValueError subclass, NOT a FileNotFoundError), which escaped the
    # pre-check and leaked a raw traceback at exit 1. It must translate to a clean
    # exit 2 (sibling of llm-eval-harness#174).
    bad = tmp_path / "grid.json"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")
    rc = main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid UTF-8" in err
    assert str(bad) in err


def test_main_malformed_json_grid_exits_2_not_traceback(tmp_path: Path, capsys):
    # A present, valid-UTF-8-but-not-valid-JSON grid.json is bad operator input at
    # the same json.loads seam: it raises json.JSONDecodeError, which also escaped
    # the pre-check. It must translate to a clean exit 2, not leak.
    bad = tmp_path / "grid.json"
    bad.write_text("{not valid json", encoding="utf-8")
    rc = main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert str(bad) in err


# ----- degenerate grid dimensions (#117) -------------------------------------
#
# `run_grid` builds a `Workload` (validating n_vectors/dim/n_queries/top_k) and
# calls `make_backend`, and both report a bad value by raising ValueError. That
# call sat inside a try catching OSError only, so every degenerate dimension
# escaped as a raw traceback at exit 1. This is the fourth entry point in the
# repo that constructs a Workload + backend and the only one that lacked the
# arm — `cli._do_run` and `cli._do_load` both land the same ValueError as a
# clean exit 2 (#83), and `_do_load`'s comment describes exactly this shape.


def _grid_argv(out_dir: Path, overrides: dict[str, str] | None = None) -> list[str]:
    """A minimal, fast, valid grid invocation, with flags replaced by name.

    Keys are the full flag spellings (`"--top-k"`), so an override always
    *replaces* a default rather than appending a second occurrence.
    """
    flags = {
        "--n-vectors": "50",
        "--n-queries": "5",
        "--dim": "8",
        "--top-k": "3",
        "--M": "8",
        "--ef-construction": "20",
        "--ef-search": "16",
    }
    for key in overrides or {}:
        assert key in flags, f"override {key!r} is not one of {sorted(flags)}"
    flags.update(overrides or {})
    argv: list[str] = []
    for k, v in flags.items():
        argv += [k, v]
    return argv + ["--out-dir", str(out_dir)]


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"--n-vectors": "0"}, "n_vectors must be positive"),
        ({"--n-vectors": "-5"}, "n_vectors must be positive"),
        ({"--dim": "0"}, "dim must be positive"),
        ({"--n-queries": "0"}, "n_queries must be positive"),
        ({"--top-k": "0"}, "top_k must be positive"),
        ({"--top-k": "-3"}, "top_k must be positive"),
        ({"--M": "0"}, "M must be positive"),
        ({"--ef-search": "0"}, "ef_search must be positive"),
    ],
)
def test_main_degenerate_dimension_exits_2_not_traceback(
    tmp_path: Path, capsys, override: dict, needle: str
):
    rc = hnsw_grid_main(_grid_argv(tmp_path, override))
    err = capsys.readouterr().err
    assert rc == 2
    assert needle in err
    # `error:` is the neutral prefix `_do_run`/`_do_load` use for this class, so
    # all three entry points read the same.
    assert err.startswith("error:")
    assert "Traceback" not in err
    # Nothing was published for a run that never should have started.
    assert not (tmp_path / "grid.json").exists()


def test_main_unknown_backend_exits_2_not_traceback(tmp_path: Path, capsys):
    rc = hnsw_grid_main(_grid_argv(tmp_path) + ["--backend", "bogus"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown backend" in err
    assert "Traceback" not in err
    assert not (tmp_path / "grid.json").exists()


@pytest.mark.parametrize("flag", ["--M", "--ef-construction", "--ef-search"])
@pytest.mark.parametrize("empty", ["", ",", " , "])
def test_main_empty_axis_list_exits_2_and_writes_nothing(
    tmp_path: Path, capsys, flag: str, empty: str
):
    """The worst of the set: `_parse_int_list` drops empty segments, so an empty
    axis parsed to `[]`, `itertools.product` yielded nothing, and the script
    wrote a ZERO-CELL grid.json and exited 0 — announcing a successful grid run.
    A degenerate artifact published as a benchmark (handoff §10); the failure
    only surfaced later as plot_hnsw_frontier's "grid has no cells"."""
    rc = hnsw_grid_main(_grid_argv(tmp_path, {flag: empty}))
    err = capsys.readouterr().err
    assert rc == 2
    assert f"{flag} must contain at least one value" in err
    assert "Traceback" not in err
    # The regression this locks: pre-fix a grid.json existed here, with 0 cells.
    assert not (tmp_path / "grid.json").exists()


def test_main_valid_tiny_grid_still_exits_0_and_writes_cells(tmp_path: Path, capsys):
    """The new guards must not swallow a working run."""
    rc = hnsw_grid_main(_grid_argv(tmp_path))
    _ = capsys.readouterr()
    assert rc == 0
    grid = json.loads((tmp_path / "grid.json").read_text())
    assert len(grid["cells"]) == 1


def test_every_cli_workload_construction_is_guarded_against_valueerror():
    """`Workload(...)` behind a CLI must sit inside a `ValueError`-catching try,
    or that entry point reports a bad dimension as an exit-1 traceback. Derived
    from the sources by walking the AST, so a fifth entry point reintroducing
    the gap fails here rather than depending on someone remembering."""
    import ast

    def unguarded_sites(path: Path) -> list[int]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            catches_value_error = any(
                "ValueError"
                in {
                    n.id
                    for n in (
                        h.type.elts if isinstance(h.type, ast.Tuple) else [h.type] if h.type else []
                    )
                    if isinstance(n, ast.Name)
                }
                for h in node.handlers
            )
            if not catches_value_error:
                continue
            for inner in ast.walk(node):
                guarded.add(id(inner))
        bad: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Workload"
                and id(node) not in guarded
            ):
                bad.append(node.lineno)
        return bad

    # `run_grid` is a library function called from `main` inside the guarded try,
    # so the guard is one frame up; assert on the entry points that own a `main`.
    cli_path = _REPO_ROOT / "src" / "vector_bench" / "cli.py"
    assert unguarded_sites(cli_path) == [], (
        f"unguarded Workload(...) in cli.py at lines {unguarded_sites(cli_path)}"
    )
    # hnsw_grid's Workload lives in run_grid; what must be guarded is main's
    # `run_grid(...)` call, which the ValueError arm now covers.
    grid_src = (_REPO_ROOT / "scripts" / "hnsw_grid.py").read_text(encoding="utf-8")
    grid_tree = ast.parse(grid_src)
    main_fn = next(
        n for n in ast.walk(grid_tree) if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    arms = {
        n.id
        for h in ast.walk(main_fn)
        if isinstance(h, ast.ExceptHandler) and h.type is not None
        for n in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type])
        if isinstance(n, ast.Name)
    }
    assert {"ValueError", "OSError"} <= arms
