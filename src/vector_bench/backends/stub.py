"""In-process pure-numpy backend.

This is the reference implementation the harness scores everything against.
It IS the ground truth (by construction, since `ground_truth_topk` scores
with the *same per-row GEMV* `corpus @ query` this backend uses — not a
batched GEMM, whose different float32 summation order would flip tie-boundary
neighbors; see harness.py / #72), so the stub achieves recall@k = 1.0 exactly
on every workload. Useful for two things:

- Exercising the full harness end-to-end in CI without any AWS bring-up.
- Sanity-checking new backends: a real engine should approach the stub's
  recall as `ef_search` / equivalent parameters are increased.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from vector_bench.types import check_ingest_shape


class StubBackend:
    name = "stub"

    def __init__(self) -> None:
        self._vectors: np.ndarray | None = None
        self._ids: list[str] = []

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        check_ingest_shape(vectors, ids)
        if self._vectors is None:
            self._vectors = vectors.astype(np.float32, copy=False)
            self._ids = list(ids)
        else:
            self._vectors = np.vstack([self._vectors, vectors.astype(np.float32, copy=False)])
            self._ids.extend(ids)

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self._vectors is None or not self._ids:
            return []
        sims = self._vectors @ vector
        top = np.argsort(-sims)[:k]
        return [(self._ids[int(i)], float(sims[int(i)])) for i in top]

    def close(self) -> None:
        self._vectors = None
        self._ids = []
