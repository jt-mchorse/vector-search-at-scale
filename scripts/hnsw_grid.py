#!/usr/bin/env python3
"""HNSW parameter-tuning grid study (#3).

Grids over (M, ef_construction, ef_search) on `HnswSimBackend` and writes
one JSON per grid cell plus an aggregated `grid.json` with
recall + p50/p95/p99 latency per parameter triple. Frontier rendering
lives in `scripts/plot_hnsw_frontier.py`.

The `HnswSimBackend` is a pure-numpy *simulation* of HNSW's tradeoff —
the recall and latency numbers are real for the simulation but do not
claim to match an actual hnswlib / qdrant / weaviate / pgvector run.
The grid script is reusable on any backend with the same knobs: pass
`--backend qdrant` when the real bring-up is done.

Usage:
    python scripts/hnsw_grid.py \\
        --n-vectors 2000 --n-queries 200 --dim 64 \\
        --M 8,16,32 --ef-construction 50,100,200 --ef-search 16,32,64,128 \\
        --out-dir results/hnsw-grid
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vector_bench.backends import make_backend  # noqa: E402
from vector_bench.backends.hnsw_sim import (  # noqa: E402
    validate_hnsw_params as validate_hnsw_sim,
)
from vector_bench.backends.pgvector import (  # noqa: E402
    validate_hnsw_params as validate_pgvector_hnsw,
)
from vector_bench.backends.qdrant import (  # noqa: E402
    validate_corpus as validate_qdrant_corpus,
)
from vector_bench.backends.qdrant import (  # noqa: E402
    validate_hnsw_params as validate_qdrant_hnsw,
)
from vector_bench.backends.weaviate import (  # noqa: E402
    validate_hnsw_params as validate_weaviate_hnsw,
)
from vector_bench.harness import Workload, run_benchmark  # noqa: E402
from vector_bench.io_utils import atomic_write_text  # noqa: E402
from vector_bench.types import BackendError  # noqa: E402

#: Each backend's own argument names for the grid's three HNSW knobs, and
#: whether it takes `seed` (#160). `run_grid` passed `HnswSimBackend`'s names to
#: every backend, so the documented `--backend qdrant` (or pgvector / weaviate)
#: raised `TypeError: ... unexpected keyword argument 'M'` before connecting to
#: anything. `tests/test_hnsw_grid_backend_knobs.py` binds each row against the
#: adapter's real `__init__` signature.
_HNSW_KNOBS: dict[str, tuple[str, str, str, bool]] = {
    "hnsw-sim": ("M", "ef_construction", "ef_search", True),
    "qdrant": ("hnsw_m", "hnsw_ef_construct", "hnsw_ef", False),
    "pgvector": ("hnsw_m", "hnsw_ef_construction", "hnsw_ef_search", False),
    "weaviate": ("hnsw_max_connections", "hnsw_ef_construction", "hnsw_ef", False),
}

#: Each backend's own HNSW bounds, as a check over one grid cell's (M, efc, efs)
#: that raises `ValueError` (#188 pgvector, #222 qdrant, #227 hnsw-sim, #228
#: weaviate). Run over every cell before the first one runs. Pure functions, so
#: no SDK is imported. Every `_HNSW_KNOBS` backend needs a row: a constructor
#: check alone runs once per cell, after the earlier cells were written.
_HNSW_BOUNDS: dict[str, Callable[[int, int, int], None]] = {
    "pgvector": lambda M, efc, efs: validate_pgvector_hnsw(m=M, ef_construction=efc, ef_search=efs),
    "qdrant": lambda M, efc, efs: validate_qdrant_hnsw(m=M, ef_construct=efc, ef=efs),
    "weaviate": lambda M, efc, efs: validate_weaviate_hnsw(
        max_connections=M, ef_construction=efc, ef=efs
    ),
    # The default backend (#227). Its constructor refused M=0, but per cell, so
    # `--M 8,0` wrote M8's cell first -- over an existing grid's, in a reused
    # --out-dir -- and then exited 2 with no grid.json.
    "hnsw-sim": lambda M, efc, efs: validate_hnsw_sim(M=M, ef_construction=efc, ef_search=efs),
}

#: A backend's floor on the corpus, as a check over (n_vectors, dim) that raises
#: `ValueError`. Qdrant answers every query by brute-force scan under
#: `MIN_CORPUS_KB` (#209, #220); its `ingest` refused that inside cell 1, after
#: `--out-dir` was created, as a traceback at exit 1 (#245). Decidable from the
#: flags alone, so it runs with the `_HNSW_BOUNDS` pre-flight.
_CORPUS_BOUNDS: dict[str, Callable[[int, int], None]] = {
    "qdrant": lambda n, dim: validate_qdrant_corpus(n_vectors=n, dim=dim),
}

#: Backends `make_backend` knows that have no HNSW knobs to sweep.
_NO_HNSW_KNOBS = frozenset({"stub"})

#: The exact name `run_grid` gives a cell's JSON (`M{M}_efc{efc}_efs{efs}.json`).
#: Only files of this shape are removed as stale after a rerun (#252).
_CELL_NAME = re.compile(r"M[0-9]+_efc[0-9]+_efs[0-9]+\.json")


def _backend_kwargs(backend_name: str, *, M: int, efc: int, efs: int, seed: int) -> dict:
    """The grid cell's knobs, spelled the way `backend_name` takes them (#160)."""
    if backend_name in _NO_HNSW_KNOBS:
        raise ValueError(
            f"backend {backend_name!r} has no HNSW parameters to grid over; "
            f"choose one of {sorted(_HNSW_KNOBS)}"
        )
    if backend_name not in _HNSW_KNOBS:
        raise ValueError(f"unknown backend: {backend_name!r}")
    m_name, efc_name, efs_name, takes_seed = _HNSW_KNOBS[backend_name]
    kwargs: dict = {m_name: M, efc_name: efc, efs_name: efs}
    if takes_seed:
        kwargs["seed"] = seed
    return kwargs


def _parse_int_list(s: str) -> list[int]:
    return [int(p.strip()) for p in s.split(",") if p.strip()]


def run_grid(
    *,
    n_vectors: int,
    n_queries: int,
    dim: int,
    top_k: int,
    seed: int,
    M_values: list[int],
    ef_construction_values: list[int],
    ef_search_values: list[int],
    out_dir: Path,
    backend_name: str = "hnsw-sim",
) -> dict:
    """Run the grid, returning the aggregated `grid.json` payload.

    Side effect: writes one BenchmarkResult JSON per cell under `out_dir/`
    plus `grid.json` summarizing the full grid.
    """
    # Distinct values per axis, before anything runs or is written (#169). A
    # repeated value gives two cells one `run_id`, so the second run overwrote
    # the first's `<run_id>.json` while `grid.json` listed both -- the frontier
    # then kept whichever was faster. `run_under_load` refuses the same collision
    # for its `c<NNN>.json` cells (load.py); this is that rule for the grid.
    for axis, values in (
        ("M", M_values),
        ("ef_construction", ef_construction_values),
        ("ef_search", ef_search_values),
    ):
        dups = sorted({v for v in values if values.count(v) > 1})
        if dups:
            raise ValueError(
                f"{axis} values must be distinct; got {list(values)} (repeated: {dups}); "
                "duplicate values collide on the per-cell <run_id>.json filename"
            )
    # Every cell's knobs against the backend's own bounds, before any cell runs
    # or is written (#188). pgvector refuses ef_construction < 2 * M when it
    # builds the index, so the README's axes died at cell 7 of 36 (M=32,
    # efc=50) with six cell JSONs on disk and no grid.json. Qdrant refuses
    # ef_construct < 4 the same way, at create_collection (#222).
    check = _HNSW_BOUNDS.get(backend_name)
    if check is not None:
        bad = []
        for M, efc, efs in itertools.product(M_values, ef_construction_values, ef_search_values):
            try:
                check(M, efc, efs)
            except ValueError as exc:
                bad.append(f"M={M} efc={efc} efs={efs}: {exc}")
        if bad:
            raise ValueError(
                f"{len(bad)} grid cell(s) are outside {backend_name}'s HNSW bounds, so nothing "
                "was run: " + "; ".join(bad[:3]) + ("; ..." if len(bad) > 3 else "")
            )
    workload = Workload(n_vectors=n_vectors, dim=dim, n_queries=n_queries, top_k=top_k, seed=seed)
    # After `Workload` (a non-positive size gets its own message), before the
    # first `make_backend` / `mkdir` (#245).
    corpus_check = _CORPUS_BOUNDS.get(backend_name)
    if corpus_check is not None:
        corpus_check(n_vectors, dim)

    cells: list[dict] = []
    written: set[str] = set()
    for M, efc, efs in itertools.product(M_values, ef_construction_values, ef_search_values):
        run_id = f"M{M}_efc{efc}_efs{efs}"
        kwargs = _backend_kwargs(backend_name, M=M, efc=efc, efs=efs, seed=seed)
        try:
            backend = make_backend(backend_name, **kwargs)
        except BackendError as exc:
            # A backend's constructor raises `BackendError` (a `RuntimeError`)
            # for setup problems that are operator input: a missing extra, or
            # an unset QDRANT_URL / WEAVIATE_HOST / PGVECTOR_DSN. `main` caught
            # only ValueError and OSError, so `--backend qdrant` on the base
            # install was a traceback at exit 1 (#197); `vector-bench run` and
            # `load` got the same exit 2 in #196. Only construction is
            # translated here: a `BackendError` raised while a cell runs is a
            # run failure, which `main` reports as `error:` at exit 2, as
            # run/load do (#209, #252).
            raise ValueError(str(exc)) from exc
        # Created after the backend is built, not before (#197): a refused
        # setup used to leave an empty `--out-dir` behind.
        out_dir.mkdir(parents=True, exist_ok=True)
        result = run_benchmark(backend, workload, run_id=run_id, results_dir=out_dir, force=True)
        written.add(f"{run_id}.json")
        cells.append(
            {
                "run_id": run_id,
                "M": M,
                "ef_construction": efc,
                "ef_search": efs,
                "mean_recall_at_k": result.mean_recall_at_k,
                "p50_ms": result.query_latency.p50_ms,
                "p95_ms": result.query_latency.p95_ms,
                "p99_ms": result.query_latency.p99_ms,
                "ingest_seconds": result.ingest_seconds,
            }
        )
        sys.stdout.write(
            f"M={M:3d} efc={efc:4d} efs={efs:4d}  recall@{top_k}={result.mean_recall_at_k:.3f}  "
            f"p50={result.query_latency.p50_ms:.2f}ms  p95={result.query_latency.p95_ms:.2f}ms\n"
        )

    grid_payload = {
        "backend": backend_name,
        "workload": {
            "n_vectors": n_vectors,
            "n_queries": n_queries,
            "dim": dim,
            "top_k": top_k,
            "seed": seed,
        },
        "axes": {
            "M": M_values,
            "ef_construction": ef_construction_values,
            "ef_search": ef_search_values,
        },
        "cells": cells,
    }
    # A rerun into the same `--out-dir` replaces the grid, so the previous grid's
    # cells go too (#252). They stayed: `--M 8,16` then `--M 8` left
    # `M16_*.json` beside a `grid.json` that no longer listed it -- the grid's
    # version of `load --force`'s stale `c<NNN>.json` (#182, #230). Only the
    # exact cell-name shape is touched; `grid.json` is written last, as before.
    for stale in out_dir.glob("M*_efc*_efs*.json"):
        if _CELL_NAME.fullmatch(stale.name) and stale.name not in written:
            stale.unlink()
    grid_path = out_dir / "grid.json"
    atomic_write_text(grid_path, json.dumps(grid_payload, indent=2, sort_keys=True))
    sys.stdout.write(f"\nwrote {grid_path}\n")
    return grid_payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n-vectors", type=int, default=2000)
    p.add_argument("--n-queries", type=int, default=200)
    p.add_argument("--dim", type=int, default=64)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--M", type=_parse_int_list, default=[8, 16, 32])
    p.add_argument("--ef-construction", type=_parse_int_list, default=[50, 100, 200])
    p.add_argument("--ef-search", type=_parse_int_list, default=[16, 32, 64, 128])
    p.add_argument("--out-dir", type=Path, default=Path("results/hnsw-grid"))
    p.add_argument("--backend", default="hnsw-sim")
    args = p.parse_args(argv)

    # `_parse_int_list` drops empty segments, so `--M ''` (and `--M ','`) parse
    # to `[]` rather than raising. An empty axis makes the `itertools.product`
    # in `run_grid` yield nothing, so the script wrote a **zero-cell grid.json**
    # and exited 0, announcing a successful grid run — a degenerate artifact
    # published as a benchmark (handoff §10). The failure only surfaced later,
    # in a different script, as `plot_hnsw_frontier`'s "grid has no cells;
    # nothing to plot". Reject it here, mirroring `_do_load`'s
    # "--concurrency must contain at least one value" (#117).
    for flag, values in (
        ("--M", args.M),
        ("--ef-construction", args.ef_construction),
        ("--ef-search", args.ef_search),
    ):
        if not values:
            sys.stderr.write(f"{flag} must contain at least one value\n")
            return 2

    # `--out-dir` is operator input too: an unwritable target (a read-only
    # filesystem, a permission-denied dir, or a path component that is a file)
    # makes `run_grid`'s writes — `out_dir.mkdir`, the per-cell result JSON, and
    # the final `grid.json` via `atomic_write_text` — raise OSError, which without
    # this guard escaped as a raw traceback at exit 1. Translate to a clean exit 2,
    # matching the write-seam guard its three grouped sibling writers got in #98
    # (`cost_table.py`, `plot_latency.py`, `plot_hnsw_frontier.py`). `run_grid`'s
    # computation is pure, so OSError here only ever comes from the output writes.
    try:
        run_grid(
            n_vectors=args.n_vectors,
            n_queries=args.n_queries,
            dim=args.dim,
            top_k=args.top_k,
            seed=args.seed,
            M_values=args.M,
            ef_construction_values=args.ef_construction,
            ef_search_values=args.ef_search,
            out_dir=args.out_dir,
            backend_name=args.backend,
        )
    except ValueError as exc:
        # `run_grid` builds a `Workload` (which validates `n_vectors`, `dim`,
        # `n_queries`, `top_k`) and calls `make_backend`, and both report a bad
        # value by raising ValueError. That construction sat inside a try that
        # caught OSError only, so `--n-vectors 0`, `--dim 0`, `--top-k -3`,
        # `--M 0`, `--ef-search 0`, `--backend bogus` and friends all escaped as
        # raw tracebacks at exit 1 (#117).
        #
        # This is the fourth entry point in the repo that constructs a
        # `Workload` + a backend, and the only one that never got this arm:
        # `cli._do_run` and `cli._do_load` both wrap the identical call and land
        # the same ValueError as a clean exit 2 (#83), and `_do_load`'s comment
        # describes precisely this bug shape. Reuse their neutral `error:`
        # prefix so the three read the same.
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except BackendError as exc:
        # A backend failing while a cell runs (#252): a RED collection, an index
        # not built in time, a result violating the (id, score) contract. It is
        # a `RuntimeError`, so it escaped as a traceback at exit 1 here after
        # #209 gave `vector-bench run` and `load` this exact arm in `cli.py`.
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(f"could not write under {args.out_dir}: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
