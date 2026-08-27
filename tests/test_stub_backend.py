"""Unit tests for the in-process stub backend."""

from __future__ import annotations

import numpy as np
import pytest

from vector_bench import StubBackend


def _normed(rows: list[list[float]]) -> np.ndarray:
    arr = np.array(rows, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr


class TestStubBackend:
    def test_ingest_query_round_trip(self) -> None:
        vecs = _normed([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        backend = StubBackend()
        backend.ingest(vecs, ["a", "b", "c"])
        hits = backend.query(np.array([1.0, 0.0], dtype=np.float32), k=2)
        ids = [h[0] for h in hits]
        assert ids[0] == "a"  # exact-match direction → highest similarity
        assert len(hits) == 2

    def test_query_empty_index_returns_empty_list(self) -> None:
        backend = StubBackend()
        assert backend.query(np.array([1.0, 0.0], dtype=np.float32), k=5) == []

    def test_ingest_mismatched_ids_raises(self) -> None:
        backend = StubBackend()
        vecs = _normed([[1.0, 0.0]])
        with pytest.raises(ValueError, match="ingest mismatch"):
            backend.ingest(vecs, ["a", "b"])

    def test_close_is_idempotent(self) -> None:
        backend = StubBackend()
        backend.ingest(_normed([[1.0, 0.0]]), ["a"])
        backend.close()
        backend.close()  # no raise

    def test_close_clears_the_index(self) -> None:
        """Split out of `test_close_is_idempotent_and_clears_index` (#133).

        That test proved "clears index" by querying after `close()` and
        expecting `[]` — which is the behaviour #133 is about. An empty result
        list from a closed backend is not a statement about the index; the
        harness scores it as `recall = 0.0` and publishes it. The claim is about
        the released state, so it is asserted against the state.
        """
        backend = StubBackend()
        backend.ingest(_normed([[1.0, 0.0]]), ["a"])
        backend.close()
        assert backend._vectors is None
        assert backend._ids == []

    def test_appends_on_repeated_ingest(self) -> None:
        backend = StubBackend()
        backend.ingest(_normed([[1.0, 0.0]]), ["a"])
        backend.ingest(_normed([[0.0, 1.0]]), ["b"])
        hits = backend.query(np.array([0.0, 1.0], dtype=np.float32), k=2)
        ids = [h[0] for h in hits]
        assert ids[0] == "b"
