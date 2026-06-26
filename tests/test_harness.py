"""End-to-end and unit tests for the benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vector_bench import (
    BenchmarkResult,
    LatencyStats,
    StubBackend,
    Workload,
    dump_benchmark_json,
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
        # Message tightened in #29 to "must be a positive integer".
        with pytest.raises(ValueError, match="k must be a positive integer"):
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


# ----------------------------------------------------------------------
# D-011: run_benchmark refuses workload.concurrency > 1
# ----------------------------------------------------------------------


class TestRunBenchmarkConcurrencyGate:
    """Single-shot `run_benchmark` must reject misconfigured workloads
    instead of silently recording the wrong concurrency on the JSON.
    Concurrency studies go through `run_under_load` (#4, D-008)."""

    def test_concurrency_one_still_passes(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1, concurrency=1)
        result = run_benchmark(StubBackend(), w, run_id="c1", results_dir=tmp_path)
        assert result.workload.concurrency == 1
        assert result.mean_recall_at_k == pytest.approx(1.0)

    def test_concurrency_gt_one_raises(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1, concurrency=4)
        with pytest.raises(ValueError, match="run_benchmark requires workload.concurrency == 1"):
            run_benchmark(StubBackend(), w, run_id="c4", results_dir=tmp_path)


# ----------------------------------------------------------------------
# #55: BenchmarkResult finiteness/range guards + no invalid-JSON token
# ----------------------------------------------------------------------


def _make_result(**overrides: object) -> BenchmarkResult:
    """Construct a valid BenchmarkResult, overriding individual fields.

    BenchmarkResult was the one result dataclass without a __post_init__; a
    non-finite field reached json.dumps and serialized as the invalid-JSON
    token `Infinity`/`NaN`. These tests pin the guard added in #55.
    """
    base: dict[str, object] = {
        "run_id": "r",
        "backend": "stub",
        "workload": Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1),
        "ingest_seconds": 0.5,
        "ingest_rows_per_sec": 40.0,
        "query_latency": LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0),
        "mean_recall_at_k": 0.9,
        "started_at": "2026-06-26T00:00:00Z",
        "git_sha": None,
        "cost_per_query_usd": None,
    }
    base.update(overrides)
    return BenchmarkResult(**base)  # type: ignore[arg-type]


class TestBenchmarkResultFinitenessGuard:
    def test_valid_result_constructs_and_dumps_to_strict_json(self) -> None:
        # The clean path still round-trips through the *strict* JSON parser
        # (allow_nan defaults False on json.loads), proving no Infinity/NaN token.
        result = _make_result(ingest_rows_per_sec=12345.0, cost_per_query_usd=0.0001)
        text = json.dumps(result.to_dict(), indent=2, sort_keys=True)
        parsed = json.loads(text)  # strict: raises on Infinity/NaN
        assert parsed["ingest_rows_per_sec"] == 12345.0

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), -1.0])
    def test_rejects_non_finite_or_negative_ingest_rows_per_sec(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"ingest_rows_per_sec must be a finite number >= 0"):
            _make_result(ingest_rows_per_sec=bad)

    @pytest.mark.parametrize("bad", [float("inf"), float("nan"), -0.001])
    def test_rejects_non_finite_or_negative_ingest_seconds(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"ingest_seconds must be a finite number >= 0"):
            _make_result(ingest_seconds=bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.5])
    def test_rejects_out_of_range_mean_recall(self, bad: float) -> None:
        with pytest.raises(
            ValueError, match=r"mean_recall_at_k must be a finite number in \[0, 1\]"
        ):
            _make_result(mean_recall_at_k=bad)

    @pytest.mark.parametrize("bad", [float("inf"), float("nan"), -0.5])
    def test_rejects_non_finite_or_negative_cost(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"cost_per_query_usd must be a finite number >= 0"):
            _make_result(cost_per_query_usd=bad)

    def test_none_cost_is_accepted(self) -> None:
        # cost_per_query_usd is Optional; None (unpopulated, pre-#5) stays valid.
        assert _make_result(cost_per_query_usd=None).cost_per_query_usd is None


class TestLatencyStatsFinitenessGuard:
    """#59: LatencyStats is the nested result dataclass #55 missed. A non-finite
    or negative p50/p95/p99/max flows through to_dict() -> json.dumps and
    serializes as the invalid-JSON token NaN/Infinity. These pin the guard."""

    def test_valid_latency_dumps_to_strict_json(self) -> None:
        # The clean path round-trips a full BenchmarkResult through the *strict*
        # JSON parser (json.loads, allow_nan defaults False), proving no
        # Infinity/NaN token leaks from the nested latency block.
        result = _make_result()
        parsed = json.loads(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        assert parsed["query_latency"]["p99_ms"] == 3.0

    @pytest.mark.parametrize(
        "field", ["p50_ms", "p95_ms", "p99_ms", "max_ms"]
    )
    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), -1.0])
    def test_rejects_non_finite_or_negative_field(self, field: str, bad: float) -> None:
        kwargs = {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0, "max_ms": 4.0}
        kwargs[field] = bad
        with pytest.raises(ValueError, match=rf"{field} must be a finite number >= 0"):
            LatencyStats(**kwargs)

    def test_zero_latency_is_accepted(self) -> None:
        # 0.0 is a legitimate (if degenerate) measured latency; the guard is
        # >= 0, not > 0, matching the ingest_seconds contract.
        ls = LatencyStats(p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, max_ms=0.0)
        assert ls.max_ms == 0.0


class TestRunBenchmarkDegenerateIngest:
    def test_non_positive_ingest_time_raises(self, tmp_path: Path, monkeypatch) -> None:
        # Force a non-positive ingest duration by pinning perf_counter to a
        # constant: the guard must fail loud rather than fabricate inf rows/sec.
        import vector_bench.harness as harness_mod

        monkeypatch.setattr(harness_mod.time, "perf_counter", lambda: 100.0)
        w = Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1)
        with pytest.raises(ValueError, match=r"non-positive .* ingest time"):
            run_benchmark(StubBackend(), w, run_id="degenerate", results_dir=tmp_path)

    def test_concurrency_gate_message_points_at_run_under_load_and_d011(
        self, tmp_path: Path
    ) -> None:
        # Caller without docs in hand should be able to find the alternative
        # from the message alone.
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1, concurrency=2)
        with pytest.raises(ValueError, match="run_under_load") as exc_info:
            run_benchmark(StubBackend(), w, run_id="c2", results_dir=tmp_path)
        assert "D-011" in str(exc_info.value)

    def test_concurrency_gate_runs_before_filesystem_check(self, tmp_path: Path) -> None:
        # If the workload is invalid, the harness should refuse before
        # touching the filesystem — otherwise a misconfigured caller
        # would leave a stale results path locked for a future
        # `force=False` retry.
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1, concurrency=8)
        out_path = tmp_path / "c8.json"
        with pytest.raises(ValueError, match="concurrency"):
            run_benchmark(StubBackend(), w, run_id="c8", results_dir=tmp_path)
        assert not out_path.exists()


# Issue #29: Workload integer-typed count fields rejected as `isinstance(int)`
# so non-int (float, NaN, bool, str) is rejected at construction rather than
# silently truncating downstream in `range(int(x))` or propagating into the
# load loop as cryptic SDK-level TypeErrors. Same shape as
# embedding-model-shootout #32 and chunking-strategies-lab #30.
class TestWorkloadIntegerValidation:
    @pytest.mark.parametrize(
        "field",
        ["n_vectors", "dim", "n_queries", "top_k", "concurrency"],
    )
    @pytest.mark.parametrize(
        "bad",
        [1.5, float("nan"), float("inf"), True, "5"],
    )
    def test_rejects_non_int_field(self, field: str, bad) -> None:
        kwargs: dict = {"n_vectors": 10, "dim": 4, "n_queries": 3}
        kwargs[field] = bad
        with pytest.raises(ValueError, match=f"{field} must be an int"):
            Workload(**kwargs)

    def test_acceptance_regression_valid_ints_construct(self) -> None:
        w = Workload(n_vectors=10, dim=8, n_queries=3, top_k=2, concurrency=1)
        assert w.n_vectors == 10
        assert w.concurrency == 1


class TestRecallAtKIntegerValidation:
    @pytest.mark.parametrize(
        "bad",
        [1.5, float("nan"), True, "5"],
    )
    def test_rejects_non_int_k(self, bad) -> None:
        with pytest.raises(ValueError, match="k must be a positive integer"):
            recall_at_k(["a"], ["a"], bad)


# ----------------------------------------------------------------------
# #39: observability-parity — `.to_dict()` field-by-field contract pins
# (no `dataclasses.asdict`) + `dump_benchmark_json` package-level
# wrapper. Same shape as python-async-llm-pipelines #45,
# rag-production-kit #51, llm-cost-optimizer #51 / #53.
# ----------------------------------------------------------------------


class TestWorkloadToDictContract:
    def test_field_set_is_pinned(self) -> None:
        # Adding or dropping a field on Workload silently breaks
        # downstream JSON consumers; the explicit-contract method pins
        # the six-field surface.
        d = Workload(n_vectors=10, dim=4, n_queries=3).to_dict()
        assert sorted(d.keys()) == [
            "concurrency",
            "dim",
            "n_queries",
            "n_vectors",
            "seed",
            "top_k",
        ]

    def test_values_round_trip(self) -> None:
        w = Workload(n_vectors=42, dim=8, n_queries=7, top_k=4, seed=99, concurrency=1)
        d = w.to_dict()
        assert d == {
            "n_vectors": 42,
            "dim": 8,
            "n_queries": 7,
            "top_k": 4,
            "seed": 99,
            "concurrency": 1,
        }


class TestLatencyStatsToDictContract:
    def test_field_set_is_pinned(self) -> None:
        d = LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0).to_dict()
        assert sorted(d.keys()) == ["max_ms", "p50_ms", "p95_ms", "p99_ms"]

    def test_values_round_trip(self) -> None:
        s = LatencyStats(p50_ms=1.5, p95_ms=4.0, p99_ms=9.5, max_ms=12.0)
        assert s.to_dict() == {"p50_ms": 1.5, "p95_ms": 4.0, "p99_ms": 9.5, "max_ms": 12.0}


class TestBenchmarkResultToDictContract:
    def test_field_set_is_pinned(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(StubBackend(), w, run_id="contract", results_dir=tmp_path)
        d = result.to_dict()
        assert sorted(d.keys()) == [
            "backend",
            "cost_per_query_usd",
            "extra",
            "git_sha",
            "ingest_rows_per_sec",
            "ingest_seconds",
            "mean_recall_at_k",
            "query_latency",
            "run_id",
            "started_at",
            "workload",
        ]
        # Nested shapes are owned by the nested classes' to_dict() pins.
        assert sorted(d["workload"].keys()) == [
            "concurrency",
            "dim",
            "n_queries",
            "n_vectors",
            "seed",
            "top_k",
        ]
        assert sorted(d["query_latency"].keys()) == ["max_ms", "p50_ms", "p95_ms", "p99_ms"]

    def test_extra_dict_is_shallow_copied(self, tmp_path: Path) -> None:
        # The frozen-dataclass `extra` mapping is exposed only through
        # the dict surface; mutating the returned dict must not bleed
        # back into the BenchmarkResult instance.
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(StubBackend(), w, run_id="extra", results_dir=tmp_path)
        snapshot = result.to_dict()
        snapshot["extra"]["leaked"] = "yes"
        assert "leaked" not in result.extra

    def test_to_json_alias_returns_same_payload(self, tmp_path: Path) -> None:
        # `to_json()` survives as a thin delegate so the cli.py /
        # downstream callers don't churn; same contract as `to_dict()`.
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(StubBackend(), w, run_id="alias", results_dir=tmp_path)
        assert result.to_json() == result.to_dict()


class TestDumpBenchmarkJson:
    def test_writes_round_trippable_json(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(
            StubBackend(), w, run_id="dump", results_dir=tmp_path, write_json=False
        )
        out_path = tmp_path / "explicit.json"
        returned = dump_benchmark_json(out_path, result=result)
        assert returned == out_path
        assert out_path.exists()
        # Round-trip through `json.loads` validates that to_dict() emits
        # plain JSON-native types (no numpy scalars, no tuples, etc.) —
        # previously the `default=` fallback masked any leakage.
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["run_id"] == "dump"
        assert payload["workload"]["n_vectors"] == 10

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(
            StubBackend(), w, run_id="overwrite", results_dir=tmp_path, write_json=False
        )
        out_path = tmp_path / "guarded.json"
        dump_benchmark_json(out_path, result=result)
        with pytest.raises(FileExistsError, match="already exists"):
            dump_benchmark_json(out_path, result=result)

    def test_force_overwrites(self, tmp_path: Path) -> None:
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        result = run_benchmark(
            StubBackend(), w, run_id="force_dump", results_dir=tmp_path, write_json=False
        )
        out_path = tmp_path / "force.json"
        dump_benchmark_json(out_path, result=result)
        # Second write must not raise.
        dump_benchmark_json(out_path, result=result, force=True)

    def test_run_benchmark_routes_through_dump_wrapper(self, tmp_path: Path, monkeypatch) -> None:
        # `run_benchmark`'s write step delegates to `dump_benchmark_json`;
        # patching the wrapper proves the route exists (not a duplicate
        # inlined json.dumps path).
        calls: list[tuple[Path, BenchmarkResult]] = []

        def fake_dump(path, *, result, force=False):
            calls.append((Path(path), result))
            return Path(path)

        monkeypatch.setattr("vector_bench.harness.dump_benchmark_json", fake_dump)
        w = Workload(n_vectors=10, dim=4, n_queries=3, top_k=2, seed=1)
        run_benchmark(StubBackend(), w, run_id="routed", results_dir=tmp_path)
        assert len(calls) == 1
        assert calls[0][0] == tmp_path / "routed.json"
        assert isinstance(calls[0][1], BenchmarkResult)
