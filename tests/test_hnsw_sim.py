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


# Issue #31: completes the #29 sweep (`Workload`, `recall_at_k`). Sign-only
# accepted `True` (silently bound M=1 → recall silently collapsed), float
# (silently bound, then numpy slice errors deep in ingest), and NaN/Inf
# (silent type confusion surfacing as cryptic numpy errors).
_BAD_INT = [1.5, float("nan"), float("inf"), True, "16"]


@pytest.mark.parametrize("bad", _BAD_INT)
def test_hnsw_sim_M_must_be_int(bad):
    with pytest.raises(ValueError, match="M must be an int"):
        HnswSimBackend(M=bad)


@pytest.mark.parametrize("bad", _BAD_INT)
def test_hnsw_sim_ef_construction_must_be_int(bad):
    with pytest.raises(ValueError, match="ef_construction must be an int"):
        HnswSimBackend(ef_construction=bad)


@pytest.mark.parametrize("bad", _BAD_INT)
def test_hnsw_sim_ef_search_must_be_int(bad):
    with pytest.raises(ValueError, match="ef_search must be an int"):
        HnswSimBackend(ef_search=bad)


@pytest.mark.parametrize("good", [1, 8, 16, 32, 64])
def test_hnsw_sim_accepts_valid_int_params(good):
    backend = HnswSimBackend(M=good, ef_construction=good * 2, ef_search=good)
    assert backend.M == good  # noqa: SIM300
    assert backend.ef_construction == good * 2  # noqa: SIM300
    assert backend.ef_search == good  # noqa: SIM300


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


def test_recall_non_decreasing_in_m(tiny_workload):
    """#61: a denser graph (higher M) must not *lower* recall.

    The docstring contract is "Higher M = denser graph = better recall at
    small ef_search." The old beam-search iteration cap `ef // M` shrank the
    exploration budget as M grew, so recall fell when M rose (e.g. M=8 below
    M=4) — the opposite of the contract. With the cap decoupled from M, recall
    is non-decreasing in M. Held at a small, fixed ef_search where the dip was
    most pronounced.
    """
    vecs, ids, queries = tiny_workload
    sims_all = queries @ vecs.T
    truth = np.argsort(-sims_all, axis=1)[:, :5]
    truth_sets = [set(int(idx) for idx in row) for row in truth]
    id_to_idx = {v: i for i, v in enumerate(ids)}

    def measure(m: int) -> float:
        backend = HnswSimBackend(M=m, ef_construction=80, ef_search=16, seed=1)
        backend.ingest(vecs, ids)
        recalls = []
        for q_idx in range(len(queries)):
            hits = backend.query(queries[q_idx], k=5)
            hit_idxs = set(id_to_idx[hid] for hid, _ in hits)
            recalls.append(len(hit_idxs & truth_sets[q_idx]) / 5.0)
        return float(np.mean(recalls))

    recalls = [measure(m) for m in (4, 8, 16, 32)]
    # Non-decreasing across the M sweep (tiny tolerance for ties/plateau).
    for lo, hi in zip(recalls, recalls[1:], strict=False):
        assert hi >= lo - 1e-9, f"recall dropped as M increased: {recalls}"


@pytest.fixture
def order_sensitive_workload() -> tuple[np.ndarray, list[str], np.ndarray]:
    """A workload where the pre-fix shared-RNG bug actually changes top-k.

    `tiny_workload` is too small/connected — the beam converges to the same
    top-k regardless of entry points, so it can't *discriminate* the bug. With
    300 vectors in 24 dims at ef_search=20 the beam does not fully converge, so
    the entry points (and therefore the result) genuinely depend on prior RNG
    consumption under the old code. Verified: the pre-fix `query()` returns
    different top-k for query 1 depending on whether query 0 ran first.
    """
    rng = np.random.default_rng(7)
    vecs = rng.standard_normal((300, 24), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    queries = rng.standard_normal((5, 24), dtype=np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True) + 1e-12
    ids = [f"c{i:08d}" for i in range(300)]
    return vecs, ids, queries


def test_query_result_is_independent_of_issue_order(order_sensitive_workload):
    """Regression for #65: a query's result must be a pure function of
    (seed, params, vector) — NOT of how many queries ran before it.

    Pre-fix, `query()` consumed the shared, ingest-advanced `self._rng`, so the
    same query vector returned different top-k (and recall) depending on its
    position in the issue sequence. Compare query index 1 issued *after* query 0
    against the same query issued *first* on a fresh, identically seeded backend.
    """
    vecs, ids, queries = order_sensitive_workload

    def fresh_backend() -> HnswSimBackend:
        b = HnswSimBackend(M=8, ef_construction=40, ef_search=20, seed=42)
        b.ingest(vecs, ids)
        return b

    after = fresh_backend()
    after.query(queries[0], k=10)  # consume RNG with a prior query first
    result_after = after.query(queries[1], k=10)

    first = fresh_backend()
    result_first = first.query(queries[1], k=10)  # same query, issued first

    assert result_after == result_first


def test_query_does_not_consume_shared_rng(tiny_workload):
    """Root-cause guard for #65: `query()` must not advance the shared
    `self._rng`. The bug *was* that it did, coupling each query's entry points
    to prior consumption. This catches a regression even on workloads where the
    beam happens to converge to the same top-k regardless of entry points."""
    vecs, ids, queries = tiny_workload
    backend = HnswSimBackend(M=8, ef_construction=40, ef_search=20, seed=42)
    backend.ingest(vecs, ids)
    state_before = backend._rng.bit_generator.state
    backend.query(queries[0], k=10)
    assert backend._rng.bit_generator.state == state_before


def test_query_is_deterministic_across_backends(tiny_workload):
    """Two independently-built backends with the same seed return identical
    results for the same query (no hidden dependence on object identity)."""
    vecs, ids, queries = tiny_workload
    a = HnswSimBackend(M=8, ef_construction=40, ef_search=20, seed=42)
    a.ingest(vecs, ids)
    b = HnswSimBackend(M=8, ef_construction=40, ef_search=20, seed=42)
    b.ingest(vecs, ids)
    assert a.query(queries[2], k=10) == b.query(queries[2], k=10)
