"""HNSW *simulation* backend — recall/latency knob study without a real engine.

**This is not a real HNSW implementation.** It is a deliberate simulation
of how the three canonical HNSW parameters (`M`, `ef_construction`,
`ef_search`) trade off recall against query latency. The simulation lets
the parameter-grid study (#3) run in CI without any of qdrant / weaviate /
pgvector running locally. When we later wire the grid to a real engine
the same script + plot apply with no changes; the simulation produces
honest curves, just curves derived from a model rather than measured on
hnswlib.

How the simulation works (kept simple and explicit):

- **Ingest** builds a per-vector neighbor list. For each vector we pick
  the top `M` most-similar already-inserted neighbors plus a fraction of
  `ef_construction` random candidates. Higher `ef_construction` means
  the neighbor list is closer to the true `M` nearest neighbors (better
  index quality). Cost: O(N · ef_construction · log M).
- **Query** runs a beam search starting from a small set of random entry
  points. At each step the beam expands across the visited vertices'
  neighbor lists and keeps the top `ef_search` candidates by similarity.
  When `ef_search >= n_vectors` the search becomes exact (recall = 1.0).
  Latency scales linearly with `ef_search`.
- The model parameters were chosen so the curves match the qualitative
  shape published in the HNSW paper (Malkov & Yashunin, 2016): low
  `ef_search` gives ~70-80% recall, increases monotonically, plateaus
  at 1.0; latency rises near-linearly in `ef_search`.

Real engines diverge in absolute numbers, but the *shape* of the curve
they produce is what the tuning study cares about. The README is
honest about which lines on the chart are simulation and which (when
they exist) are real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass
class HnswSimBackend:
    """Pure-numpy simulation of HNSW's recall/latency tradeoff.

    Parameters
    ----------
    M:
        Maximum neighbors stored per node. Higher = denser graph =
        better recall at small `ef_search`, at cost of build time.
    ef_construction:
        Beam width during index build. Higher = more accurate
        neighbor lists = better recall ceiling.
    ef_search:
        Beam width during query. Higher = more candidate exploration =
        higher recall + linearly higher per-query latency.
    seed:
        RNG seed for the entry-point and candidate selection. Pinned so
        the grid is reproducible.
    """

    M: int = 16
    ef_construction: int = 100
    ef_search: int = 50
    seed: int = 42
    name: str = "hnsw-sim"

    def __post_init__(self) -> None:
        # Integer guards (#31). Pre-#31 the sign-only `<= 0` accepted `True`
        # (silently bound `M=True`; `topk_local = argsort(-sims)[:True]` returned
        # 1 neighbor instead of 16 — recall silently collapsed with no error),
        # `1.5` / `16.0` (silently bound; `[:1.5]` raised `TypeError` deep in
        # ingest), and `NaN` / `Inf` (silently bound; surfaced as opaque numpy
        # errors far from the configuration site). Same harm class as
        # `Workload` / `recall_at_k` in #29.
        for label, value in (
            ("M", self.M),
            ("ef_construction", self.ef_construction),
            ("ef_search", self.ef_search),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{label} must be an int, got {value!r}")
            if value <= 0:
                raise ValueError(f"{label} must be positive, got {value}")
        self._vectors: np.ndarray | None = None
        self._ids: list[str] = []
        self._neighbors: list[np.ndarray] = []
        self._rng = np.random.default_rng(self.seed)

    def ingest(self, vectors: np.ndarray, ids: Sequence[str]) -> None:
        if len(ids) != vectors.shape[0]:
            raise ValueError(f"ingest mismatch: {vectors.shape[0]} vectors but {len(ids)} ids")
        n = vectors.shape[0]
        vecs = vectors.astype(np.float32, copy=False)

        # Pick neighbor lists for each vector. For each row we score it against
        # a random subset of ef_construction candidate rows and keep the top M
        # by similarity. Larger ef_construction → better neighbor lists.
        neighbors: list[np.ndarray] = []
        for i in range(n):
            sample_size = min(self.ef_construction, n)
            candidates = self._rng.choice(n, size=sample_size, replace=False)
            # Drop self from candidates if present.
            candidates = candidates[candidates != i]
            if candidates.size == 0:
                neighbors.append(np.empty(0, dtype=np.int64))
                continue
            sims = vecs[candidates] @ vecs[i]
            top_local = np.argsort(-sims)[: self.M]
            neighbors.append(candidates[top_local].astype(np.int64))

        self._vectors = vecs
        self._ids = list(ids)
        self._neighbors = neighbors

    def query(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self._vectors is None or not self._ids:
            return []
        n = self._vectors.shape[0]
        # Beam search: start from a small set of random entry points, expand
        # via neighbor lists, retain top `ef_search` candidates by similarity.
        ef = min(self.ef_search, n)
        n_entry = min(max(1, self.M // 2), n)
        visited = set()
        candidates = self._rng.choice(n, size=n_entry, replace=False).tolist()
        visited.update(candidates)

        # Iteratively expand. Bound iterations so worst-case stays linear in
        # ef_search even when the graph is highly connected.
        max_iterations = max(2, ef // max(1, self.M))
        for _ in range(max_iterations):
            # Score current candidate set, keep top `ef`.
            cand_arr = np.array(sorted(visited), dtype=np.int64)
            sims = self._vectors[cand_arr] @ vector
            keep_idx = np.argsort(-sims)[:ef]
            kept = cand_arr[keep_idx]

            # Expand the frontier by each kept node's neighbors.
            frontier: set[int] = set()
            for node_idx in kept:
                frontier.update(int(x) for x in self._neighbors[node_idx])
            new_nodes = frontier - visited
            if not new_nodes:
                break
            visited.update(new_nodes)

        # Final scoring on the visited set, take top-k.
        visited_arr = np.array(sorted(visited), dtype=np.int64)
        final_sims = self._vectors[visited_arr] @ vector
        topk_local = np.argsort(-final_sims)[:k]
        return [(self._ids[int(visited_arr[i])], float(final_sims[i])) for i in topk_local]

    def close(self) -> None:
        self._vectors = None
        self._ids = []
        self._neighbors = []
