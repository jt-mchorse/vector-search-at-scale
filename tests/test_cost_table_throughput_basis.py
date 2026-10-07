"""The cost doc names the throughput basis it actually amortises over (#180).

It said the `$/query` column used c001's "single-client p50". `throughput_qps`
is `n_queries / query_elapsed_s` (`load.py`), the measured rate, which pays the
per-query overhead a 1000 / p50 rate leaves out: the committed c001 reads
1623.5 qps against 1634.2 from its own p50.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from vector_bench import load

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "cost_per_query.md"


def test_throughput_is_computed_from_wall_clock_not_p50() -> None:
    src = inspect.getsource(load)
    assert "throughput_qps = workload.n_queries / query_elapsed_s" in src


def test_the_committed_c001_rate_is_not_the_p50_rate() -> None:
    # The label's subject, measured: if these ever coincide the distinction
    # stops mattering, and this arm says so.
    for path in sorted((ROOT / "results" / "load").glob("*/c001.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        from_p50 = 1000.0 / run["query_latency"]["p50_ms"]
        assert abs(run["throughput_qps"] - from_p50) > 1.0, path


def test_the_doc_does_not_call_the_basis_p50() -> None:
    bullet = next(
        line
        for line in DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("- **Throughput**")
    )
    assert "single-client p50" not in bullet
    assert "measured rate" in bullet
    assert "÷ the query phase's wall-clock at concurrency 1" in bullet
