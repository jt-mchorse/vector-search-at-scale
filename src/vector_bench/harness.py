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
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from vector_bench.io_utils import atomic_write_text
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
        # Integer guards (#29). Pre-#29 the sign-only `<= 0` check accepted
        # NaN (NaN comparisons always false) and fractional floats (which
        # silently truncated via range(int(x)) in the load loop). bool
        # excluded explicitly since Python's bool subclasses int and the
        # operator's intent for a count field is never a boolean.
        for name, value in [
            ("n_vectors", self.n_vectors),
            ("dim", self.dim),
            ("n_queries", self.n_queries),
            ("top_k", self.top_k),
            ("concurrency", self.concurrency),
        ]:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an int, got {value!r}")
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        if self.top_k > self.n_vectors:
            raise ValueError(f"top_k ({self.top_k}) exceeds n_vectors ({self.n_vectors})")

    def to_dict(self) -> dict[str, Any]:
        # Explicit field-by-field construction (#39) instead of `asdict`
        # so a future internal-only field can't silently ship into the
        # output JSON. Downstream consumers (plot scripts, dashboard,
        # cross-run analysis) bind to this exact six-field contract.
        return {
            "n_vectors": self.n_vectors,
            "dim": self.dim,
            "n_queries": self.n_queries,
            "top_k": self.top_k,
            "seed": self.seed,
            "concurrency": self.concurrency,
        }


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

    def __post_init__(self) -> None:
        # Sibling of the BenchmarkResult guard (#55): LatencyStats is the nested
        # result dataclass that guard missed. Its fields flow through
        # `to_dict()` into `dump_benchmark_json`'s `json.dumps` (default
        # `allow_nan=True`), so a non-finite p50/p95/p99/max serializes as the
        # invalid-JSON token `NaN`/`Infinity` (rejected by jq/JS/Go) — a
        # fabricated latency in a benchmark whose whole point is honest measured
        # values (handoff §10). A negative latency is likewise nonsensical. The
        # normal `run_benchmark` path computes these from finite `perf_counter`
        # deltas; fail loud here so a hand-built or JSON-reconstructed instance
        # can't smuggle corruption into the dump. Same shape as `BenchmarkResult`
        # (#55), `Workload` (#29), the cost dataclasses (#53), and `LoadCell`.
        for name, value in (
            ("p50_ms", self.p50_ms),
            ("p95_ms", self.p95_ms),
            ("p99_ms", self.p99_ms),
            ("max_ms", self.max_ms),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        # Four-field contract (#39).
        return {
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
        }


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

    def __post_init__(self) -> None:
        # Finiteness/range guards (#55) — `BenchmarkResult` was the one result
        # dataclass without a `__post_init__`, while `Workload` (#29),
        # `InstancePrice`/`EbsGp3Price` (#53), and `cost_per_query` (#51) all
        # guard their numerics. A non-finite field reaches `dump_benchmark_json`'s
        # `json.dumps` (default allow_nan=True), which emits the bare token
        # `Infinity`/`NaN` — invalid JSON that strict parsers (jq, JS, Go) reject,
        # and a fabricated number in a benchmark whose whole point is honest
        # measured values (handoff §10). Sibling of rag-production-kit #80. Fail
        # loud at construction so corrupt data from any path can't reach the dump.
        for name, value in [
            ("ingest_seconds", self.ingest_seconds),
            ("ingest_rows_per_sec", self.ingest_rows_per_sec),
        ]:
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")
        if not math.isfinite(self.mean_recall_at_k) or not (0.0 <= self.mean_recall_at_k <= 1.0):
            raise ValueError(
                f"mean_recall_at_k must be a finite number in [0, 1], got {self.mean_recall_at_k!r}"
            )
        if self.cost_per_query_usd is not None and (
            not math.isfinite(self.cost_per_query_usd) or self.cost_per_query_usd < 0
        ):
            raise ValueError(
                f"cost_per_query_usd must be a finite number >= 0 when set, "
                f"got {self.cost_per_query_usd!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        # Eleven-field contract (#39). Nests `workload.to_dict()` and
        # `query_latency.to_dict()` so the nested shape is also pinned
        # by the nested classes' own contracts. `extra` is shallow-copied
        # so callers can't mutate the frozen dataclass through the dict.
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "workload": self.workload.to_dict(),
            "ingest_seconds": self.ingest_seconds,
            "ingest_rows_per_sec": self.ingest_rows_per_sec,
            "query_latency": self.query_latency.to_dict(),
            "mean_recall_at_k": self.mean_recall_at_k,
            "started_at": self.started_at,
            "git_sha": self.git_sha,
            "cost_per_query_usd": self.cost_per_query_usd,
            "extra": dict(self.extra),
        }

    def to_json(self) -> dict[str, Any]:
        # Back-compat alias; callers (cli.py, run_benchmark, downstream
        # scripts) continue to call `.to_json()`. The contract is owned
        # by `to_dict()` (#39).
        return self.to_dict()


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
    # Integer guard (#29) — NaN passed sign-only and silently miscounted via
    # set-slicing; fractional k truncated via list slicing.
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError(f"k must be a positive integer, got {k!r}")
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
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
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


def dump_benchmark_json(
    path: str | Path,
    *,
    result: BenchmarkResult,
    force: bool = False,
) -> Path:
    """Atomically write `result` to `path` as JSON (#39).

    Pulls the inline `force`-check + `json.dumps` + `atomic_write_text`
    triple out of `run_benchmark` so callers that build a
    `BenchmarkResult` outside the runner (cross-run analysis, test
    fixtures, custom drivers) can materialize one to disk through the
    same idempotency contract.

    Refuses to overwrite an existing file unless ``force=True`` —
    matches D-007 (one JSON per run_id under results/, no silent clobber).
    Routes through `vector_bench.io_utils.atomic_write_text` per D-012
    so the file is either fully written or not present, never a half-
    written half-result.

    Returns the resolved output path.
    """
    out_path = Path(path)
    if not force and out_path.exists():
        raise FileExistsError(
            f"results file already exists at {out_path}; pass force=True to overwrite"
        )
    atomic_write_text(out_path, json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return out_path


def run_benchmark(
    backend: Backend,
    workload: Workload,
    *,
    run_id: str,
    results_dir: str | Path = "results",
    force: bool = False,
    write_json: bool = True,
) -> BenchmarkResult:
    """Run one benchmark: ingest, query, score, write JSON. Idempotent by `run_id`.

    `workload.concurrency` must be 1 (D-011). `run_benchmark` is the
    single-shot serial entry point; concurrency studies live in
    `vector_bench.load.run_under_load`, which sweeps a list of
    concurrency levels and records per-cell latency. Allowing
    `workload.concurrency > 1` here would silently produce a results
    JSON whose recorded concurrency disagrees with the actual
    execution mode — a latency stat that lies about its concurrency
    is exactly the credibility leak this repo's premise can't afford.
    """
    if workload.concurrency != 1:
        raise ValueError(
            f"run_benchmark requires workload.concurrency == 1; got {workload.concurrency}. "
            "Concurrency studies go through vector_bench.load.run_under_load "
            "(or `vector-bench load` on the CLI), which sweeps a list of "
            "concurrency levels and records per-cell latency. "
            "(D-011 — see MEMORY/core_decisions_human.md.)"
        )
    out_path = Path(results_dir) / f"{run_id}.json"
    if write_json and not force and out_path.exists():
        # Pre-flight the force-check before running the workload so a
        # misconfigured `run_id` collision fails fast (otherwise the
        # operator would pay the wall-clock of the workload only to
        # discover the destination is locked).
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
        # A non-positive ingest time is a degenerate measurement (a clock that
        # didn't advance, or a no-op backend): there's no meaningful rows/sec to
        # report. The previous `else float("inf")` fabricated an infinite
        # throughput that serialized as the invalid-JSON token `Infinity` (#55).
        # Fail loud at the measurement site instead, where the cause is locatable.
        if ingest_seconds <= 0:
            raise ValueError(
                f"ingest finished in a non-positive {ingest_seconds:.6g}s; a degenerate "
                "ingest time can't yield a meaningful ingest_rows_per_sec — check the "
                "backend's ingest timing (a no-op or sub-resolution clock)"
            )

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
        # ingest_seconds is guaranteed > 0 by the guard above, so this is finite.
        ingest_rows_per_sec=workload.n_vectors / ingest_seconds,
        query_latency=latency_stats,
        mean_recall_at_k=sum(recalls) / len(recalls) if recalls else 0.0,
        started_at=started_at,
        git_sha=_git_sha(),
        cost_per_query_usd=None,
    )

    if write_json:
        # Idempotency contract belongs to `dump_benchmark_json` (#39).
        # `force=True` is safe here because we already pre-flighted the
        # `force=False` collision above; the second check inside the
        # dump wrapper would never re-fire on a successful run.
        dump_benchmark_json(out_path, result=result, force=True)

    return result
