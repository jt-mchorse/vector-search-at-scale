"""vector-bench: reproducible benchmark harness for pgvector, Qdrant, Weaviate.

Public surface:

    from vector_bench import (
        Backend, Workload, BenchmarkResult, QueryHit,
        run_benchmark, generate_corpus, ground_truth_topk,
        StubBackend,
    )

The harness layer (`run_benchmark`) and the in-process `StubBackend` ship
in the base install. Real-engine adapters are gated behind extras
(`pgvector`, `qdrant`, `weaviate`) so the package loads in CI without
network access or the heavyweight client SDKs.
"""

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import (
    BenchmarkResult,
    LatencyStats,
    QueryHit,
    Workload,
    generate_corpus,
    ground_truth_topk,
    recall_at_k,
    run_benchmark,
)
from vector_bench.types import Backend, BackendError

__all__ = [
    "Backend",
    "BackendError",
    "BenchmarkResult",
    "LatencyStats",
    "QueryHit",
    "StubBackend",
    "Workload",
    "generate_corpus",
    "ground_truth_topk",
    "recall_at_k",
    "run_benchmark",
]

__version__ = "0.0.1"
