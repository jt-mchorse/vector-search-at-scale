"""Tests for ``scripts/plot_latency.py``.

The script isn't an importable package module, so we load it by path. The
focus here is ``_load_matrix``'s canonicalization contract: cells must come
back ascending by concurrency regardless of on-disk order, so the plot path
(which consumes them in array order on a log x-axis) draws a monotone curve
rather than a backtracking zig-zag (#49).
"""

from __future__ import annotations

import importlib.util
import json as _json
from pathlib import Path

import pytest

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, dump_load_matrix_json

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "plot_latency", REPO_ROOT / "scripts" / "plot_latency.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_latency)


def _cell(concurrency: int) -> LoadCell:
    return LoadCell(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        concurrency=concurrency,
        ingest_seconds=1.0,
        # Latency rises with concurrency so an unsorted plot is visibly wrong.
        query_latency=LatencyStats(
            p50_ms=float(concurrency),
            p95_ms=float(concurrency * 2),
            p99_ms=float(concurrency * 3),
            max_ms=float(concurrency * 4),
        ),
        mean_recall_at_k=0.9,
        throughput_qps=float(concurrency * 10),
        started_at="2026-06-23T00:00:00Z",
        git_sha=None,
    )


def test_load_matrix_orders_cells_by_concurrency(tmp_path: Path) -> None:
    # Build a matrix whose cells are in DESCENDING concurrency order (the shape
    # produced by `--concurrency 100,10,1`), persist it through the real
    # serializer, then reload via the script's `_load_matrix`.
    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(100), _cell(10), _cell(1)),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)

    loaded = plot_latency._load_matrix(tmp_path / "matrix.json")

    # Pre-fix the cells came back [100, 10, 1] (on-disk order); they must be
    # canonicalized ascending so the log-x plot line doesn't backtrack.
    assert [c.concurrency for c in loaded.cells] == [1, 10, 100]
    # The p50 series the plotter derives is then monotonically non-decreasing.
    assert [c.query_latency.p50_ms for c in loaded.cells] == [1.0, 10.0, 100.0]


def test_main_missing_matrix_path_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A missing operator-supplied matrix.json must translate to a clean exit 2
    # with a stderr message, matching the sibling `plot_hnsw_frontier.py` — not
    # escape as a raw FileNotFoundError traceback (exit 1). (#83/#84 contract.)
    missing = tmp_path / "does_not_exist" / "matrix.json"
    rc = plot_latency.main([str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err
    assert str(missing) in err


def test_main_reports_all_missing_paths(tmp_path: Path, capsys) -> None:
    # nargs="+" accepts several paths; every missing one is named before exit 2.
    m1 = tmp_path / "a.json"
    m2 = tmp_path / "b.json"
    rc = plot_latency.main([str(m1), str(m2)])
    assert rc == 2
    err = capsys.readouterr().err
    assert str(m1) in err
    assert str(m2) in err


def test_main_non_utf8_matrix_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A present-but-non-UTF-8 matrix file is bad operator input, the same class
    # as the missing-file case. `_load_matrix` does json.loads(read_text()), so a
    # non-UTF-8 byte raises UnicodeDecodeError (a ValueError subclass, NOT a
    # FileNotFoundError), which escaped the pre-check and leaked a raw traceback
    # at exit 1. It must translate to a clean exit 2 (sibling of llm-eval-harness#174).
    bad = tmp_path / "matrix.json"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")
    rc = plot_latency.main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid UTF-8" in err
    assert str(bad) in err


def test_main_malformed_json_matrix_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A present, valid-UTF-8-but-not-valid-JSON matrix file is bad operator input
    # at the same json.loads seam: it raises json.JSONDecodeError, which also
    # escaped the pre-check. It must translate to a clean exit 2, not leak.
    bad = tmp_path / "matrix.json"
    bad.write_text("{not valid json", encoding="utf-8")
    rc = plot_latency.main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert str(bad) in err


def test_main_malformed_load_matrix_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    # A file that is valid JSON but NOT a valid load matrix (a bad-typed field)
    # is the same class of bad operator input as the decode failures above:
    # `_load_matrix` coerces + constructs `LatencyStats(**...)`, whose
    # `__post_init__` runs `math.isfinite` on each field — a string p50_ms raises
    # TypeError (not a JSONDecodeError). It must translate to a clean exit 2.
    import json as _json

    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(1),),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)
    matrix_path = tmp_path / "matrix.json"
    data = _json.loads(matrix_path.read_text(encoding="utf-8"))
    data["cells"][0]["query_latency"]["p50_ms"] = "fast"  # valid JSON, bad type
    matrix_path.write_text(_json.dumps(data), encoding="utf-8")

    rc = plot_latency.main([str(matrix_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not a valid load matrix" in err
    assert str(matrix_path) in err
    # A clean operator-error line, not a leaked LatencyStats/isfinite traceback.
    assert "Traceback" not in err
    assert "math.isfinite" not in err


def _write_matrix_with(tmp_path: Path, mutate) -> Path:
    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(1),),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)
    matrix_path = tmp_path / "matrix.json"
    data = _json.loads(matrix_path.read_text(encoding="utf-8"))
    mutate(data)
    matrix_path.write_text(_json.dumps(data), encoding="utf-8")
    return matrix_path


@pytest.mark.parametrize("field", ["mean_recall_at_k", "throughput_qps", "ingest_seconds"])
def test_main_boolean_loadcell_numeric_exits_2_not_fabricated(
    tmp_path: Path, capsys, field: str
) -> None:
    # A JSON `true`/`false` at a LoadCell numeric field passes `float()`/`int()`
    # (bool subclasses int) and would fabricate a 1.0/0.0 on the published table
    # (e.g. `mean_recall_at_k: true` -> a fake perfect recall). #108 hardened the
    # structural/decode path but not this boolean-coercion sibling; it must exit 2.
    matrix_path = _write_matrix_with(tmp_path, lambda d: d["cells"][0].__setitem__(field, True))
    rc = plot_latency.main([str(matrix_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not a valid load matrix" in err
    assert "Traceback" not in err


def test_main_boolean_latency_field_exits_2_not_fabricated(tmp_path: Path, capsys) -> None:
    # A boolean nested latency field reaches `LatencyStats(**...)` raw (no
    # coercion), where `math.isfinite(True)` is True — it must be rejected, not
    # loaded as a fabricated 1.0/0.0 ms.
    matrix_path = _write_matrix_with(
        tmp_path, lambda d: d["cells"][0]["query_latency"].__setitem__("p50_ms", True)
    )
    rc = plot_latency.main([str(matrix_path)])
    assert rc == 2
    assert "not a valid load matrix" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [True, False])
def test_latency_stats_rejects_boolean_field(bad: bool) -> None:
    with pytest.raises(ValueError, match=r"p50_ms must be a finite number"):
        LatencyStats(p50_ms=bad, p95_ms=1.0, p99_ms=2.0, max_ms=3.0)


def test_main_unwritable_out_dir_exits_2_not_traceback(tmp_path: Path, capsys) -> None:
    import pytest

    pytest.importorskip("matplotlib")  # the write seam is only reached when plotting runs
    # The output dir is operator input too: an unwritable --out-dir must land as a
    # clean exit 2, not a raw OSError traceback after the table prints (write-seam
    # sibling of the input guards above and llm-eval-harness#158/#159). A path whose
    # parent component is a FILE makes _maybe_plot's mkdir raise NotADirectoryError.
    matrix = LoadMatrix(
        run_id="demo",
        backend="stub",
        workload=Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1),
        cells=(_cell(1), _cell(10)),
    )
    dump_load_matrix_json(tmp_path, matrix=matrix)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rc = plot_latency.main([str(tmp_path / "matrix.json"), "--out-dir", str(blocker / "sub")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not write" in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------
# #121: chart filenames omitted `run_id`, so two runs of one backend at one
# scale collided. The second silently overwrote the first while stderr
# reported both as written, and the process exited 0.
#
# D-007 ("one JSON file per run_id under results/, refuse overwrite without
# force") records its rationale as "clear failure mode when operator typos
# run_id collision". These lock the downstream renderer to that posture.
# --------------------------------------------------------------------------


def _matrix(run_id: str, *, backend: str = "stub", n_vectors: int = 64) -> LoadMatrix:
    """A minimal two-cell matrix, parameterized by the fields that name a chart."""
    workload = Workload(n_vectors=n_vectors, dim=16, n_queries=20, top_k=5, seed=1)
    cells = tuple(
        LoadCell(
            run_id=run_id,
            backend=backend,
            workload=workload,
            concurrency=c,
            ingest_seconds=1.0,
            query_latency=LatencyStats(
                p50_ms=float(c), p95_ms=float(c * 2), p99_ms=float(c * 3), max_ms=float(c * 4)
            ),
            mean_recall_at_k=0.9,
            throughput_qps=float(c * 10),
            started_at="2026-08-07T00:00:00Z",
            git_sha=None,
        )
        for c in (1, 10)
    )
    return LoadMatrix(run_id=run_id, backend=backend, workload=workload, cells=cells)


def _write(tmp_path: Path, matrix: LoadMatrix) -> Path:
    d = tmp_path / matrix.run_id
    d.mkdir(parents=True, exist_ok=True)
    dump_load_matrix_json(d, matrix=matrix)
    return d / "matrix.json"


def test_chart_name_is_keyed_by_run_id() -> None:
    """The naming contract itself, independent of matplotlib being installed."""
    a = plot_latency._chart_name(_matrix("baseline"))
    b = plot_latency._chart_name(_matrix("after-tuning"))

    assert a != b, "two runs of one backend at one scale must not share a filename"
    assert a.startswith("baseline"), a
    # backend + scale stay in the name — they're what makes a listing readable.
    assert "stub" in a and "n64" in a


def test_two_runs_of_one_backend_produce_two_charts(tmp_path: Path, capsys) -> None:
    """The headline regression: same backend, same scale, different run_id."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "charts"
    paths = [_write(tmp_path, _matrix("baseline")), _write(tmp_path, _matrix("after-tuning"))]

    rc = plot_latency.main([str(p) for p in paths] + ["--out-dir", str(out)])
    assert rc == 0

    written = sorted(out.glob("*.png"))
    assert len(written) == 2, (
        f"expected one chart per run_id; got {[p.name for p in written]}. "
        "Pre-fix both rendered to stub_n64.png and one was lost."
    )


def test_reported_paths_are_exactly_the_paths_that_exist(tmp_path: Path, capsys) -> None:
    """Pre-fix stderr claimed two charts while one file existed.

    Anchored to the *discrepancy* rather than to a count, so a future change
    that reintroduces double-reporting fails here even if the count changes.
    """
    pytest.importorskip("matplotlib")
    out = tmp_path / "charts"
    paths = [_write(tmp_path, _matrix("baseline")), _write(tmp_path, _matrix("after-tuning"))]

    plot_latency.main([str(p) for p in paths] + ["--out-dir", str(out)])
    err = capsys.readouterr().err

    reported = {line.removeprefix("# wrote ").strip() for line in err.splitlines() if "wrote" in line}
    on_disk = {str(p) for p in out.glob("*.png")}
    assert reported == on_disk, (
        f"stderr reported {reported} but the directory holds {on_disk}"
    )


def test_duplicate_run_id_exits_2_and_writes_nothing(tmp_path: Path, capsys) -> None:
    """D-007's 'clear failure mode' — a run_id collision must be loud.

    No matplotlib guard: the check runs before `_maybe_plot`, so the contract
    holds on a box without the chart extra too.
    """
    out = tmp_path / "charts"
    same = _write(tmp_path, _matrix("baseline"))

    rc = plot_latency.main([str(same), str(same), "--out-dir", str(out)])

    assert rc == 2
    err = capsys.readouterr().err
    assert "baseline" in err, err
    assert not out.exists(), (
        "a rejected invocation must not leave a partial chart set behind — "
        "the guard runs before the output dir is created"
    )


def test_collision_guard_fires_before_the_table_is_printed(tmp_path: Path, capsys) -> None:
    """Fail before the work, not after it.

    A table on stdout alongside a nonzero exit reads as a successful run to
    anything capturing stdout, which is how this class of bug survives.
    """
    out = tmp_path / "charts"
    same = _write(tmp_path, _matrix("baseline"))

    rc = plot_latency.main([str(same), str(same), "--out-dir", str(out)])

    assert rc == 2
    assert capsys.readouterr().out == "", "no table should be printed on the reject path"


def test_rerunning_the_same_invocation_still_overwrites_its_own_output(tmp_path: Path) -> None:
    """Idempotent regeneration is preserved.

    D-007's force-check governs the results JSON, not derived plots. Breaking
    re-render would be a worse bug than the one #121 fixes, so it's pinned.
    """
    pytest.importorskip("matplotlib")
    out = tmp_path / "charts"
    p = _write(tmp_path, _matrix("baseline"))
    argv = [str(p), "--out-dir", str(out)]

    assert plot_latency.main(argv) == 0
    first = sorted(x.name for x in out.glob("*.png"))
    assert plot_latency.main(argv) == 0, "re-running the same invocation must not be rejected"
    assert sorted(x.name for x in out.glob("*.png")) == first


def test_distinct_backends_still_get_distinct_charts(tmp_path: Path) -> None:
    """The documented multi-backend example keeps working unchanged."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "charts"
    paths = [
        _write(tmp_path, _matrix("stub-100k", backend="stub")),
        _write(tmp_path, _matrix("pgvector-100k", backend="pgvector")),
    ]

    assert plot_latency.main([str(p) for p in paths] + ["--out-dir", str(out)]) == 0
    assert len(list(out.glob("*.png"))) == 2
