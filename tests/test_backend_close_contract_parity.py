"""`close()` is the Protocol's one un-enumerated method (#133).

`Backend` has three methods. `ingest` was enumerated across all five adapters in
`#131`; `query` has been fixed three times (`#63/#64`, `#69/#70`, `#79`). `close`
never was — and its docstring carried a claim:

    def close(self) -> None:
        \"\"\"Release any held resources. Idempotent.\"\"\"

The idempotence half is true, on all five, and is pinned here rather than left
in prose. What the docstring never said is what the object *is* afterwards, and
the five implementations answered differently. Measured after a successful
3-vector ingest, on `main`::

    case                  stub        hnsw-sim    pgvector          qdrant/weaviate
    close() x1, x2, x3    ok          ok          ok                ok
    query()  after close  [] (len 0)  [] (len 0)  AttributeError    closed SDK client
    ingest() after close  ok          ok          AttributeError    closed SDK client

The empty list is the sharp one. The harness scores `hits` against ground truth,
so an empty result is not an error — it is `recall = 0.0`, written to
`results/<run_id>/*.json` and rendered into the comparison table. Exactly the
harm this repo has named four times; from `tests/test_qdrant_backend.py`, "a
contract violation that deflates recall with no diagnostic".

And `ingest` after `close` *succeeded* on those two, silently resurrecting a
backend whose resources were released — `close()` sets `self._vectors = None;
self._ids = []`, which is indistinguishable from a fresh instance, so nothing
downstream could tell a closed backend from an empty one.

`pgvector`'s raw `AttributeError: 'NoneType' object has no attribute 'cursor'`
was at least loud, but neither `ValueError` ("for caller mistakes") nor
`BackendError` (which exists so "the harness can decide whether to retry or
surface as a hard failure"), and it names nothing.

**Reachability, stated plainly.** Not reachable from the CLI: it builds one
backend per invocation and both `run_benchmark` and `run_under_load` close it in
a `finally`. The road is a library caller reusing an instance, which nothing said
not to do::

    b = HnswSimBackend()
    run_benchmark(b, workload=...)    # closes b in its finally
    run_under_load(b, workload=...)   # ingested and queried a closed backend

On `hnsw-sim` and `stub` that second study completed and published numbers.

Driven through every adapter with the `object.__new__` + fake-client pattern
`test_backend_ingest_shape_parity.py` established, so no SDK and no live engine
is needed.
"""

from __future__ import annotations

import numpy as np
import pytest

from vector_bench.types import BackendError

from .test_backend_ingest_shape_parity import _build

BACKENDS = ["stub", "hnsw-sim", "pgvector", "qdrant", "weaviate"]

VECTORS = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32)
IDS = ["a", "b", "c"]


def _ingested(name: str):
    backend, _count = _build(name)
    backend.ingest(VECTORS, IDS)
    return backend


# ----------------------------------------------------------------------
# The claim that was already true, now pinned
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", BACKENDS)
def test_close_is_idempotent(name: str) -> None:
    """The docstring said so; nothing checked it across the five.

    `_closing` calls `close()` from a `finally`, so a teardown method that
    raised on a second call would turn one failure into two.
    """
    backend = _ingested(name)
    backend.close()
    backend.close()
    backend.close()


@pytest.mark.parametrize("name", BACKENDS)
def test_close_is_idempotent_even_without_a_prior_ingest(name: str) -> None:
    backend, _count = _build(name)
    backend.close()
    backend.close()


# ----------------------------------------------------------------------
# The claim that was not true
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", BACKENDS)
def test_query_after_close_raises_rather_than_returning_a_result(name: str) -> None:
    """The row that publishes a number. `stub` and `hnsw-sim` returned `[]`."""
    backend = _ingested(name)
    backend.close()
    with pytest.raises(BackendError, match="backend is closed"):
        backend.query(VECTORS[0], 2)


@pytest.mark.parametrize("name", BACKENDS)
def test_ingest_after_close_raises_rather_than_resurrecting(name: str) -> None:
    backend = _ingested(name)
    backend.close()
    with pytest.raises(BackendError, match="backend is closed"):
        backend.ingest(VECTORS, IDS)


@pytest.mark.parametrize("name", BACKENDS)
def test_the_error_names_the_backend_and_points_at_the_issue(name: str) -> None:
    backend = _ingested(name)
    backend.close()
    with pytest.raises(BackendError) as exc:
        backend.query(VECTORS[0], 2)
    message = str(exc.value)
    assert "#133" in message
    assert "Construct a new backend" in message
    assert message.split(":")[0].endswith("Backend"), message


@pytest.mark.parametrize("name", BACKENDS)
def test_the_type_is_backenderror_not_valueerror(name: str) -> None:
    """`BackendError`'s own docstring picks it: it is "Distinct from `ValueError`
    (which is for caller mistakes)", and exists so the harness "can decide
    whether to retry or surface as a hard failure". A call on a closed backend is
    a lifecycle error, not a bad argument."""
    backend = _ingested(name)
    backend.close()
    with pytest.raises(BackendError) as exc:
        backend.query(VECTORS[0], 2)
    assert not isinstance(exc.value, ValueError), name
    assert isinstance(exc.value, RuntimeError), "BackendError is a RuntimeError"


def test_all_five_agree_on_every_row() -> None:
    """The differential: one grid, five adapters, verdicts compared.

    A rule living in only one implementation shows up here as a disagreement,
    and so would a sixth adapter that forgot the guard.
    """

    def verdict(name: str, action: str) -> str:
        backend = _ingested(name)
        backend.close()
        try:
            if action == "query":
                backend.query(VECTORS[0], 2)
            else:
                backend.ingest(VECTORS, IDS)
        except BackendError:
            return "BackendError"
        except Exception as e:  # noqa: BLE001 - the point is to record the type
            return type(e).__name__
        return "NO RAISE"

    for action in ("query", "ingest"):
        verdicts = {name: verdict(name, action) for name in BACKENDS}
        assert set(verdicts.values()) == {"BackendError"}, (action, verdicts)


# ----------------------------------------------------------------------
# Controls — the guard must not touch a normal run
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", BACKENDS)
def test_an_open_backend_is_unaffected(name: str) -> None:
    backend, count_indexed = _build(name)
    backend.ingest(VECTORS, IDS)
    assert count_indexed() == 3


@pytest.mark.parametrize("name", ["stub", "hnsw-sim"])
def test_query_before_close_still_returns_results(name: str) -> None:
    """The two in-process adapters are the ones whose post-close `query` used to
    return `[]`; assert the *pre*-close path is untouched."""
    backend = _ingested(name)
    hits = backend.query(VECTORS[0], 2)
    assert len(hits) == 2
    assert all(isinstance(h[0], str) for h in hits)


@pytest.mark.parametrize("name", BACKENDS)
def test_repeated_ingest_before_close_still_works(name: str) -> None:
    backend, count_indexed = _build(name)
    backend.ingest(VECTORS, IDS)
    backend.ingest(VECTORS, ["d", "e", "f"])
    assert count_indexed() >= 3


def test_an_empty_backend_is_not_a_closed_backend() -> None:
    """The distinction the old behaviour destroyed: `close()` on the in-process
    adapters left `_vectors = None; _ids = []`, indistinguishable from a fresh
    instance. A never-ingested backend still answers a query with `[]`; a closed
    one raises."""
    fresh, _ = _build("stub")
    assert fresh.query(VECTORS[0], 2) == []

    closed, _ = _build("stub")
    closed.close()
    with pytest.raises(BackendError):
        closed.query(VECTORS[0], 2)
