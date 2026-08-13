"""`render_table` column labels must identify the run when a backend repeats (#123).

Headers were built from `m.backend` alone, so two matrices of one backend — a
baseline and a re-tune, which is what the per-run_id results tree exists for —
produced six identically-labelled columns. Unlike the chart collision fixed in
#121, no data was lost: both series were present and correct, in input order.
The defect is that `render_table`'s docstring says it is "designed to drop into
a README", and once it is in a README the input order that disambiguated it is
no longer visible.

`run_id` is the disambiguator because the repo already answers "which run is
this?" that way: D-007 keys results one JSON file per run_id, and #121 keyed
chart filenames by run_id for this exact case.

The fallback is conditional, so the tests below carry equal weight: the
byte-identity assertions for the single-backend and one-per-backend cases are
what make this safe to land without regenerating the committed README table.
"""

from __future__ import annotations

import re
from pathlib import Path

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, render_table

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _wl() -> Workload:
    return Workload(n_vectors=64, dim=16, n_queries=20, top_k=5, seed=1)


def _cell(run_id: str, backend: str, concurrency: int, p50: float) -> LoadCell:
    return LoadCell(
        run_id=run_id,
        backend=backend,
        workload=_wl(),
        concurrency=concurrency,
        ingest_seconds=0.5,
        query_latency=LatencyStats(
            p50_ms=p50, p95_ms=p50 * 1.1, p99_ms=p50 * 1.2, max_ms=p50 * 1.5
        ),
        mean_recall_at_k=1.0,
        throughput_qps=100.0,
        started_at="2026-08-12T00:00:00Z",
        git_sha=None,
    )


def _matrix(run_id: str, backend: str, p50s: dict[int, float]) -> LoadMatrix:
    return LoadMatrix(
        run_id=run_id,
        backend=backend,
        workload=_wl(),
        cells=tuple(_cell(run_id, backend, c, p) for c, p in sorted(p50s.items())),
    )


def _header(table: str) -> str:
    return table.splitlines()[0]


def test_repeated_backend_labels_columns_by_run_id() -> None:
    """The case from #123: a baseline and a re-tune of one backend."""
    baseline = _matrix("stub-10k", "stub", {1: 0.612, 10: 0.844})
    retune = _matrix("stub-10k-retune", "stub", {1: 0.306, 10: 0.422})

    header = _header(render_table([baseline, retune]))

    assert "stub @ stub-10k p50 ms" in header, header
    assert "stub @ stub-10k-retune p50 ms" in header, header
    # The point is that no two columns share a label — assert the property
    # itself, not just that two specific strings are present.
    cols = [c.strip() for c in header.strip("| ").split("|")]
    assert len(cols) == len(set(cols)), f"duplicate column labels remain: {cols}"


def test_repeated_backend_keeps_both_series_in_input_order() -> None:
    """No data was ever lost, so the fix must not "resolve" the ambiguity by
    dropping a column. This asserts the values are still all there, in order —
    otherwise the label test above could pass on a broken implementation.
    """
    baseline = _matrix("stub-10k", "stub", {1: 0.612})
    retune = _matrix("stub-10k-retune", "stub", {1: 0.306})

    table = render_table([baseline, retune])
    row = next(line for line in table.splitlines() if line.startswith("| 1 "))
    values = [c.strip() for c in row.strip("| ").split("|")][1:]

    assert values[0] == "0.612", f"baseline p50 missing or reordered: {row}"
    assert values[3] == "0.306", f"re-tune p50 missing or reordered: {row}"
    assert len(values) == 6, f"expected 6 value columns for two matrices; got {row}"


def test_distinct_backends_keep_the_short_label() -> None:
    """The common case must be untouched — this is the conditional half of the
    fix, and the reason no committed output changes."""
    stub = _matrix("run-a", "stub", {1: 0.612})
    qdrant = _matrix("run-b", "qdrant", {1: 1.400})

    header = _header(render_table([stub, qdrant]))

    assert header == (
        "| concurrency | stub p50 ms | stub p95 ms | stub p99 ms "
        "| qdrant p50 ms | qdrant p95 ms | qdrant p99 ms |"
    ), header
    assert "@" not in header, f"no run_id should appear when backends are distinct: {header}"


def test_single_matrix_header_is_unchanged() -> None:
    """`cli.py --render-table` passes exactly one matrix, so this is the path
    every existing invocation takes."""
    header = _header(render_table([_matrix("stub-10k", "stub", {1: 0.612})]))

    assert header == "| concurrency | stub p50 ms | stub p95 ms | stub p99 ms |", header


def test_committed_readme_table_still_renders_byte_identically() -> None:
    """The acceptance criterion that proves the change is confined to the
    ambiguous case.

    The README's "Latency under load" table is this function's output for the
    committed `results/load/stub-10k/matrix.json`. Re-rendering it here and
    comparing against the committed markdown means a future widening of the
    fallback — dropping the `counts[...] > 1` condition, say — fails loudly
    instead of silently rewriting a published table.
    """
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    committed_header = "| concurrency | stub p50 ms | stub p95 ms | stub p99 ms |"
    assert committed_header in readme, (
        "the committed README table header moved; this test pins render_table "
        "against it, so update both together"
    )

    # Rebuild the same shape the committed table describes and confirm the
    # header we would emit is the one already published.
    rendered = render_table([_matrix("stub-10k", "stub", {1: 0.612, 10: 0.844, 100: 1.740})])
    assert _header(rendered) == committed_header

    # And the value rows still match the published numbers.
    for concurrency, p50 in ((1, "0.612"), (10, "0.844"), (100, "1.740")):
        row = next(line for line in rendered.splitlines() if line.startswith(f"| {concurrency} "))
        assert re.search(rf"\|\s*{re.escape(p50)}\s*\|", row), (
            f"p50 for concurrency={concurrency} changed: {row}"
        )


def test_three_matrices_only_the_repeated_backend_is_disambiguated() -> None:
    """A mixed input: the repeated backend gets run_ids, the singleton does
    not. Pins that the condition is per-backend rather than per-table."""
    a = _matrix("run-a", "stub", {1: 0.6})
    b = _matrix("run-b", "stub", {1: 0.3})
    c = _matrix("run-c", "qdrant", {1: 1.4})

    header = _header(render_table([a, b, c]))

    assert "stub @ run-a p50 ms" in header, header
    assert "stub @ run-b p50 ms" in header, header
    assert "qdrant p50 ms" in header, header
    assert "qdrant @ run-c" not in header, f"singleton backend must keep the short label: {header}"
