"""Backend protocol and shared error type.

Mirrors the portfolio's single-method-protocol pattern already used in
`eval-harness` (Backend), `rag-production-kit` (Embedder/Reranker/Generator),
and `embedding-model-shootout` (Embedder): one Protocol, two methods, lazy
SDK imports per implementation so the package loads in CI without any of
the engine clients installed.

Backends are stateful (they hold a connection or client handle), so they
expose `close()` for explicit teardown. The harness uses them inside a
context manager helper (`closing(backend)`) to guarantee cleanup even on
exceptions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np


class BackendError(RuntimeError):
    """Raised by backend adapters for setup/connection/SDK failures.

    Distinct from `ValueError` (which is for caller mistakes) so the
    harness can decide whether to retry or surface as a hard failure.
    """


@runtime_checkable
class Backend(Protocol):
    """Single-method-ingest / single-method-query seam over vector engines."""

    name: str

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        """Insert `vectors` (shape: [n, dim]) under `ids` into the backend."""

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Return the top-`k` (id, similarity) pairs for `vector`.

        Implementations must return similarity (higher is better), not
        distance. Conversion lives in the adapter so the harness deals in
        one direction only.
        """

    def close(self) -> None:
        """Release any held resources. Idempotent."""
