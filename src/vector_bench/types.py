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


def check_ingest_shape(vectors: np.ndarray, ids: Sequence[str]) -> None:
    """Raise unless `ids` pairs one-to-one with `vectors`' rows.

    One definition, called by every adapter. It lived inline in `StubBackend`
    and `HnswSimBackend` and was absent from the three real-engine adapters, so
    two of five backends honoured the contract and three did not (#131).
    Measured across all five, with the fake-client pattern
    `tests/test_qdrant_backend.py` established:

        case                       stub        hnsw-sim    pgvector    qdrant      weaviate
        3 vectors, 3 ids (control) indexed 3   indexed 3   indexed 3   indexed 3   indexed 3
        4 ids for 3 vectors        ValueError  ValueError  indexed 3   indexed 3   indexed 3
        2 ids for 3 vectors        ValueError  ValueError  IndexError  IndexError  IndexError
        0 ids for 3 vectors        ValueError  ValueError  IndexError  IndexError  IndexError

    The surplus row is the one that matters. All three real engines return
    **normally** having indexed three rows, and the harness then scores recall
    over the four ids it believes it ingested — the fourth retrievable by no
    query, because it was never stored. Recall is silently deflated and the
    published benchmark number is wrong. That is the harm `#63/#64`, `#69/#70`
    and `#79` fixed three times on `query`, whose test docstring names it: "a
    contract violation that deflates recall with no diagnostic". All three were
    on `query`; `ingest` was never enumerated.

    `ValueError`, not `BackendError`, per that type's own docstring: it is
    "Distinct from `ValueError` (which is for caller mistakes)". A length
    mismatch is a caller mistake, detected before any engine is touched — and
    keeping `ValueError` leaves the two backends that already worked byte-for-
    byte unchanged, message included.
    """
    if len(ids) != vectors.shape[0]:
        raise ValueError(f"ingest mismatch: {vectors.shape[0]} vectors but {len(ids)} ids")


def check_open(closed: bool, *, backend: str, method: str) -> None:
    """Raise unless the backend is still open (#133).

    One definition, called at the top of every adapter's `ingest` and `query`,
    so a sixth adapter gets the contract by construction -- the same shape
    `check_ingest_shape` above established for `#131`.

    `close()` documents itself as "Release any held resources. Idempotent." That
    is true, and stays true. What it never said is what the object *is*
    afterwards, and the five implementations answered differently. Measured
    after a successful 3-vector ingest::

        case                  stub        hnsw-sim    pgvector          qdrant/weaviate
        close() x1, x2, x3    ok          ok          ok                ok
        query()  after close  [] (len 0)  [] (len 0)  AttributeError    closed SDK client
        ingest() after close  ok          ok          AttributeError    closed SDK client

    The empty list is the sharp one. The harness scores `hits` against ground
    truth, so an empty result is not an error -- it is `recall = 0.0`, written to
    `results/<run_id>/*.json` and rendered into the comparison table. Exactly the
    harm this repo has named four times; from `tests/test_qdrant_backend.py`,
    "a contract violation that deflates recall with no diagnostic".

    And `ingest` after `close` *succeeded* on those two, silently resurrecting a
    backend whose resources were released -- because `close()` sets
    `self._vectors = None; self._ids = []`, which is indistinguishable from a
    fresh instance, so nothing downstream could tell a closed backend from an
    empty one.

    `pgvector`'s raw `AttributeError: 'NoneType' object has no attribute
    'cursor'` was at least loud, but the wrong type and it names nothing.
    `BackendError` is chosen by its own docstring: it is "Distinct from
    `ValueError` (which is for caller mistakes)", and it exists so "the harness
    can decide whether to retry or surface as a hard failure". A call on a closed
    backend is a lifecycle error, not a caller's bad argument.

    Not reachable from the CLI -- it builds one backend per invocation and both
    `run_benchmark` and `run_under_load` close it in a `finally`. The road is a
    library caller reusing an instance, which nothing said not to do::

        b = HnswSimBackend()
        run_benchmark(b, workload=...)    # closes b in its finally
        run_under_load(b, workload=...)   # ingested and queried a closed backend

    On `hnsw-sim` and `stub` that second study completed and published numbers.
    """
    if closed:
        raise BackendError(
            f"{backend}: backend is closed; `close()` released its resources and the "
            f"instance cannot be reused. Construct a new backend for another run "
            f"(#133)."
        )


@runtime_checkable
class Backend(Protocol):
    """Single-method-ingest / single-method-query seam over vector engines."""

    name: str

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        """Insert `vectors` (shape: [n, dim]) under `ids` into the backend.

        `ids` must pair one-to-one with `vectors`' rows; implementations call
        `check_ingest_shape` first and raise `ValueError` otherwise (#131).
        """

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Return the top-`k` (id, similarity) pairs for `vector`.

        Implementations must return similarity (higher is better), not
        distance. Conversion lives in the adapter so the harness deals in
        one direction only.
        """

    def close(self) -> None:
        """Release any held resources. Idempotent.

        **After `close()` the instance is spent** (#133). `ingest` and `query`
        raise `BackendError` via `check_open`; they do not return a result and
        they do not silently re-open. Before that contract, `StubBackend` and
        `HnswSimBackend` answered a post-close `query` with an empty list --
        which the harness scores as `recall = 0.0` and publishes -- while
        `pgvector` raised a raw `AttributeError`. Construct a new backend for
        another run. `close()` itself stays idempotent, because `_closing`
        calls it from a `finally`.
        """
