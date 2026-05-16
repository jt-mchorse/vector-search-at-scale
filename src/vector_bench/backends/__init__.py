"""Engine adapters. Each adapter lazy-imports its SDK so the package
loads in CI without any of the real engine clients installed.

Available adapters:

- `stub`: pure-numpy reference for hermetic testing.
- `hnsw-sim`: pure-numpy *simulation* of HNSW (M / ef_construction /
  ef_search knobs) used by the parameter-tuning study (#3). NOT a real
  HNSW implementation — see `hnsw_sim.py` for the model.
- `pgvector`: psycopg/pgvector adapter; install with `vector-bench[pgvector]`.
- `qdrant`: qdrant-client adapter; install with `vector-bench[qdrant]`.
- `weaviate`: weaviate-client adapter; install with `vector-bench[weaviate]`.

Use `make_backend(name, **kwargs)` to construct one without committing to
a hard import in your caller code.
"""

from __future__ import annotations


def make_backend(name: str, **kwargs):
    """Construct a backend by name. Errors with a clear hint when the extra is missing."""
    if name == "stub":
        from vector_bench.backends.stub import StubBackend

        return StubBackend(**kwargs)
    if name == "hnsw-sim":
        from vector_bench.backends.hnsw_sim import HnswSimBackend

        return HnswSimBackend(**kwargs)
    if name == "pgvector":
        from vector_bench.backends.pgvector import PgVectorBackend

        return PgVectorBackend(**kwargs)
    if name == "qdrant":
        from vector_bench.backends.qdrant import QdrantBackend

        return QdrantBackend(**kwargs)
    if name == "weaviate":
        from vector_bench.backends.weaviate import WeaviateBackend

        return WeaviateBackend(**kwargs)
    raise ValueError(f"unknown backend: {name!r}")
