"""Benchmark harness: deterministic corpus, ground-truth top-k, recall + latency.

The harness is intentionally small. It:

1. Generates a deterministic corpus + query set from a seed. `numpy`'s
   `default_rng(seed)` makes the vectors and ids reproducible across
   machines, so the same `(seed, n_vectors, n_queries, dim)` always
   yields the same workload — the harness needs this for cross-engine
   comparison to be meaningful.
2. Computes the ground truth top-k via a pure-numpy cosine score against
   every corpus vector. This is the slow path, used for the recall
   denominator; backends are scored against it.
3. Runs ingest + query against the backend, capturing wall-clock
   per-query latency and aggregating to p50 / p95 / p99 / max.
4. Writes one JSON file per `run_id` to a caller-supplied results dir.
   Re-running the same `run_id` refuses to overwrite unless `force=True`
   — idempotency by filesystem, not by replaying a prior JSON.

Concurrency is wired in as a parameter on `Workload` for future use by
issue #4 (latency under load); this PR runs queries serially so the
recall numbers are deterministic and the latency stats are clean baseline
single-threaded numbers. The `concurrency` parameter is validated and
recorded in the output JSON.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from vector_bench.types import Backend


@dataclass(frozen=True)
class Workload:
    """Deterministic workload definition.

    All fields are recorded on the output `BenchmarkResult` so two runs
    are comparable only if their workloads match. The harness checks
    schema-wise; the operator is responsible for not lying.
    """

    n_vectors: int
    dim: int
    n_queries: int
    top_k: int = 10
    seed: int = 1
    concurrency: int = 1

    def __post_init__(self) -> None:
        for name, value in [("n_vectors", self.n_vectors), ("dim", self.dim),
                            ("n_queries", self.n_queries), ("top_k", self.top_k),
                            ("concurrency", self.concurrency)]:
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        if self.top_k > self.n_vectors:
            raise ValueError(f"top_k ({self.top_k}) exceeds n_vectors ({self.n_vectors})")


@dataclass(frozen=True)
class QueryHit:
    query_idx: int
    hits: tuple[str, ...]  # backend's top-k ids in rank order


@dataclass(frozen=True)
class LatencyStats:
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True)
class BenchmarkResult:
    """Output of one `run_benchmark` call. Fully JSON-serializable."""

    run_id: str
    backend: str
    workload: Workload
    ingest_seconds: float
    ingest_rows_per_sec: float
    query_latency: LatencyStats
    mean_recall_at_k: float
    started_at: str
    git_sha: str | None
    cost_per_query_usd: float | None  # populated by issue #5
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def generate_corpus(workload: Workload) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """Generate corpus + queries deterministically from `workload.seed`.

    Returns `(corpus, queries, corpus_ids, query_ids)`.

    Both corpus and queries are drawn from a unit-Gaussian and L2-normalized
    so cosine similarity reduces to a dot product. The corpus is stored as
    `float32` to match what the real engines ingest; queries are kept in
    the same dtype for consistency.
    """
    rng = np.random.default_rng(workload.seed)
    corpus = rng.standard_normal((workload.n_vectors, workload.dim), dtype=np.float32)
    queries = rng.standard_normal((workload.n_queries, workload.dim), dtype=np.float32)
    corpus /= np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-12
    queries /= np.linalg.norm(queries, axis=1, keepdims=True) + 1e-12
    corpus_ids = [f"c{i:08d}" for i in range(workload.n_vectors)]
    query_ids = [f"q{i:06d}" for i in range(workload.n_queries)]
    return corpus, queries, corpus_ids, query_ids


def ground_truth_topk(
    corpus: np.ndarray, queries: np.ndarray, corpus_ids: list[str], k: int
) -> list[list[str]]:
    """Brute-force cosine top-k per query. The recall denominator.

    Returns a list-of-lists of corpus ids, one inner list per query, in
    descending similarity order.
    """
    # Both arrays are already L2-normalized — dot product is cosine similarity.
    sims = queries @ corpus.T  # shape: (n_queries, n_vectors)
    n_q = sims.shape[0]
    # argsort descending; `argpartition` would be faster for large N but
    # `argsort` keeps the code obvious and is plenty fast for benchmark
    # sizes the harness exercises locally (<=1M during real runs, much
    # smaller in tests).
    top = np.argsort(-sims, axis=1)[:, :k]
    out: list[list[str]] = []
    for i in range(n_q):
        out.append([corpus_ids[idx] for idx in top[i]])
    return out


def recall_at_k(predicted: list[str], truth: list[str], k: int) -> float:
    """Fraction of the top-k truth ids present anywhere in the top-k predicted."""
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    truth_set = set(truth[:k])
    pred_set = set(predicted[:k])
    if not truth_set:
        return 0.0
    return len(truth_set & pred_set) / len(truth_set)


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile. Avoids the numpy dependency on a hot path."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (pct / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _utc_now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str | None:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, check=False, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


@contextmanager
def _closing(backend: Backend):
    try:
        yield backend
    finally:
        backend.close()


def run_benchmark(
    backend: Backend,
    workload: Workload,
    *,
    run_id: str,
    results_dir: str | Path = "results",
    force: bool = False,
    write_json: bool = True,
) -> BenchmarkResult:
    """Run one benchmark: ingest, query, score, write JSON. Idempotent by `run_id`."""
    out_path = Path(results_dir) / f"{run_id}.json"
    if not force and out_path.exists():
        raise FileExistsError(
            f"results file already exists at {out_path}; pass force=True to overwrite"
        )

    corpus, queries, corpus_ids, _ = generate_corpus(workload)
    truth = ground_truth_topk(corpus, queries, corpus_ids, workload.top_k)

    started_at = _utc_now_iso()
    with _closing(backend):
        ingest_start = time.perf_counter()
        backend.ingest(corpus, corpus_ids)
        ingest_seconds = time.perf_counter() - ingest_start

        latencies_ms: list[float] = []
        recalls: list[float] = []
        for i in range(workload.n_queries):
            q_start = time.perf_counter()
            hits = backend.query(queries[i], workload.top_k)
            latencies_ms.append((time.perf_counter() - q_start) * 1000.0)
            predicted = [hit_id for hit_id, _score in hits]
            recalls.append(recall_at_k(predicted, truth[i], workload.top_k))

    latency_stats = LatencyStats(
        p50_ms=_percentile(latencies_ms, 50.0),
        p95_ms=_percentile(latencies_ms, 95.0),
        p99_ms=_percentile(latencies_ms, 99.0),
        max_ms=max(latencies_ms) if latencies_ms else 0.0,
    )

    result = BenchmarkResult(
        run_id=run_id,
        backend=backend.name,
        workload=workload,
        ingest_seconds=ingest_seconds,
        ingest_rows_per_sec=workload.n_vectors / ingest_seconds if ingest_seconds > 0 else float("inf"),
        query_latency=latency_stats,
        mean_recall_at_k=sum(recalls) / len(recalls) if recalls else 0.0,
        started_at=started_at,
        git_sha=_git_sha(),
        cost_per_query_usd=None,
    )

    if write_json:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        os.makedirs(out_path.parent, exist_ok=True)
        out_path.write_text(json.dumps(result.to_json(), indent=2, sort_keys=True), encoding="utf-8")

    return result
