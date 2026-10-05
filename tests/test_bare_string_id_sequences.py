"""`recall_at_k` and `ground_truth_topk` refuse a bare string of ids (#167).

A `str` slices and iterates like a list of one-character ids. Measured on
`main`:

    recall_at_k("abc", "cba", 3)                -> 1.0   (two different ids)
    recall_at_k(["c1"], "c1", 5)                -> 0.0   (a correct prediction)
    recall_at_k("c00000001", ["c00000001"], 5)  -> 0.0

`recall_at_k` is the README's headline metric and both functions are exported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from vector_bench import Workload, generate_corpus, ground_truth_topk, recall_at_k, run_benchmark
from vector_bench.backends.stub import StubBackend

_SPLIT = "would be compared one character at a time"


@pytest.mark.parametrize(
    ("predicted", "truth", "k", "name"),
    [
        ("abc", "cba", 3, "predicted"),
        (["c1"], "c1", 5, "truth"),
        ("c00000001", ["c00000001"], 5, "predicted"),
        (b"ab", ["a"], 2, "predicted"),
        (["a"], bytearray(b"a"), 2, "truth"),
        ("", ["a"], 1, "predicted"),
    ],
)
def test_recall_at_k_refuses_a_bare_string(predicted: Any, truth: Any, k: int, name: str) -> None:
    with pytest.raises(ValueError, match=_SPLIT) as exc:
        recall_at_k(predicted, truth, k)
    assert str(exc.value).startswith(f"{name} must be a sequence of ids")


def test_recall_at_k_message_shows_the_working_spelling() -> None:
    with pytest.raises(ValueError, match=r"pass \['c1'\]"):
        recall_at_k(["c1"], "c1", 5)


@pytest.mark.parametrize(
    ("predicted", "truth", "expected"),
    [
        (["c1"], ["c1"], 1.0),
        (("a", "b"), ("b", "c"), 0.5),
        (["a", "b", "c"], ("x", "y", "z"), 0.0),
    ],
)
def test_recall_at_k_sequences_are_unchanged(predicted: Any, truth: Any, expected: float) -> None:
    assert recall_at_k(predicted, truth, 3) == expected


def test_ground_truth_topk_refuses_a_bare_string_of_corpus_ids() -> None:
    # Long enough that every `corpus_ids[idx]` lands: on `main` this returned
    # single characters as ids with no error.
    w = Workload(n_vectors=8, dim=4, n_queries=2, top_k=3)
    corpus, queries, corpus_ids, _ = generate_corpus(w)
    with pytest.raises(ValueError, match=_SPLIT) as exc:
        ground_truth_topk(corpus, queries, "abcdefgh", 3)
    assert str(exc.value).startswith("corpus_ids must be a sequence of ids")
    # And the real ids still work, one inner list per query.
    got = ground_truth_topk(corpus, queries, corpus_ids, 3)
    assert len(got) == 2
    assert all(len(row) == 3 and set(row) <= set(corpus_ids) for row in got)
    assert isinstance(corpus, np.ndarray)


def test_run_benchmark_is_unchanged_through_the_harness(tmp_path: Path) -> None:
    # The orchestrator always passes lists; the stub is the ground-truth oracle,
    # so its recall is exactly 1.0 before and after.
    w = Workload(n_vectors=64, dim=8, n_queries=5, top_k=5)
    result = run_benchmark(StubBackend(), w, run_id="bare-167", results_dir=tmp_path)
    assert result.mean_recall_at_k == 1.0
