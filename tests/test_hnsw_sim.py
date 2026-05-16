"""Tests for `HnswSimBackend` — the pure-numpy simulation used by the
HNSW parameter-tuning study (#3)."""

from __future__ import annotations

import numpy as np
import pytest

from vector_bench.backends import make_backend
from vector_bench.backends.hnsw_sim import HnswSimBackend


@pytest.fixture
def tiny_workload() -> tuple[np.ndarray, list[str], np.ndarray]:
    """Small deterministic workload — 100 unit vectors in 16 dims, 5 queries."""
    rng = np.random.default_rng(42)
    vecs = rng.standard_normal((100, 16), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    queries = rng.standard_normal((5, 16), dtype=np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True) + 1e-12
    ids = [f"id{i:04d}" for i in range(100)]
    return vecs, ids, queries


def test_construct_and_ingest(tiny_workload):
    vecs, ids, _ = tiny_workload
    backend = HnswSimBackend(M=8, ef_construction=50, ef_search=20)
    backend.ingest(vecs, ids)
    backend.close()


def test_query_returns_k_results(tiny_workload):
    vecs, ids, queries = tiny_workload
    backend = HnswSimBackend(M=8, ef_construction=50, ef_search=30)
    backend.ingest(vecs, ids)
    hits = backend.query(queries[0], k=5)
    assert len(hits) == 5
    assert all(isinstance(hid, str) for hid, _ in hits)
    assert all(isinstance(score, float) for _, score in hits)
    # Results are sorted descending by similarity.
    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True)


def test_empty_index_returns_empty_query():
    backend = HnswSimBackend()
    assert backend.query(np.zeros(16, dtype=np.float32), k=5) == []


def test_make_backend_routes_to_hnsw_sim():
    backend = make_backend("hnsw-sim", M=4, ef_construction=20, ef_search=10)
    assert backend.name == "hnsw-sim"


def test_close_is_idempotent(tiny_workload):
    vecs, ids, _ = tiny_workload
    backend = HnswSimBackend()
    backend.ingest(vecs, ids)
    backend.close()
    backend.close()  # second close must not raise


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_nonpositive_params(bad):
    with pytest.raises(ValueError, match="must be positive"):
        HnswSimBackend(M=bad)
    with pytest.raises(ValueError, match="must be positive"):
        HnswSimBackend(ef_construction=bad)
    with pytest.raises(ValueError, match="must be positive"):
        HnswSimBackend(ef_search=bad)


def test_ingest_mismatch_raises():
    backend = HnswSimBackend()
    vecs = np.zeros((5, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="ingest mismatch"):
        backend.ingest(vecs, ["a", "b"])  # 2 ids for 5 rows


def test_recall_non_decreasing_in_ef_search(tiny_workload):
    """The whole point of the simulation: bigger ef_search → higher recall.

    Compares recall against ground-truth top-5 at three increasing ef_search
    values. Allows a small tolerance for ties in low-recall regimes but
    asserts a strict floor at the largest ef_search.
    """
    vecs, ids, queries = tiny_workload
    # Ground truth via brute force.
    sims_all = queries @ vecs.T
    truth = np.argsort(-sims_all, axis=1)[:, :5]
    truth_sets = [set(int(idx) for idx in row) for row in truth]
    id_to_idx = {v: i for i, v in enumerate(ids)}

    def measure(ef_search: int) -> float:
        backend = HnswSimBackend(M=8, ef_construction=80, ef_search=ef_search, seed=1)
        backend.ingest(vecs, ids)
        recalls = []
        for q_idx in range(len(queries)):
            hits = backend.query(queries[q_idx], k=5)
            hit_idxs = set(id_to_idx[hid] for hid, _ in hits)
            recalls.append(len(hit_idxs & truth_sets[q_idx]) / 5.0)
        return float(np.mean(recalls))

    low = measure(ef_search=8)
    mid = measure(ef_search=32)
    high = measure(ef_search=80)  # essentially exhaustive
    assert high >= mid >= low
    # At ef_search ≈ n_vectors, recall is near-perfect — the simulation
    # collapses to brute force.
    assert high >= 0.7
