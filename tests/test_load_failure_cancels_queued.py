"""One failed query cancels the queued rest of its level (#186).

Every query of a level is submitted up front, and the executor's `with` exit
waited on all of them, so a failure surfaced only after the whole remaining
level had run against the backend: 200 of 200 calls, 5.5 s on a 50 ms stub.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from vector_bench.load import _execute_at_concurrency


class _Backend:
    def __init__(self, fail_first: bool) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self._lock = threading.Lock()

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        time.sleep(0.05)
        if self.fail_first and first:
            raise RuntimeError("backend down")
        return []


N = 200


def test_a_failure_cancels_the_queued_queries_and_surfaces_promptly() -> None:
    backend = _Backend(fail_first=True)
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="backend down"):
        _execute_at_concurrency(backend, np.zeros((N, 4), dtype=np.float32), [[]] * N, 10, 2)
    assert time.perf_counter() - started < 1.5
    assert backend.calls <= 10, backend.calls


def test_a_healthy_level_still_runs_every_query() -> None:
    backend = _Backend(fail_first=False)
    latencies, recalls = _execute_at_concurrency(
        backend, np.zeros((20, 4), dtype=np.float32), [[]] * 20, 10, 4
    )
    assert backend.calls == 20
    assert len(latencies) == len(recalls) == 20
    assert all(lat > 0 for lat in latencies)
