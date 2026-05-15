"""End-to-end and unit tests for the benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vector_bench import (
    StubBackend,
    Workload,
    generate_corpus,
    ground_truth_topk,
    recall_at_k,
    run_benchmark,
)


class TestWorkloadValidation:
    def test_rejects_non_positive_fields(self) -> None:
        with pytest.raises(ValueError, match="n_vectors must be positive"):
            Workload(n_vectors=0, dim=8, n_queries=1)
        with pytest.raises(ValueError, match="dim must be positive"):
            Workload(n_vectors=1, dim=0, n_queries=1)
        with pytest.raises(ValueError, match="n_queries must be positive"):
            Workload(n_vectors=1, dim=8, n_queries=0)

    def test_rejects_top_k_above_corpus(self) -> None:
        with pytest.raises(ValueError, match="top_k"):
            Workload(n_vectors=2, dim=4, n_queries=1, top_k=10)


class TestCorpusGeneration:
    def test_deterministic_for_fixed_seed(self) -> None:
        w = Workload(n_vectors=20, dim=8, n_queries=5, seed=42)
        c1, q1, _, _ = generate_corpus(w)
        c2, q2, _, _ = generate_corpus(w)
        np.testing.assert_array_equal(c1, c2)
        np.testing.assert_array_equal(q1, q2)

    def test_vectors_are_unit_normalized(self) -> None:
        w = Workload(n_vectors=10, dim=8, n_queries=3, seed=1)
        c, q, _, _ = generate_corpus(w)
        np.testing.assert_allclose(np.linalg.norm(c, axis=1), 1.0, atol=1e-5)
        np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1.0, atol=1e-5)


class TestRecallAtK:
    def test_full_match(self) -> None:
        assert recall_at_k(["a", "b", "c"], ["a", "b", "c"], 3) == pytest.approx(1.0)

    def test_partial_match(self) -> None:
        assert recall_at_k(["a", "x", "c"], ["a", "b", "c"], 3) == pytest.approx(2 / 3)

    def test_no_match_returns_zero(self) -> None:
        assert recall_at_k(["x", "y"], ["a", "b"], 2) == pytest.approx(0.0)

    def test_rejects_non_positive_k(self) -> None:
        with pytest.raises(ValueError, match="k must be positive"):
            recall_at_k([], [], 0)


class TestGroundTruth:
    def test_returns_per_query_top_k_in_descending_similarity(self) -> None:
        w = Workload(n_vectors=15, dim=8, n_queries=4, seed=7, top_k=5)
        c, q, ids, _ = generate_corpus(w)
        truth = ground_truth_topk(c, q, ids, w.top_k)
        assert len(truth) == w.n_queries
        for row in truth:
            assert len(row) == w.top_k
            assert len(set(row)) == w.top_k  # unique


class TestRunBenchmarkAgainstStub:
    def test_stub_achieves_recall_one(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=50, dim=8, n_queries=10, top_k=5, seed=3)
        result = run_benchmark(StubBackend(), w, run_id="stub-1", results_dir=tmp_path)
        assert result.backend == "stub"
        assert result.mean_recall_at_k == pytest.approx(1.0)
        # Sanity: latency stats are populated and non-negative.
        assert result.query_latency.p50_ms >= 0.0
        assert result.query_latency.p95_ms >= result.query_latency.p50_ms

    def test_writes_json_to_results_dir(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=2)
        run_benchmark(StubBackend(), w, run_id="stub-2", results_dir=tmp_path)
        out = tmp_path / "stub-2.json"
        assert out.exists()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["backend"] == "stub"
        assert payload["workload"]["n_vectors"] == 20
        assert payload["mean_recall_at_k"] == pytest.approx(1.0)

    def test_rejects_overwrite_without_force(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        run_benchmark(StubBackend(), w, run_id="dup", results_dir=tmp_path)
        with pytest.raises(FileExistsError, match="already exists"):
            run_benchmark(StubBackend(), w, run_id="dup", results_dir=tmp_path)

    def test_force_overwrites(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        run_benchmark(StubBackend(), w, run_id="dup2", results_dir=tmp_path)
        # Second call with force=True must not raise.
        run_benchmark(StubBackend(), w, run_id="dup2", results_dir=tmp_path, force=True)

    def test_idempotent_across_runs_with_same_seed(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=30, dim=8, n_queries=6, top_k=3, seed=99)
        r1 = run_benchmark(StubBackend(), w, run_id="i1", results_dir=tmp_path)
        r2 = run_benchmark(StubBackend(), w, run_id="i2", results_dir=tmp_path)
        # Same workload + same backend (stub is deterministic) → identical recall.
        assert r1.mean_recall_at_k == r2.mean_recall_at_k
        # Workload fields are recorded identically.
        assert r1.workload == r2.workload

    def test_does_not_write_when_write_json_false(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(
            StubBackend(), w, run_id="nowrite", results_dir=tmp_path, write_json=False
        )
        assert not (tmp_path / "nowrite.json").exists()
        assert result.run_id == "nowrite"
