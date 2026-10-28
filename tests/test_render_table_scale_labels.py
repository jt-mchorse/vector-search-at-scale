"""One run_id at two scales gets two column labels (#287).

`_column_labels` told two runs of one backend apart by `run_id` only (#123). A
baseline at two scales -- one `run_id`, two results dirs -- repeated
`backend @ run_id`, and `plot_latency`'s collision guard keys on the chart
filename, which carries `n_vectors`, so both inputs passed it and the table
published identically labelled columns at exit 0. `n` is added only for that
case; every label #123 produced is unchanged.
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path

from vector_bench.harness import LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, _column_labels

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _matrix(run_id: str, backend: str, n: int) -> LoadMatrix:
    wl = Workload(n_vectors=n, dim=16, n_queries=20, top_k=5, seed=1)
    cell = LoadCell(
        run_id=run_id,
        backend=backend,
        workload=wl,
        concurrency=1,
        ingest_seconds=0.5,
        query_latency=LatencyStats(p50_ms=1.0, p95_ms=1.1, p99_ms=1.2, max_ms=1.5),
        mean_recall_at_k=1.0,
        throughput_qps=100.0,
        started_at="2026-10-28T00:00:00Z",
        git_sha=None,
    )
    return LoadMatrix(run_id=run_id, backend=backend, workload=wl, cells=(cell,))


def test_one_run_id_at_two_scales_gets_two_labels() -> None:
    labels = _column_labels([_matrix("baseline", "stub", 200), _matrix("baseline", "stub", 400)])
    assert labels == ["stub @ baseline n=200", "stub @ baseline n=400"]


def test_labels_are_unique_whenever_the_chart_names_are() -> None:
    """Every mix of backend, run_id and n whose chart filenames are distinct."""
    keys = list(itertools.product(["stub", "qdrant"], ["a", "b"], [200, 400]))
    for size in (2, 3, 4):
        for combo in itertools.combinations(keys, size):
            labels = _column_labels([_matrix(r, b, n) for b, r, n in combo])
            assert len(set(labels)) == len(labels), (combo, labels)


def test_labels_123_produced_are_unchanged() -> None:
    assert _column_labels([_matrix("a", "stub", 200)]) == ["stub"]
    assert _column_labels([_matrix("a", "stub", 200), _matrix("a", "qdrant", 400)]) == [
        "stub",
        "qdrant",
    ]
    assert _column_labels([_matrix("a", "stub", 200), _matrix("b", "stub", 400)]) == [
        "stub @ a",
        "stub @ b",
    ]


def test_plot_latency_publishes_two_labels_for_the_issue_case(tmp_path: Path) -> None:
    paths = []
    for n in (200, 400):
        out = tmp_path / f"n{n}"
        subprocess.run(
            [sys.executable, "-m", "vector_bench.cli", "load", "--backend", "stub", "--n", str(n),
             "--queries", "10", "--concurrency", "1", "--run-id", "baseline", "--results-dir", str(out)],
            check=True, capture_output=True,
        )  # fmt: skip
        paths.append(str(out / "baseline" / "matrix.json"))
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "plot_latency.py"), *paths,
         "--out-dir", str(tmp_path / "png")],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    header = next(line for line in proc.stdout.splitlines() if line.startswith("| concurrency"))
    assert "stub @ baseline n=200 p50 ms" in header
    assert "stub @ baseline n=400 p50 ms" in header
