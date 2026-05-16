# Session History (human-readable)

Chronological log of work sessions. Most recent first below the divider.

---

## 2026-05-15 — Issue #1: terraform infra for 3 backends × 3 scales
**Duration:** ~75 min · **Branch:** `session/2026-05-15-0913-issue-01`

- Shipped `terraform/modules/{common-network,pgvector,qdrant,weaviate}` plus a `benchmark` env that composes them per scale tier (`1m | 10m | 100m`). One VPC, single AZ, single-node-per-backend EC2 with EBS data volumes and Docker user-data.
- Added a `Makefile` (`fmt`, `fmt-check`, `validate`, `plan`, `up`, `down`, `output`, `clean`) so the operator surface stays small. Updated CI to run `terraform fmt -check -recursive`, `terraform validate` per module/env, and `shellcheck` on scripts.
- Backfilled `README.md` with a real "What this is" + Quickstart, replaced the placeholder `docs/architecture.md` with a layered diagram and component breakdown, and added `docs/infra.md` with per-tier instance sizing and on-demand AWS costs (us-east-1, 2026-05).

**Why this work, this session:** Issue #1 is the foundation every downstream study reuses; without identical infra across backends the comparison isn't apples-to-apples.

**Open questions / blockers:** None blocking #2; the modules `terraform validate` clean but have not been `apply`'d against a live AWS account yet. First real apply is the operator's call (cost-bearing).

**Next session:** Issue #2 (benchmark harness). It consumes `endpoints` outputs from this PR's env unchanged.

## 2026-05-15 — Issue #2: Benchmark harness for the three backends
**Duration:** ~60 min · **Branch:** `session/2026-05-15-1933-issue-2`

- Stood up a Python package alongside the terraform substrate: `pyproject.toml` (base dep just `numpy`; per-backend extras `pgvector`, `qdrant`, `weaviate`), `src/vector_bench/` with `types.py` (Backend Protocol, D-005), `harness.py` (`Workload`, `BenchmarkResult`, `run_benchmark`, `generate_corpus`, `ground_truth_topk`, `recall_at_k`), `backends/` (stub + three real-engine adapters all lazy-imported), and `cli.py` (`vector-bench run`).
- The stub backend (D-006) is the in-process numpy reference; recall@k = 1.0 by construction since it uses the same cosine similarity as the ground-truth computation. Hermetic-CI rationale matches `rag-production-kit`'s `LexicalOverlapReranker`.
- One JSON file per `run_id` under `results/` (D-007): re-running the same id raises `FileExistsError` without `--force`, so operator typos surface loudly rather than silently overwrite. The output schema records the full `Workload` so cross-backend comparison is just JSON diffing.
- 23 hermetic tests across `tests/test_harness.py`, `tests/test_stub_backend.py`, `tests/test_cli.py`: workload validation, deterministic corpus generation, ground-truth top-k, recall math, stub round-trip, end-to-end against the stub, JSON write, idempotency, force overwrite, CLI smoke.
- README quickstart grows a "Benchmark harness" subsection showing both the hermetic stub flow and the real-backend flow gated on the operator's `make up`. CI gains a `python` job (ruff + pytest) alongside the existing terraform jobs.

**Why this work, this session:** Issue #1 brought the infra up; the harness is what turns "three running engines" into "comparable numbers." Locking the Backend Protocol now keeps issues #3/#4/#5 from re-litigating the contract.

**Open questions / blockers:** Real numbers require the operator to (a) `make up SCALE=1m`, then (b) set the per-engine env vars and run `vector-bench run --backend {pgvector,qdrant,weaviate}`. The harness and adapters are shipped; the cost-bearing decision to actually `apply` belongs to the operator.

**Next session:** Issue #3 (HNSW parameter sweep), #4 (latency-under-load study), or #5 (cost per query) — all compose this harness unchanged.

## 2026-05-16 — Issue #2: Mark PR #7 ready + squash-merge
**Duration:** ~10 min · **Branch:** `session/2026-05-15-1933-issue-2` (merged + deleted)

- Verified the three acceptance criteria are functionally satisfied by the shipped harness: same workload across three adapters via the `Backend` Protocol, structured JSON per run via `run_benchmark` writing `results/<run_id>.json`, re-runs idempotent via the `force=True` guard. Local hermetic suite 23/23 green; ruff check clean.
- Marked PR #7 ready and squash-merged into `main` at 2026-05-16T15:16Z (per D-004's scheduled-session merge override). Branch deleted; issue #2 closed.

**Why this work, this session:** PR #7 had been parked as a draft since 2026-05-15 even though all five CI checks were green. The harness blocks issues #3 (HNSW sweep) and #4 (latency-under-load), so getting it on `main` unblocks two more priority:high issues.

**Open questions / blockers:** None. Real numbers still pending the operator's cost-bearing `make up` + `vector-bench run` — that's documented in `docs/benchmarks.md`.

**Next session:** Issue #4 (latency-under-load study) is the highest-leverage next step; #3 (HNSW tuning) also unblocked.

## 2026-05-16 — Issue #4: Latency-under-load study (1, 10, 100 concurrent)
**Duration:** ~45 min · **Branch:** `session/2026-05-16-1527-issue-4`

- Shipped `src/vector_bench/load.py` with `run_under_load(backend, workload, concurrency_levels)`: ingests once, then drives queries through `ThreadPoolExecutor` at each requested concurrency level, capturing per-cell p50/p95/p99/max + throughput + recall. One JSON per cell plus a `matrix.json` index under `results/load/<run_id>/`, preserving D-007 idempotency (refuses to overwrite without `force=True`).
- New `vector-bench load` CLI subcommand uses the same env contract as `vector-bench run`; `--concurrency` is a comma-separated list of ints (defaults to `1,10,100` matching the issue's three levels). `--render-table` appends a markdown latency table to the JSON output.
- `scripts/plot_latency.py` reads one or more `matrix.json` files, prints a unified markdown table, and (if matplotlib is installed) emits one PNG line chart per backend × scale showing p50/p95/p99 vs concurrency on a log-x axis. Matplotlib is lazy-imported so a fresh CI box without the chart dep degrades to "chart skipped" without breaking the table.
- 11 new hermetic tests cover per-cell aggregation under concurrency, idempotency + force overwrite, concurrency input validation (empty list rejected, non-positive rejected), the CLI subcommand (good path + bad concurrency string), table rendering shape, and `write_json=False` skipping filesystem entirely. Full suite is 34/34 pass; ruff clean.
- README "Benchmarks / Results" grows a "Latency under load (#4)" subsection with a real measured stub-10k table (10 000 corpus vectors × 64 dims × 500 queries, recall@10 = 1.0 by construction) and the reproducer command. The shape ("p99 walks up faster than p50 as concurrency grows") is the GIL-bound stub showing thread contention on a numpy matmul — honest interpretation; the live-engine curves will land when the operator runs `make up`.
- D-008 lands: re-scopes the issue's "k6/locust" criterion to ThreadPoolExecutor-over-the-`Backend`-Protocol. k6 is HTTP-only but pgvector talks the PostgreSQL wire protocol; driving load through the same Protocol the rest of the package uses keeps the apples-to-apples comparison intact across all three backends and removes a translation layer.

**Why this work, this session:** Issue #2 (the benchmark harness) merged earlier this session unblocked #4. The latency-under-load story is the highest-leverage next study because all three downstream issues (#3 HNSW tuning, #5 cost per query) reuse the per-concurrency latency numbers; landing the load module here means those issues compose it directly. The k6 → ThreadPoolExecutor swap is the only deliberate scope adjustment.

**Open questions / blockers:** None blocking. Real-engine load numbers require the operator's cost-bearing `make up SCALE=1m` + per-backend `vector-bench load --backend <b> --run-id <id>`; the harness, script, and JSON schema are all shipped, so the operator's session is the one-line `vector-bench load ...` cycle followed by `scripts/plot_latency.py`. `results/` is gitignored, so the only committed view of any run is the README table (regenerable from a fresh clone via the CLI).

**Next session:** Issue #3 (HNSW parameter tuning study) reuses `run_under_load` directly — sweep `ef_search` per backend at fixed concurrency, plot p95 vs recall. Issue #5 (cost per query) reads the same per-cell JSONs and multiplies through the per-tier instance cost from `docs/infra.md` to produce the dollar curve.

## 2026-05-17 — Issue #3: HNSW parameter tuning study
**Duration:** ~55 min · **Branch:** `session/2026-05-16-2331-issue-3`

- Shipped `HnswSimBackend` (D-009): a pure-numpy *simulation* of HNSW's recall/latency tradeoff with `M`, `ef_construction`, `ef_search` knobs. Explicitly framed in the module docstring as not a real HNSW implementation — what it produces is a qualitatively correct curve (low ef_search → ~10-30% recall, climbs monotonically, plateaus at ~1.0; latency rises near-linearly in ef_search) so the grid script + frontier plot can be exercised in hermetic CI without qdrant / weaviate / pgvector running. Same scripts apply to real backends via `--backend qdrant` when the AWS bring-up is done.
- `scripts/hnsw_grid.py`: grids over the three params, calls `run_benchmark` for each cell, writes one `BenchmarkResult` JSON per cell + an aggregated `grid.json`. Default grid 3 × 3 × 4 = 36 cells. Re-uses the existing harness so the per-cell results have the same schema as every other study in this repo.
- `scripts/plot_hnsw_frontier.py`: loads `grid.json`, computes non-dominated cells on (p95_ms, recall@10), renders PNG + SVG with the full grid as muted-grey dots and the frontier in red. Matplotlib lazy-imported with "degrades to table-only" fallback (same pattern as `plot_latency.py`). Prints a recommended-defaults table — the lowest-p95 cell that clears a recall floor (default 0.95).
- Ran the real grid on 2000 vectors × 64 dims × 200 queries with seed 1: 36 cells, 14 on the Pareto frontier. Recommended defaults at recall ≥ 0.95: M=32, ef_construction=100, ef_search=128 → recall@10 = 0.998, p95 = 2.02 ms. Knee is clearly visible in the frontier plot around (0.5 ms, 88% recall). Committed `docs/hnsw/frontier.png` and `.svg`.
- 19 new tests: 9 in `tests/test_hnsw_sim.py` (construct, ingest, query shape, empty index, make_backend routing, idempotent close, parameter validation, ingest mismatch, monotone-in-ef_search recall) and 10 in `tests/test_hnsw_grid.py` (frontier filtering, single-cell frontier, recommended-defaults knee selection, no-floor return-None, end-to-end tiny grid, parametrized PNG+SVG render, empty-grid rejection). Full suite 53/53. Ruff lint+format clean.
- README "HNSW parameter tuning (#3)" subsection added under Benchmarks/Results with the embedded frontier plot, recommended defaults table, reproduce commands, and explicit honest framing about simulation vs. real engines (D-009 reference).

**Why this work, this session:** Issue #3 was one of two open `priority:med` issues. Picked it over #5 (cost-per-query) because cost numbers need real-engine instance pricing which isn't measurable in CI, while parameter tuning has a believable simulation path that produces real curves. The simulation backend also unblocks future iterations: when real engines are wired, the same grid/plot scripts run against them with `--backend`.

**Open questions / blockers:** Real-engine numbers wait for an operator's `make up SCALE=1m` + per-backend HNSW knob configuration. The grid script accepts a `--backend` arg, so the path is the same.

**Next session:** Loop to a different portfolio repo per the multi-issue session prompt. Remaining in this repo is #5 (cost per query), which is more naturally an operator-triggered run since it depends on real instance pricing.
