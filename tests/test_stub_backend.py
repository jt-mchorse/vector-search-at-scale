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

    def test_close_is_idempotent_and_clears_index(self) -> None:
        backend = StubBackend()
        backend.ingest(_normed([[1.0, 0.0]]), ["a"])
        backend.close()
        backend.close()  # no raise
        assert backend.query(np.array([1.0, 0.0], dtype=np.float32), k=1) == []

    def test_appends_on_repeated_ingest(self) -> None:
        backend = StubBackend()
        backend.ingest(_normed([[1.0, 0.0]]), ["a"])
        backend.ingest(_normed([[0.0, 1.0]]), ["b"])
        hits = backend.query(np.array([0.0, 1.0], dtype=np.float32), k=2)
        ids = [h[0] for h in hits]
        assert ids[0] == "b"
