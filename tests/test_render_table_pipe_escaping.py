"""A free-form label must not add a column to the rendered GFM table (#125).

`render_table` builds its separator row from `len(header)` — the list length —
while the header row is a rendered string. A `|` inside `backend` or `run_id`
therefore added a rendered column the separator lacked, and the two rows
desynced into a table GitHub draws as a mangled grid. That is the failure mode
that survives review: the reader sees a broken table rather than an obviously
wrong number.

The docstring on `render_table` says its output is "designed to drop into a
README", and the committed "Latency under load" table comes from it.
"""

from __future__ import annotations

import pytest

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, render_table

WORKLOAD = Workload(n_vectors=100, dim=8, n_queries=10, top_k=5, seed=1, concurrency=1)
LATENCY = LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0)


def _matrix(run_id: str, backend: str) -> LoadMatrix:
    cell = LoadCell(
        run_id=run_id,
        backend=backend,
        workload=WORKLOAD,
        concurrency=1,
        ingest_seconds=1.0,
        query_latency=LATENCY,
        mean_recall_at_k=0.9,
        throughput_qps=10.0,
        started_at="2026-01-01T00:00:00Z",
        git_sha=None,
    )
    return LoadMatrix(run_id=run_id, backend=backend, workload=WORKLOAD, cells=[cell])


def _rows(matrices: list[LoadMatrix]) -> tuple[str, str]:
    lines = [line for line in render_table(matrices).splitlines() if line.startswith("|")]
    return lines[0], lines[1]


def _columns(row: str) -> int:
    """GFM column count: cells between the outer pipes, `\\|` not counting."""
    return len(row.strip().strip("|").replace("\\|", "").split("|"))


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_pipe_in_run_id_keeps_header_and_separator_aligned():
    """The disambiguated `{backend} @ {run_id}` label path."""
    header, separator = _rows([_matrix("r1|evil", "hnsw"), _matrix("r2", "hnsw")])

    assert _columns(header) == _columns(separator)


def test_pipe_in_backend_keeps_header_and_separator_aligned():
    """The short-label fallback path, taken when a backend appears once."""
    header, separator = _rows([_matrix("r1", "hn|sw")])

    assert _columns(header) == _columns(separator)


def test_pipe_in_backend_on_the_disambiguated_path_too():
    header, separator = _rows([_matrix("r1", "hn|sw"), _matrix("r2", "hn|sw")])

    assert _columns(header) == _columns(separator)


def test_the_pipe_is_escaped_not_stripped():
    """GitHub renders `\\|` as a literal pipe, so the real name still shows."""
    header, _ = _rows([_matrix("r1", "hn|sw")])

    assert "hn\\|sw" in header
    assert "hn|sw" not in header.replace("hn\\|sw", "")


@pytest.mark.parametrize("pipes", ["|", "||", "a|b|c"])
def test_multiple_pipes_are_all_escaped(pipes):
    header, separator = _rows([_matrix(f"r{pipes}", "hnsw"), _matrix("r2", "hnsw")])

    assert _columns(header) == _columns(separator)


# ---------------------------------------------------------------------------
# Locks: clean output must not move
# ---------------------------------------------------------------------------


def test_clean_names_are_byte_identical_on_the_short_label_path():
    header, separator = _rows([_matrix("r1", "hnsw")])

    assert header == "| concurrency | hnsw p50 ms | hnsw p95 ms | hnsw p99 ms |"
    assert separator == "| --- | --- | --- | --- |"


def test_clean_names_are_byte_identical_on_the_disambiguated_path():
    header, _ = _rows([_matrix("r1", "hnsw"), _matrix("r2", "hnsw")])

    assert header == (
        "| concurrency "
        "| hnsw @ r1 p50 ms | hnsw @ r1 p95 ms | hnsw @ r1 p99 ms "
        "| hnsw @ r2 p50 ms | hnsw @ r2 p95 ms | hnsw @ r2 p99 ms |"
    )


def test_distinct_backends_still_use_the_short_label():
    """#123's conditional fallback: only an ambiguous backend gets the run_id."""
    header, _ = _rows([_matrix("r1", "hnsw"), _matrix("r2", "ivf")])

    assert "@" not in header


def test_empty_matrix_list_is_unchanged():
    assert render_table([]) == "_(no matrices to render)_"
