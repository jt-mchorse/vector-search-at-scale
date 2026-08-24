# Session History (human-readable)

Chronological log of work sessions. Most recent first below the divider.

---

## 2026-05-19 — Issue #14: snapshot test for HNSW Recommended-defaults row
**Duration:** ~30 min · **Branch:** `session/2026-05-19-1526-issue-14` · **PR:** #15

- Added `tests/test_hnsw_recommended_defaults_snapshot.py` (3 tests, module-scoped grid fixture). Imports `scripts/hnsw_grid.py` via `importlib.util.spec_from_file_location` so the test doesn't depend on the script being on PYTHONPATH, runs the default 36-cell grid (`n_vectors=2000, n_queries=200, dim=64, top_k=10, seed=1`), picks the knee at `recall ≥ 0.95` by min `p95_ms`, and asserts (a) the parameter triple is `(32, 100, 128)`, (b) `mean_recall_at_k` matches `0.998` within `abs=5e-4`, and (c) the README literally contains the row anchor `| 32 | 100 | 128 | 0.998 |` so a README rewrite that drops the row fails loudly.
- `p95_ms` is intentionally **not** locked — the README's `2.02 ms` is the operator's first-measurement reference, and wall-clock latency varies across machines and Python versions, so asserting it would make the test a CI flake. The selection-logic determinism is what gets snapshotted; the docstring documents this exclusion.
- A live grid re-run on this machine produced `p95=1.89ms`, confirming the wall-clock drift is real even on the same logical machine; the parameter triple + recall stayed exactly where the README quotes them, validating the snapshot's design.
- Tamper-verified by editing the README cell `0.998 → 0.999`; the row-anchor assertion fired with the regen hint pointing at `scripts/hnsw_grid.py`. Reverted to green.

**Why this work, this session:** Continuation of the portfolio-wide drift-lock pattern. The HNSW Recommended-defaults row is the most concrete recommendation this repo's README makes; without the lock, a future tweak to `HnswSimBackend` or the grid axes could silently desync the README from the script's actual output. Pairs well with the existing `test_readme_snapshot.py` (other README invariants) and `test_cost_table.py` (cost-table doc).

**Open questions / blockers:** None — PR ready for review.

**Next session:** Three drift-locks now in place across this repo (README invariants, cost table, HNSW recommended defaults). Continue the multi-issue loop into the next portfolio repo (python-async-llm-pipelines is next in §8).

## 2026-05-19 — Issue #11: drop drift framing + snapshot test
**Duration:** ~25 min · **Branch:** `session/2026-05-19-issue-11`

- Rewrote `What this is` third paragraph past-tense: dropped `this PR, issue #1` anchor, cited every closed issue (#1..#5) plainly.
- Demo section: replaced "60-second demo pending until the harness (#2) ships." with today's two-command hermetic demo (`vector-bench run --backend stub` + `scripts/cost_table.py --dry`) plus the captured-asset follow-up filed as #12.
- `tests/test_readme_snapshot.py` (5 tests) locks: all five `(#N)` refs present, no `pending until ... ships` framing, no `this PR, issue #N` framing, Demo section is honest about today's runnable surface, every relative file reference resolves.

**Why this work, this session:** Sister to the portfolio-wide drift-lock pattern; vector-search-at-scale still carried the two stale fragments after the 2026-05-18 cycle.

**Open questions / blockers:** None.

**Next session:** Continues with Phase A; #12 is priority:low demo capture.

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

## 2026-05-17 — Issue #5: Cost per query at each scale
**Duration:** ~40 min · **Branch:** `session/2026-05-17-2325-issue-5`

- Shipped `src/vector_bench/cost.py` — a dep-free, dataclass-based cost model. `InstancePrice` + `EbsGp3Price` + `PriceTable` for prices; `InfraSpec` for sizing; `monthly_cost()` (instance + storage + IOPS-above-3000 + throughput-above-125-MiB/s); `cost_per_query()` (monthly ÷ qps × 730 hours × 3600 s/month). Unknown instance types raise with the known list and a pointer to the operator override. The 730-hours-per-month constant matches AWS Cost Explorer's billing convention; pinning the constant keeps the table reproducible against AWS's own arithmetic.
- Shipped `src/vector_bench/prices.py` — the AWS us-east-1 public list-price snapshot dated 2026-05-17. Three instances (`m6i.large` $0.0960/hr, `r6i.xlarge` $0.2520/hr, `r6i.4xlarge` $1.0080/hr) covering the three Terraform tiers, plus gp3 EBS ($0.08/GB-mo, $0.005/IOPS-mo over 3000, $0.040/MiB·s-mo over 125). Every entry carries region, vcpus, memory_gib; the `PriceTable` carries `snapshot_date` + `source_url`. The README's bump procedure says: update prices, bump `SNAPSHOT_DATE`, refresh `docs/cost_per_query.md` via the script, commit together.
- Shipped `scripts/cost_table.py` — parses the per-tier sizing locals out of `terraform/envs/benchmark/main.tf` (focused regex; tolerates inline comments; refuses incomplete blocks), reads `throughput_qps` from `results/load/<run_id>/c001.json` per tier, runs each (tier, engine) through `cost_per_query()`, and writes `docs/cost_per_query.md` with the table + an assumptions block. The committed `--dry` mode uses `stub-10k`'s 1623.5 qps as a simulated stand-in across all three tiers and the table marks each row `(simulated)` so the doc never claims real-engine $/query at 10M/100M without real-engine data.
- Monthly costs at the committed Terraform sizing + AWS list: $74.08/mo (1m, m6i.large), $219.96/mo (10m, r6i.xlarge), $915.84/mo (100m, r6i.4xlarge). At 1623.5 qps amortized 24/7 that's $0.02 / $0.05 / $0.21 per million queries across the three tiers. Identical across the three engines per tier because they share the same Terraform-defined instance + EBS sizing; the README's writeup leads with that framing and explicitly calls out the 24/7-amortization caveat.
- 32 new tests in `tests/test_cost.py` (cost-math against fixture prices, IOPS + MiB/s surcharge thresholds, full-stack monthly cost, unknown-instance-type error path, `cost_per_query` divisor logic, seconds_per_month override, non-positive validation) and `tests/test_cost_table.py` (parser against the committed `main.tf`, throughput loader, `build_rows` against sample TF + the real TF, `render_markdown` includes every row + assumptions, `main()` round-trips against the committed repo state and reproduces the expected monthly costs). Suite total 83 passing. Ruff clean.
- Recorded D-010: cost model ships with a documented AWS us-east-1 list-price snapshot (dated + URL'd) and every callsite accepts a `PriceTable` override. Alternatives rejected: ship no defaults (table can't be regenerated by CI; operator wires prices every run), build pricing into the model code (snapshot date + source URL lose visibility), online price lookup (CI DNS flakes). Mirrors the llm-cost-optimizer D-003 posture.
- Public surface widened: `vector_bench` top-level now exports `cost_per_query`, `monthly_cost`, `InfraSpec`, `PriceTable`, `InstancePrice`, `EbsGp3Price`, `CostBreakdown`, `CostPerQuery`, `aws_us_east_1_snapshot`, `HOURS_PER_MONTH`, `SECONDS_PER_MONTH`, `UnknownInstanceTypeError`.

**Why this work, this session:** Issue #5 was the last `priority:med` open in this repo. The cost layer ties the other studies (#3 HNSW tuning, #4 latency under load) together into a story a client could cite — "at 10M scale, $0.05/M queries on a 24/7 sustained workload, list prices, here's the model if your contract differs." With it shipped, `vector-search-at-scale` hits its v0.1 bar (modulo the 60-second demo which still waits on real-engine bring-up).

**Open questions / blockers:** None. Real-engine throughput numbers at 10M / 100M require `make up` + the load harness; the script is wired for that — the operator points `--results-dir` at their real `results/load/<run_id>/` and the table regenerates against measured qps. CI keeps using `stub-10k` until the operator commits real numbers.

**Next session:** This repo has no more `priority:med` open. Loop to another portfolio repo.

## 2026-05-18 — Issue #5 (continuation): Unblock PR #10 fixture
**Duration:** ~12 min · **Branch:** `session/2026-05-17-2325-issue-5` · **PR:** [#10](https://github.com/jt-mchorse/vector-search-at-scale/pull/10) (awaiting CI re-run)

- The two failing cost-table tests on PR #10's CI were calling `scripts/cost_table.py` in `--dry` mode, which reads `results/load/stub-10k/c001.json` as the single-client throughput basis. The file existed locally on the author host but `results/` was excluded in `.gitignore`, so CI saw `FileNotFoundError` even though the local pre-flight passed.
- Re-included the stub-10k subdirectory in `.gitignore` (layer-by-layer because git can't re-include past a parent-excluded directory) and committed the four load-harness artifacts that the run emits. The pattern still excludes every other operator run — `git check-ignore results/hnsw-grid/x.json` confirms.
- `pytest -q` is 83/83 (was 79 passing, 2 failing).

**Why this work, this session:** Phase A auto-review left this PR commented with a clear blocker; the small fix gets a working PR over the merge line, same posture as the embedding-model-shootout lint fix earlier this session.

**Open questions / blockers:** None — pending CI re-run.

**Next session:** All in-flight PRs from earlier sessions are now unblocked (either merged, or pushed-fix awaiting CI). Loop to fresh repos: chunking-strategies-lab is next in §8 build sequence with zero open issues.

## 2026-05-19 — Issue #14: Fix PR #15 cross-platform CI flake
**Duration:** ~35 min · **Branch:** `session/2026-05-19-1526-issue-14` · **PR:** [#15](https://github.com/jt-mchorse/vector-search-at-scale/pull/15) (ready, re-running CI)

- PR #15's three snapshot tests were green on the author host (Mac ARM, Python 3.14) but red on Linux x86_64 CI (Python 3.11/3.12). Both pure-numpy, seed=1 — but cross-platform OpenBLAS variance in float32 dot products perturbs simulated `recall@10` by ~0.001–0.002, and the `min(p95_ms)` knee selection adds a microsecond-scale wall-clock criterion on top. Compounded: CI picked `(M=32, ef_construction=200, ef_search=128, recall=0.999)`, local picks `(32, 100, 128, 0.998)`.
- Redesigned the snapshot to lock what's actually stable across platforms: (1) literal README row anchor (no live grid, catches rewrites), (2) recall@10 at the README's exact parameter triple within `abs=5e-3` (was 5e-4, absorbs BLAS noise), (3) README's row sits in the Pareto-front family `{M=32, ef_search=128, recall ≥ 0.99}`. Dropped the `min(p95_ms)` selection entirely — wall-clock is the wrong axis for a snapshot.
- Tamper-verified each test: README cell mutation fires the row-anchor test; `EXPECTED_RECALL_AT_10 = 0.900` fires the recall-at-cell test with the live recall in the message; `RECOMMENDED_FAMILY_MIN_RECALL = 0.9999` fires the family-membership test.

**Why this work, this session:** Phase A's PR review pass left PR #15 commented as the only blocked PR; fixing it both closes issue #14 (which the PR addresses) and unblocks JT from a noisy CI signal. No new core decision — D-009 (HnswSimBackend is pure-numpy simulation) still governs.

**Open questions / blockers:** None — pushed; awaiting CI re-run.

**Next session:** Loop to another repo. vector-search-at-scale has zero `priority:high` and only one remaining `priority:low` (the 60-sec demo capture, #12), which is gated on real-engine bring-up.

## 2026-05-20 — Issue #16: lock vector_bench public surface
**Duration:** ~20 min · **Branch:** `session/2026-05-20-0336-issue-16`

- Added `tests/test_public_surface.py` (4 standalone + 1 dotted-path + 5 submodule anchors = 10 test items). First variant in the May 2026 pattern series to use the absolute-import AST filter (`module.startswith("vector_bench.")`); the prior five repos used relative imports (`level >= 1`). Six axes: semver, all-bound, all-matches-absolute, package-docstring (8 names from lines 5-9 of `__init__.py`), console-script `vector_bench.cli.main`, five submodule anchors.
- No `__version__` companion change — the package already publishes `__version__ = "0.0.1"` at `__init__.py:71`.
- Tamper-verified four axes: bad version, drop `"Workload"` from `__all__`, in-process delete of `cli.main` (proves the console-script entry-point is guarded), alias-rename `run_benchmark as run_benchmark_v2` (fires three axes simultaneously).
- Full suite 101/101 (was 91; +10 new).

**Why this work, this session:** Ninth strike of the portfolio-wide public-surface hygiene pattern. This repo was skipped during the original five-target sweep (DAY session report named it as already-handled via the `test_hnsw_recommended_defaults_snapshot.py` bug fix), but that bug fix was orthogonal — the public surface still wasn't locked. This PR closes that gap.

**Open questions / blockers:** None — PR ready for review.

**Next session:** Continue the night-session loop into the remaining un-locked Python packages or pivot to a different hygiene surface.

## 2026-05-21 — Issue #12: 60-second demo capture script
**Duration:** ~22 min · **Branch:** `session/2026-05-21-1923-issue-12` · **PR:** #18

- Added `scripts/capture_demo.sh` driving the two surfaces from the README's Demo section: `vector-bench run --backend stub` (with `--force` and a per-run tempdir so re-records don't need cleanup) then `python scripts/cost_table.py --dry` followed by `sed -n '1,30p'` of the rendered file so the markdown table is on camera. `CAPTURE_PACE_SECONDS` honored (default 2, 0 for CI). `CAPTURE_DEMO_N` and `CAPTURE_DEMO_QUERIES` let the operator vary takes.
- Added `tests/test_capture_demo_smoke.py` (3 tests) that runs the script with `PACE=0` and asserts: vector-bench's JSON-output load-bearing keys (`mean_recall_at_k`, `query_latency` with p50/p95/p99 sub-keys, `run_id=demo-capture`, `top_k=10`); the cost table markdown header signature matches what `test_cost_table.py` locks separately; every tier × engine row (1m/10m/100m × pgvector/qdrant/weaviate) appears; script exists and is executable.
- Fixed the README Demo block's stale `--k 10` flag to the actual CLI's `--top-k 10` and added the required `--run-id` / `--results-dir`. The capture script forced the question of what the real invocation is and shipping a working capture while the README docs were broken would be silly. Added a paragraph pointing at the new capture script and smoke test. 104/104 tests pass, ruff clean.

**Why this work, this session:** Seventh repo to land the `scripts/capture_demo.sh` pattern this week. Issue #12 was the explicit owner of the README's "pending 60s demo" claim and was sitting at `priority:low` — closing it cleanly closes the last quality-bar gap in this repo's v0.1 story.

**Open questions / blockers:** None. The Terraform `make validate` third surface is deliberately out of scope; the smoke test can't assume Terraform is installed, and the capture script's epilogue points the operator at that command for local pre-record validation.

**Next session:** Continue the multi-issue loop on the remaining stale repos. python-async-llm-pipelines #14 is the next in §8 build order.

## 2026-05-22 — `vector-bench run` refuses concurrency > 1 (#19, D-011)

**Duration:** ~30 min. **Issue:** [#19](https://github.com/jt-mchorse/vector-search-at-scale/issues/19). **PR:** TBD.

The `run` subcommand accepted `--concurrency N` and propagated it through to `Workload`. The harness recorded the value on the output JSON. But `run_benchmark` executed queries serially in a tight loop — `workload.concurrency > 1` was silently ignored. A reader of the resulting JSON would assume the recorded `query_latency` percentiles reflected the recorded concurrency level. They did not. For a repo whose tagline is "the kind of doc you'd cite in an architecture review", a latency stat that lies about its concurrency was the credibility leak to close.

The fix mirrors the shape of `chunking-strategies-lab`'s D-011, closed earlier today: promote a documented-only constraint to runtime enforcement. `run_benchmark` now raises `ValueError` immediately when `workload.concurrency != 1`, with a message that names `run_under_load` (the concurrent entry point, D-008) and references D-011. The gate fires before the filesystem-existence check, so a misconfigured call doesn't leave behind a stale results path that traps a future retry. The CLI's `--concurrency` help text on the `run` subcommand is reworded to say "reserved; values > 1 are refused — use `vector-bench load` for concurrency studies". The README's Benchmark-harness section gets a one-paragraph note explaining the split.

Why prioritized: this was the third post-v0.1 silent-numerical-bug fix today (after embedding-model-shootout #17 word-bigrams and chunking-strategies-lab #19 late-chunking embedder consistency). Closing them in sequence is bracing the portfolio against exactly the failure mode the handoff §10 spends its longest rule on: "do not invent benchmark numbers". A stat that's silently wrong is functionally identical to a fabricated one. Open questions / followups: none. An explicit `allow_misreport=True` opt-out can be wired up later if a curious caller wants it, but YAGNI.

## 2026-05-22 — Issue #21: architecture doc reflects all three shipped layers, not the Terraform-PR-only pre-shipping state

**Duration:** ~25 min. **Issue:** [#21](https://github.com/jt-mchorse/vector-search-at-scale/issues/21). **PR:** [#22](https://github.com/jt-mchorse/vector-search-at-scale/pull/22).

`docs/architecture.md` was committed alongside the Terraform PR (issue #1) and never reframed when the harness (#2), HNSW tuning (#3), latency-under-load (#4), and cost-per-query (#5) shipped. The L3–4 prose still said "This PR (issue #1) ships the bottom one. The harness (issue #2) and the per-axis studies (issues #3, #4, #5) plug into the same VPC and the same set of three backends" — `this PR` framing left over from the first commit, factually wrong for the next four shipped layers. The mermaid `BENCH` node was tagged "future · issue #2" while `src/vector_bench/`, `scripts/{hnsw_grid,plot_latency,cost_table}.py`, and the result JSONs under `docs/{benchmarks,cost_per_query,hnsw}` had been on disk and exercised by CI for months. Three of the section headers (`Layer 1 — Infra (this PR · issue #1)`, `Layer 2 — Benchmark harness (issue #2 · pending)`, `Layer 3 — Per-axis studies (issues #3, #4, #5 · pending)`) each carried a pre-shipping label. Root README was already correct (locked by `tests/test_readme_snapshot.py` + `tests/test_hnsw_recommended_defaults_snapshot.py`); only `docs/architecture.md` lagged.

Rewrote the doc to a steady-state pipeline view. The mermaid diagram gains a two-class legend: `shipped` (green) for nodes that run in code today, `opkey` (yellow) for nodes that are wired end-to-end but require operator-supplied AWS credit (the three EC2 instances). The BENCH node becomes green with the harness annotation `(#2)`. Each layer's header drops its pre-shipping label. Layer 2's `vector-bench run` bullet now documents the D-011 runtime gate (refuses `--concurrency > 1`; use `vector-bench load` instead) inline where a reader of the architecture doc would actually look for it. Added "What's still operator-supplied" naming exactly what genuinely needs operator action (`terraform apply` + AWS credit; the captured 60-second walkthrough binary) and a "Where to look next" footer enumerating each layer's code + test files, parallel to the embedding-model-shootout and chunking-strategies-lab architecture-doc shape.

Lock-against-drift: `tests/test_architecture_doc.py` is the Python sister of the architecture-doc lock that landed earlier in the session in `embedding-model-shootout` PR #20 and of the JS variants in `mcp-server-cookbook`, `nextjs-streaming-ai-patterns`, and `ai-app-integration-tests`. Three invariants: every backtick-quoted `src/vector_bench/...`, `scripts/...`, `terraform/...`, `results/...`, `docs/...`, `tests/...`, or `Makefile` token resolves on disk (placeholder shapes containing `<...>` or `{...}` are skipped, since they document a template rather than a literal path); every issue in `KNOWN_SHIPPED_ISSUES = (1, 2, 3, 4, 5, 14, 16)` is referenced at least once (#11 README pivot, #12 operator artifact, and #19 runtime gate are intentionally excluded — each is documented elsewhere); banned phrases (`this pr`, `· pending`, `· future`, `(unfiled)`, `to-be-filed`) are absent. Three belt-and-braces hard-pin tests lock `BANNED_PHRASES`, `KNOWN_SHIPPED_ISSUES`, and `RESOLVABLE_PREFIXES` to their exact contents. Tamper-verified three ways: each axis fires with the specific drift quoted. Full suite 115/115 (was 108; +7 new). `ruff check . && ruff format --check .` clean.

Thirteenth post-v0.1 drift fix in the portfolio pattern, fourth architecture-doc lock test in this session. The portfolio now has seven repos with an architecture-doc lock test (the four from this session plus three earlier).

**Why this work, this session:** Loop iteration in a day session. Three architecture-doc fixes already landed today across other repos with the same shape (`nextjs` #18, `ai-app` #18, `mcp-cookbook` #22, `emb-shootout` #19). Issue #21 was filed mid-session as `priority:med` then closed in the same session per the session prompt's loop protocol.

**Open questions / blockers:** None — PR opened ready for review.

**Next session:** Loop forward if pace allows; `prompt-regression-suite` or `llm-eval-harness` are candidates if their `docs/architecture.md` (where present) has similar drift. Otherwise wrap session within the cap.

## 2026-05-23 — Architecture-doc active-decision-range axis + 9-decision backfill (#23)

**Duration:** ~22 min. **Issue:** [#23](https://github.com/jt-mchorse/vector-search-at-scale/issues/23). **PR:** [#24](https://github.com/jt-mchorse/vector-search-at-scale/pull/24).

Seventh of twelve repos to ship the active-decision-range upper-bound axis on its architecture-doc lock (sister to `llm-eval-harness` PR #32 and `embedding-model-shootout` PR #22 earlier today, plus four repos this week). Of 11 active D-NNN entries in `MEMORY/core_decisions_ai.md`, **only D-011 was cited** in `docs/architecture.md` before this PR — D-002 through D-010 were all silently missing despite governing every load-bearing posture choice in the substrate. Backfilled inline at the layer each governs: D-002 single-AZ + D-003 Weaviate-OSS + D-004 single-node-pinned-tags (Layer 1), D-005 Backend Protocol + D-006 stub-recall=1 + D-007 one-JSON-per-run + D-008 ThreadPoolExecutor-not-k6 + D-009 hnsw_sim-pure-numpy (Layer 2), D-010 AWS us-east-1 PriceTable (Layer 3). Tamper-verified three axes.

**Why this work, this session:** Third issue in today's multi-issue loop after `llm-eval-harness` PR #32 and `embedding-model-shootout` PR #22. Vector-search-at-scale had the **largest backfill gap** in the portfolio so far for this pattern — 9 uncited active decisions in a doc that should be the canonical layer-by-layer reference.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Apply same pattern to `prompt-regression-suite` and `agent-orchestration-platform` (last two arch-doc tests lacking the D-axis).

## 2026-05-24 — Issue #25: `cost_table.py` --dry/--no-dry + --load-results

**Duration:** ~30 min. **Issue:** [#25](https://github.com/jt-mchorse/vector-search-at-scale/issues/25). **Branch:** `session/2026-05-24-0349-issue-25`.

`scripts/cost_table.py` shipped a `--dry` flag the rest of the script never read, and its docstring promised a `--load-results PATH` per-tier flag that didn't exist on the parser at all. Every tier row in the rendered `docs/cost_per_query.md` was hardcoded `(simulated)` regardless of inputs — the docstring's clear intent (real vs simulated marker per tier) wasn't implemented.

Fixed in two parts. `--dry` now uses `argparse.BooleanOptionalAction` (default True); `--dry` labels non-overridden rows `(simulated)` and `--no-dry` drops the marker. New `--load-results TIER=PATH` (repeatable) per-tier override reads that tier's `c001.json` from the override dir and labels the row `(real)` regardless of `--dry`. Manual tier validation against `SCALE_TIERS` so unknown tier exits 2 with the inventory on stderr; malformed entries (missing `=`, empty path) likewise exit 2.

Five new tests cover the per-row simulated marker under default `--dry`, `--no-dry` dropping the marker from rows, the override happy path (one tier real + others stay simulated), the unknown-tier exit-2 stderr-inventory path, and the malformed-entry exit-2 path. A small `_table_row_lines` test helper filters the rendered markdown to per-tier rows via substring (the explanatory prose paragraph also mentions `(simulated)` as documentation; the helper avoids false positives). The existing snapshot tests pass unchanged — default `--dry` output is byte-identical to the prior committed `docs/cost_per_query.md`.

The docstring's Modes section was rewritten to match the implementation. Same shape as `llm-cost-optimizer` #30 earlier this session (revived an unreachable real-API guard), just with a richer surface — per-tier overrides instead of just a flag flip.

**Why this work, this session:** Seventh issue in the night-session multi-issue loop. First non-pure-parity fix tonight — this closed an actual doc/impl mismatch (docstring lied about `--load-results`), not just added a flag.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue to build-sequence #8 (`python-async-llm-pipelines`).

## 2026-05-25 — Issue #27: operator-supplied dataclasses validate fields in __post_init__
**Duration:** ~20 min · **Branch:** `session/2026-05-24-issue-27`

- Three frozen dataclasses in `src/vector_bench/cost.py` (`InstancePrice`, `EbsGp3Price`, `InfraSpec`) accepted negative rates, negative counts, and empty strings without raising. A negative `usd_per_hour` flowed through `monthly_cost` at line 194 (`instance.usd_per_hour * HOURS_PER_MONTH`) and silently inverted the sign of `total_usd_month` — the published `docs/costs.md` would have shown the vector DB **paying the operator** to run. The `max(0, ...)` clamps at lines 196 and 198 made the harm worse for `provisioned_iops` and `provisioned_throughput_mibps`: a negative input silently rounds to zero, omitting a real cost line.
- Added `__post_init__` on each of the three dataclasses raising `ValueError(f"{field} ...")` with the offending field named and the violated bound shown. Zero accepted on rate fields (free-tier / test fixture is meaningful). Source comments document the D-010 anchor and the `max(0, ...)` clamp interaction.
- Twenty new collected cases in `tests/test_cost.py` under a `#27` block, plus three `_valid_x_kwargs()` test helpers that centralize the fixtures so each negative test only mutates the field under test. Covers: 5 InstancePrice numeric × bad-value, 2 InstancePrice empty-string, 3 EbsGp3Price rate × bad-value, 2 EbsGp3Price baseline × bad-value, 1 EbsGp3Price empty-region, 3 InfraSpec numeric × bad-value, 3 InfraSpec empty-string, plus one inclusive-zero acceptance test across all three dataclasses. Full suite 143/143 (was 123 after #25).

**Why this work, this session:** Direct mirror of three sister fixes shipped today: `llm-cost-optimizer` #34 PR #35 (`ModelPricing.__post_init__`), `rag-production-kit` #36 PR #37 (`ModelPrice.__post_init__`), `embedding-model-shootout` #29 PR #30 (`SweepResult.__post_init__`). All four cost-aware repos in the portfolio now defend their published cost dashboards consistently. D-010 anchors the contract — it already mirrored `llm-cost-optimizer` D-003's "unknown raises" posture; this extends it to "negative raises" on the rate fields.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Sixth Phase B+C target of today's 180-min day session. Build sequence #8 (`python-async-llm-pipelines`) is the natural next pickup if time remains before the 15-min cleanup buffer.

## 2026-05-25 — Issue #29: Workload and recall_at_k isinstance(int) guards
**Duration:** ~25 min · **Branch:** `session/2026-05-24-issue-29`

- Two existing sign-only checks accepted non-int (float, NaN, +Infinity, bool, str). `Workload.__post_init__` looped five integer-typed count fields (`n_vectors`, `dim`, `n_queries`, `top_k`, `concurrency`) with `<= 0` comparison — NaN passes (NaN comparisons always false), fractional silently truncates via `range(int(x))` in the load loop, bool subclasses int and flattens operator intent. `recall_at_k(k)` had the same sign-only shape; non-int k silently miscounts via set/list slicing.
- Tightened both to require `isinstance(x, int)` (bool excluded explicitly since Python's bool subclasses int and a count field's operator intent is never a boolean). Workload keeps the existing per-field "must be positive" message; the new isinstance check fires with "must be an int" before reaching it. `recall_at_k` message tightened from "must be positive" to "must be a positive integer"; one pre-existing test pinning the old message updated in place.
- 26 new parametrized tests in `tests/test_harness.py` under a `#29` block: `field × bad_value` matrix for the five Workload fields (5 × 5 = 25 cases) + boundary acceptance regression; per-bad-value rejection for `recall_at_k`. Test count 173.

**Why this work, this session:** Eleventh Phase B+C target in the 360-min night session. Second PR in vector-search-at-scale tonight; the first was via the Phase A fixup-merge of #28 (cost dataclass `__post_init__` sign-only validation). The two together cover both the benchmark surface and the cost surface.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the loop. Remaining unvisited-tonight-for-second-iteration: `python-async-llm-pipelines`. After that, all twelve repos will have received at least one Phase B+C iteration tonight (or have a second one).

## 2026-05-26 — Issue #31: HnswSimBackend completes the #29 sweep
**Duration:** ~25 min · **Branch:** `session/2026-05-25-2300-issue-31`

- `HnswSimBackend.__post_init__` at `src/vector_bench/backends/hnsw_sim.py:69-76` was the only remaining sign-only `<= 0` construction site in `src/vector_bench/` after #29's `Workload` + `recall_at_k` sweep. Tightened M / ef_construction / ef_search with `isinstance(value, int) + reject bool` above the existing `<= 0` check, inside the existing field-loop — adds one condition per iteration, no new branches in the file structure.
- Closed five silent failure modes — most importantly the worst-harm-class one: `M=True` silently bound `self.M = True`, and `topk_local = argsort(-sims)[:self.M]` returned 1 neighbor instead of 16. **Recall silently collapsed; benchmark numbers looked fine but were wrong** — exactly the failure mode this repo exists to defend against. Also closed: `M=1.5` / `M=16.0` (silently bound, `[:1.5]` raised `TypeError` deep in ingest), `M=NaN/Inf` (silently bound, opaque numpy errors at query time), `ef_construction=True` (silently set `sample_size=1` per row → recall terrible, no error), `ef_search=True` (silently capped beam search frontier).
- Three new parametrize blocks (one per field) over the existing `_BAD_INT = [1.5, NaN, +Inf, True, "16"]` shape from `tests/test_harness.py`, plus an acceptance pin over `[1, 8, 16, 32, 64]`. Added `# noqa: SIM300` to three acceptance asserts because ruff's Yoda-condition rule treats the `good` parametrize parameter name as a constant (codebase otherwise uses the standard `attr == value` shape). 20 new collected cases; full suite 173 → 193. Ruff clean.

**Why this work, this session:** Fourth Phase B+C target in the 360-min night session, continuing the portfolio-wide validation sweep. Picked via build-sequence #7 among repos with un-swept constructors after `prompt-regression-suite#38` (#2) and `chunking-strategies-lab#32` (#3).

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the loop. `python-async-llm-pipelines` (build #8) had only one Phase A fixup PR today and may have un-swept sites in its async core / benchmark dataclasses.

## 2026-05-26 — Issue #33: Atomic writes — last repo, completes portfolio 12-of-12 saturation
**Duration:** ~22 min · **Branch:** `session/2026-05-26-1950-issue-33`

Five production sites used `Path.write_text`: load.py's per-cell loop (most blast-radius-y — partial state across cell files breaks the matrix-load reader silently) + the top-level matrix.json, harness.py's per-backend result JSON, scripts/hnsw_grid.py, and scripts/cost_table.py (renders into the README's "Cost analysis" section). New `vector_bench/io_utils.py` matches the portfolio standard; all five sites routed; 6 unit + 2 integration tests added. D-012 codifies the placement. Full suite 193 → 201.

**Why this work, this session:** Sixth and final Phase B issue of today's DAY session. Completes the 2026-05-26 portfolio atomic-write arc at 12 of 12 repos: nextjs-streaming-ai-patterns has no on-disk write paths to harden, so every repo in the portfolio that emits artifacts now does so atomically.

**Open questions / blockers:** none.

**Next session:** Portfolio atomic-write arc is saturated. Future sessions should pivot to a different harm class (input-trust on external API responses, resource leaks on error paths, test-determinism guarantees) or to higher-level work (the operator-supplied 60-second demo capture remaining on three repos: llm-cost-optimizer#18, nextjs-streaming-ai-patterns, ai-app-integration-tests).

## 2026-05-26 — Issue #35: README decision-range upper-bound lock
**Duration:** ~6 min · **Branch:** `session/2026-05-26-2330-issue-35`

- Added `tests/test_readme_decision_range.py`.
- Added `D-002…D-012` citation under `## Architecture`.

**Why this work, this session:** Propagation 6 of 10 of the cross-portfolio drift class.

**Open questions / blockers:** none.
**Next session:** Continue to python-async-llm-pipelines.

## 2026-05-27 — Issue #37: CONTRIBUTING.md cadence-wording propagation
**Duration:** ~3 min · **PR:** #38

- Replaced pre-D-008 `~60-minute session cap` line with D-008 (180/360 min, multi-issue loop) and D-004 (Phase A PR auto-merge) wording, matching the bootstrap template post-portfolio-ops#3.

**Why this work, this session:** Iteration in the autonomous NIGHT session propagation arc for portfolio-ops#3.

**Open questions / blockers:** none.

**Next session:** continue portfolio propagation.

## 2026-06-02 — Issue #39: Observability-parity to_dict() + dump_*_json wrappers
**Duration:** ~30 min · **Branch:** `session/2026-06-02-0313-issue-39`

- Replaced `dataclasses.asdict(self)` with explicit field-by-field `.to_dict()` methods on five dataclasses: `Workload` (6 fields), `LatencyStats` (4 fields), `BenchmarkResult` (11 fields), `LoadCell` (10 fields), `LoadMatrix` (4 fields). Each pinned by a `sorted(d.keys()) == [...]` test so a future internal-only field can't silently leak into the output JSON consumed by `scripts/plot_hnsw_frontier.py`, `scripts/plot_latency.py`, and `scripts/cost_table.py`. Existing `.to_json()` methods kept as thin delegates so `cli.py` and downstream callers don't churn.
- Added package-level `dump_benchmark_json(path, *, result, force=False)` in `harness.py` and `dump_load_matrix_json(out_dir, *, matrix, force=False)` in `load.py`. Both pull the inline `force`-check + `json.dumps` + `atomic_write_text` triples out of `run_benchmark` / `run_under_load`. The runners call the wrappers internally (single source of truth for the file-writing logic) and pre-flight the `force=False` collision before paying workload cost. Honors D-007 idempotency + D-012 atomic_write_text routing without a new decision.
- Dropped the no-longer-needed `_json_default` fallback handler in `load.py` — `to_dict()` returns native JSON types only, so `json.dumps` doesn't need a `default=` arm. One less surface for downstream readers to reason about.
- Extended the public surface: `LoadCell`, `LoadMatrix`, `dump_benchmark_json`, `dump_load_matrix_json`, `run_under_load` added to `vector_bench/__init__.py` top-level imports and `__all__`. `tests/test_public_surface.py` `SUBMODULE_ANCHORS` extended with the `load` submodule anchored by `run_under_load`. Arch-doc lock `KNOWN_SHIPPED_ISSUES` extended to include `#39` (both the module constant and the hard-pin test).
- Test suite grew from 217 → 240 cases (23 new): 4 `Workload.to_dict`, 2 `LatencyStats.to_dict`, 4 `BenchmarkResult.to_dict` (incl. shallow-copy of `extra`), 4 `dump_benchmark_json` (round-trip, refuse-overwrite, force, monkeypatch routing proof), 2 `LoadCell.to_dict`, 2 `LoadMatrix.to_dict`, 5 `dump_load_matrix_json`. Arch-doc lock caught my first draft (the new doc section quoted banned phrase "this PR" — corrected to "with #39 landed"), validating the lock pre-merge.

**Why this work, this session:** Iteration 1 of the night session loop. `vector-search-at-scale` was the last Python repo in the portfolio still serving JSON shapes via bare `dataclasses.asdict`, the same gap closed today across `python-async-llm-pipelines` (#45), `rag-production-kit` (#51), and `llm-cost-optimizer` (#51 + #53). Closing it here saturates the observability-parity arc across all four Python JSON-writing repos.

**Open questions / blockers:** none — ready for review.

**Next session:** Continue the night-session multi-repo loop. Remaining untouched-since-2026-05-27 candidates: `mcp-server-cookbook` (TS), `nextjs-streaming-ai-patterns`, `ai-app-integration-tests`. Each may have observability or validate parity opportunities; TS variant of the dump_*_json pattern is the natural extension.

## 2026-06-17 — Issue #41: Workflow YAML-parseability lock
**Duration:** ~7 min · **Branch:** `session/2026-06-17-1924-issue-41`

Added `tests/test_workflows_yaml_parseable.py` (3 tests for `ci.yml`)
and pulled `pyyaml>=6.0` into `dev` extras.

**Why this work, this session:** Eighth hop of the `portfolio-ops#30`
propagation arc.

**Open questions / blockers:** none — PR #42 open.

**Next session:** continue propagation to the remaining 4 repos.

## 2026-06-18 — Issue #43: timeout-minutes guard + lock test
**Duration:** ~15 min · **Branch:** `session/2026-06-18-0326-issue-43`

- Added `timeout-minutes` to every job in `ci.yml`: 15 for `fmt`,
  `shellcheck`, `python`, `memory-check`; 20 for `validate` (terraform
  init across every module + env on a cold provider cache).
- Added `tests/test_workflows_timeout_minutes.py` — 16 new tests
  (1 smoke + 5 jobs × 3 parametrized invariants).

**Why this work, this session:** sixth hop in the portfolio-wide
timeout-minutes propagation arc started by `llm-eval-harness` #63.

**Open questions / blockers:** none. When the 1M/10M/100M benchmark
suites from handoff §2 land, the policy ceiling should be revisited.

**Next session:** continue propagation. Remaining repos with findings:
python-async-llm-pipelines, agent-orchestration-platform (TS),
mcp-server-cookbook (TS), ai-app-integration-tests (TS), portfolio-ops.

## 2026-06-18 — Issue #45: concurrency guard + lock test
**Duration:** ~8 min · **Branch:** `session/2026-06-18-1530-issue-45`

- Added top-level `concurrency:` to `ci.yml`.
- Copied lock test; docstring origin updated.

**Why this work, this session:** eighth per-repo hop in the
concurrency-lock arc.

**Open questions / blockers:** none. Test count 238 → 245.

**Next session:** continue propagation to remaining 4 repos.

## 2026-06-22 — Issue #47: run_under_load — measure wall-clock throughput
**Duration:** ~30 min · **Branch:** `session/2026-06-22-1924-issue-47`

- Found via a Phase A Explore-subagent sweep over the core package (harness/load/cost/prices/hnsw_sim/stub/io_utils/cli/scripts) — picked because vector-search-at-scale was >36h stale at build-sequence position 7. `run_under_load` computed `throughput_qps` as `n_queries / (sum(latencies)/c)`: dividing the *sum* of overlapping per-query service times by the concurrency bakes in perfect linear scaling, so the QPS column grew with `c` by construction even for a backend that gains nothing — and could report a throughput above the backend's physical serialization ceiling. Same "silent numerical quality bug" class as D-011.
- Fix: the harness already runs the queries concurrently for real, so measure the query phase's wall-clock and use the canonical `throughput_qps = n_queries / wall_clock_s`. Dropped the now-dead `max(c, 1)`. Latency stats, per-cell JSON, and idempotency untouched.
- Demonstration: a lock-serialized 5 ms backend is physically capped at ~200 QPS; pre-fix the formula reported 297 QPS at concurrency 16. Added a property-invariant test asserting reported throughput stays at/below the serialization ceiling. Verified it fails pre-fix, passes post-fix. Full suite green, ruff clean. PR #48 ready.

**Why this work, this session:** the repo had zero open issues; a dogfood sweep surfaced a real benchmark-number correctness bug (physically-impossible reported QPS) — strictly higher value than a synthetic fill.

**Open questions / blockers:** none.

**Next session:** the throughput number is now measured rather than assumed. A possible follow-on (deferred, not filed): separating per-query *service* time from *queue wait* in `_execute_at_concurrency`, which would let latency stats under load distinguish the two — only worth it if a future session needs substantive work here.

## 2026-06-23 — Issue #49: latency chart plotted cells in stored (unsorted) order
**Duration:** ~20 min · **Branch:** `session/2026-06-23-0341-issue-49`

- Fixed a misleading-chart bug in `scripts/plot_latency.py`. It built the p50/p95/p99 line series from `m.cells` in stored order and plotted them on a log x-axis. Cells are persisted in `--concurrency` input order, so a non-ascending list (e.g. `100,10,1`) drew a backtracking zig-zag, even though `render_table` (which sorts) produced a correct table from the same matrix.
- Sorted cells ascending by concurrency in `_load_matrix`, canonicalizing the matrix for the plot path. Added the script's first test (`tests/test_plot_latency.py`) using `importlib` + the real serializer. Red pre-fix, green post-fix. Suite 246 → 247, ruff clean.

**Why this work, this session:** found by the night session's Phase A dogfood wave; a real, uncovered defect on a documented entry point. Lower severity (chart cosmetics — the data and table were always correct), but a clean fix with a unit test.

**Open questions / blockers:** none.

**Next session:** chart styling/axis-label polish left out of scope.

---
## 2026-06-23 — Issue #51: cost_per_query accepted NaN/inf throughput
**Duration:** ~15 min · **Branch:** `session/2026-06-23-0412-issue-51`

- Fixed a non-finite-input gap in `cost_per_query`. The `<=0` guard let NaN and +inf through (both compare False to `<= 0`), so a nan throughput produced `usd_per_query=nan` and an inf throughput a fabricated `$0.00/query`. `run_under_load` emits inf throughput when query time rounds to 0, which round-trips through JSON into this public API and `scripts/cost_table.py`.
- Added a `math.isfinite` check (message keeps the "must be positive" substring). Parametrized nan/inf/-inf rejection test. Red pre-fix, green post-fix. Suite 248 → 249, ruff clean. Completes the finiteness-guard arc (#27/#29/#31).

**Why this work, this session:** found by a different-angle second pass in the night session's Phase A dogfood wave; a real silent-garbage-cost bug reachable through the documented CLI → cost_table pipeline.

**Open questions / blockers:** none.

**Next session:** `seconds_per_month` shares the `<=0` shape but is a fixed constant, never reachably non-finite; left out of scope.

---
## 2026-06-25 — Issue #53: non-finite prices poisoned the cost table at the construction seam
**Duration:** ~30 min · **Branch:** `session/2026-06-25-1519-issue-53`

- `InstancePrice` and `EbsGp3Price` validated their float rate fields with sign-only checks (`value < 0.0`) added in #27. Since `nan < 0.0` and `float("inf") < 0.0` are both False, a non-finite `usd_per_hour` / `memory_gib` / EBS rate slipped past construction, flowed through `monthly_cost()` into `CostBreakdown.total_usd_month`, and out of `cost_per_query()` as a fabricated `nan` / `$0.00` (or `Inf`) row in the published `docs/cost_per_query.md` — with no diagnostic.
- Widened the float-rate guards to `math.isfinite(...) or value < 0.0` (message unified to `"<field> must be a finite number >= 0.0"`), mirroring the downstream `cost_per_query` qps guard (#51) and the sibling `llm-cost-optimizer.pricing` finiteness sweep (#71). Integer fields stay on the sign-only check (ints can't be non-finite). 15 new tests, red-without / green-with, suite 250 → 265, ruff clean. Purely a rejection-widening — no valid price changed, so the cost-table snapshot is untouched.

**Why this work, this session:** vector-search-at-scale was the earliest repo in build sequence among those >36h stale (priority tier all fresh after the llm-cost-optimizer work this run; mcp-server-cookbook's #54/#55 are human-blocked decision-revisit, skipped per D-007). The construction-seam finiteness gap was the un-closed half of the repo's own defensive-validation arc.

**Open questions / blockers:** none.

**Next session:** `InfraSpec` int fields and the EBS baselines are int-typed and can't be non-finite, so no parallel guard is needed there; the finiteness arc on this cost model is now complete across both the construction seams and `cost_per_query`.

## 2026-06-26 — Issue #55: BenchmarkResult finiteness guard + no invalid-JSON Infinity
**Duration:** ~25 min · **Branch:** session/2026-06-26-0331-issue-55

- `run_benchmark` fabricated `ingest_rows_per_sec=float("inf")` on a 0-second ingest, which `dump_benchmark_json` serialized (json.dumps default `allow_nan=True`) as the bare token `Infinity` — invalid JSON that strict parsers reject, and a fabricated number (handoff §10). `BenchmarkResult` was the one result dataclass without a `__post_init__`, unlike `Workload`/`InstancePrice`/`cost_per_query`.
- Replaced the `inf` fabrication with a fail-loud guard at the measurement site (non-positive `ingest_seconds` → clear `ValueError`), and added a `BenchmarkResult.__post_init__` rejecting non-finite/out-of-range floats. 19 tests incl. a strict `json.loads` round-trip proving no `Infinity`/`NaN` token.

**Why this work, this session:** Surfaced by a Phase A dogfood Explore agent, hand-verified; lower reachability than the rag #86 sibling so filed priority:med. Closes a latent output-integrity gap (sibling of rag #80).

**Open questions / blockers:** none. `load.py`'s `LoadCell`/matrix dataclasses may share the missing-guard pattern — deferred follow-up.

**Next session:** harness output is now finiteness-guarded; consider the load.py matrix dataclasses next time in this repo.

## 2026-06-26 — Issue #59: LatencyStats finiteness guard (the nested dataclass #55 missed)
**Duration:** ~20 min · **Branch:** `session/2026-06-26-2053-issue-59`

- `#55` added a finiteness/range guard to `BenchmarkResult` because a non-finite field reaches `dump_benchmark_json`'s `json.dumps` (default `allow_nan=True`) and serializes as the invalid-JSON token `NaN`/`Infinity`. But `BenchmarkResult.query_latency` is a nested `LatencyStats` (`p50/p95/p99/max_ms`) — a public, frozen, directly-constructible dataclass with no `__post_init__`. #55 guarded the parent's direct numerics but not this nested sibling, so a non-finite/negative latency flows straight through to the dump. Reproduced: `LatencyStats(p95_ms=nan, p99_ms=inf, max_ms=-5)` constructs and dumps `{"p95_ms": NaN, "p99_ms": Infinity, ...}`, which a strict parser rejects.
- Added `LatencyStats.__post_init__` rejecting non-finite/negative fields, same message shape as `BenchmarkResult` (#55), `Workload` (#29), the cost dataclasses (#53), and `LoadCell`. The normal `perf_counter`-driven path is unaffected. New `TestLatencyStatsFinitenessGuard` (valid dumps through the strict parser, per-field rejection, zero accepted). Suite → 313 passed, ruff clean.

**Why this work, this session:** fifth issue of a multi-issue DAY run; a night-session lead flagged a `BenchmarkResult` gap that turned out already closed by #55 — but reading #55's own comment surfaced that it missed the nested `LatencyStats`, completing that finiteness-guard arc.

**Open questions / blockers:** none.

**Next session:** a percentile-monotonicity invariant (`p50 <= p95 <= p99 <= max`) on `LatencyStats` is the next candidate if a stronger result-integrity guard is wanted — deliberately out of scope here (separate from JSON validity).

## 2026-06-27 — Issue #61: HNSW-sim recall decreases in M (inverted contract)
**Duration:** ~25 min · **Branch:** `session/2026-06-27-0010-issue-61`

- `HnswSimBackend.query` capped beam-search iterations at `ef // M` — inversely proportional to M. Because a denser graph (higher M) got fewer expansion hops, recall *fell* as M rose (mean recall@10 at ef_search=20: M=4 0.61, M=8 0.47), contradicting the docstring ("Higher M = denser graph = better recall at small ef_search") and the HNSW-paper curve shape the module cites.
- Fixed by bounding iterations on `ef_search` alone (`max(2, ef // 4)`) — still worst-case linear in ef_search but no longer throttled by graph density. Recall is now non-decreasing in M (0.61 → 0.89 → 0.98 → 1.0) and ef_search-monotonicity is preserved. Added an M-monotonicity regression test (the existing one only swept ef_search). Suite 313 → 314, ruff check clean.

**Why this work, this session:** seventh issue of a multi-issue DAY run. embedding-model-shootout dogfood came back clean (healthy repo, no fabricated work), so I fell through to vector-search-at-scale and filed #61 from a reproduced finding.

**Open questions / blockers:** process slip — I coded before filing the issue here, then filed #61 and corrected the guessed `#59` refs in the code/test; for future issues, file first then code. Note: this repo's Python CI is `ruff check src/ tests/` only (no `ruff format --check`; the `fmt` job is `terraform fmt`), so a pre-existing `test_harness.py` format discrepancy is not CI-gated. Two runners-up unfiled: `query()` shares one RNG across ingest + every query (per-query output depends on prior query count), and the docstring's "ef_search ≥ n_vectors → recall 1.0" exactness claim isn't enforced for sparse graphs.

**Next session:** consider the shared-RNG-per-query determinism issue (a query's result shouldn't depend on how many queries ran before it).

## 2026-06-27 — Issue #63: weaviate adapter fabricated a perfect similarity for missing metadata
**Duration:** ~20 min · **Branch:** `session/2026-06-27-0452-issue-63`

- `WeaviateBackend.query` used `distance = obj.metadata.distance if obj.metadata is not None else 0.0`, so a result object with absent distance metadata produced `similarity = 1.0 - 0.0 = 1.0` — the best-possible score, silently ranking a metadata-less result as the top hit. It was the only score-fabrication path among the four backends and violates the repo's no-fabricated-numbers invariant (handoff §10). Low severity (both consumers use only the returned order and discard the score, so recall is unaffected), and the adapter was entirely untested.
- Fixed by raising `BackendError` when `obj.metadata is None or obj.metadata.distance is None` — fail loud instead of inventing a score. Added the first `tests/test_weaviate_backend.py` (fake client injected via `object.__new__`): happy-path distance→similarity + order, and both missing-metadata paths raising. Suite 314 → 317, ruff clean.

**Why this work, this session:** sixteenth issue of a multi-issue NIGHT run; a second-pass dogfood hardening fix aligning the weaviate adapter with the other three backends' fail-loud posture.

**Open questions / blockers:** none.

**Next session:** all four backends now refuse to fabricate a score; a missing/None `orig_id` guard remains out of scope.

## 2026-06-27 — Issue #65: hnsw-sim per-query recall depended on issue order
**Duration:** ~25 min · **Branch:** `session/2026-06-27-1540-issue-65`

- `HnswSimBackend.query` drew beam-search entry points from the shared, stateful `self._rng` (built in `__post_init__`, advanced by `ingest` and every prior query), so a query's entry points — and its recall/latency — depended on how many queries ran before it. That contradicts the module's "pinned so the grid is reproducible" contract and makes the threaded `run_under_load` path racy. On a 300-vec/24-dim workload the same query returned a different top-10 (recall 0.9 vs 1.0) purely based on call order.
- Fixed by deriving a per-query RNG from `(seed, query-vector bytes)` via `SeedSequence`; `ingest` keeps using `self._rng`. Added an order-independence test on a workload that actually manifests the bug, a root-cause guard that `query()` doesn't advance `self._rng`, and a same-seed cross-backend agreement test.

**Why this work, this session:** fourth find of a multi-issue DAY run; the previously-unfiled shared-RNG runner-up flagged in earlier memory, now reproduced and fixed.

**Open questions / blockers:** none. A gotcha worth remembering: the first order-test used `tiny_workload` and passed on the buggy code too (beam converged regardless of entry points) — the negative check caught it; switched to a larger discriminating workload + a state-based root-cause guard.

**Next session:** all backends fail-loud and reproducible; consider whether `run_under_load` warrants its own determinism test now that the per-query RNG removes the shared-state race.

## 2026-06-29 — Issue #67: stale cost-artifact doc refs + README test count
**Duration:** ~13 min · **Branch:** `session/2026-06-29-0347-stale-doc-refs`

- `io_utils.py` and `test_io_utils_atomic_write.py` named `docs/cost.md` and the README's "Cost analysis" section, but `scripts/cost_table.py` writes `docs/cost_per_query.md` and the README heading is "Cost per query (#5)" — there is no `docs/cost.md` and no "Cost analysis" section. The test's scratch `--out` path echoed the wrong name too. Separately, the README claimed "23 hermetic tests" while the suite is 320.
- Corrected the filename/section references in code + test, and replaced the README's hard-coded count with a count-agnostic comment (no count-lock test exists to keep an exact number honest). MEMORY history's historical mention left as-is (append-only).

**Why this work, this session:** sixth issue of the night run. A parallel audit subagent swept this repo, found no logic bug (mature, 320 tests; ANN/recall/percentile/cost code verified correct), and surfaced these stale doc references — same doc-drift class as python-async #68, rag #100, emb-shootout #71.

**Open questions / blockers:** none.

**Next session:** code/test/README cost-artifact references now match the shipped `docs/cost_per_query.md`.

## 2026-06-30 — Issue #69: WeaviateBackend.query returned (None, score) on an object missing orig_id (symmetric to #63)
**Duration:** ~15 min · **Branch:** `session/2026-06-30-1605-issue-69`

- `WeaviateBackend.query` read the result id with `obj.properties.get("orig_id")` and appended `(orig_id, similarity)`. A missing `orig_id` property yields `None`, so the method returned `[(None, score), ...]` — violating its `list[tuple[str, float]]` contract and silently deflating recall (a None id never matches a ground-truth id). This is the symmetric gap to #63, which hardened the *distance* side of this exact loop but left the *id* side reading straight through. Confirmed by reading the code (not blindly trusting the hunter).
- Fixed with an `isinstance(str)` guard before the distance check (so its error message always references a real id), raising `BackendError`. +2 tests (missing-orig_id raises — fails pre-fix; well-formed unchanged). Suite 320 → 322, ruff clean. Filed priority:low — reachability is lower than #63 (needs out-of-band Weaviate data), but the contract and #63's precedent make the symmetric guard belong here.

**Why this work, this session:** seventh issue of a DAY multi-issue run, third non-tier repo (per build sequence). Zero open issues → dogfood-and-file. I reviewed cost.py/prices.py myself (both exhaustively guarded — finiteness/sign guards throughout, nothing to fix); an Explore hunter searched cli/harness/load/backends exhaustively (76 tool-uses) and surfaced only this one candidate — a strong signal the repo is well-hardened (320 tests, cost model verified correct).

**Open questions / blockers:** none — ready for review.

**Next session:** vector-search-at-scale is mature; the weaviate id-axis guard now matches the #63 distance-axis posture.

## 2026-07-02 — Issue #72: stub oracle silently violated its recall==1.0 invariant (~30 min)

**What got done.** The stub backend is documented as the ground-truth oracle that achieves recall@k=1.0 "by construction," but `ground_truth_topk` scored with a batched GEMM (`queries @ corpus.T`) while `StubBackend.query` uses a per-row GEMV (`vectors @ vector`). float32 GEMM and GEMV sum in different orders (~9e-8 apart), so at a top-k-boundary near-tie they order neighbors differently and the oracle silently missed one of its own true neighbors — reporting recall 0.998 and polluting the baseline every real backend is scored against. Fixed by scoring `ground_truth_topk` per-query with the identical GEMV expression the stub uses; verified bit-identical via `np.array_equal`, so recall is now exactly 1.0 by construction (0 mismatches over 25k queries). Tightened the two stub-recall assertions from `pytest.approx(1.0)` to exact `== 1.0` and added a regression test on the triggering workload (fails pre-fix at 0.998, passes post-fix). Full suite green (323 passed), ruff clean.

**Why prioritized.** Third issue of the day run. After the priority tier was exhausted (blocked issues + two clean bug-hunts + zero coverage gaps), I rotated to non-tier repos per D-009 and ran two more dogfood hunts; python-async-llm-pipelines came up clean, vector-search-at-scale surfaced this. Filed priority:high because a non-deterministic oracle corrupts the harness's reference baseline. Reproduced firsthand before filing and fixing per the saturation memory guidance; no decision needed (reversible, invariant stated absolutely).

**Open questions / blockers.** None. Broader float32 tie-robustness for *real* backends is a separate concern, out of scope here.

## 2026-07-03 — Issue #74: symbol-resolution doc-lock (propagates portfolio-ops #55) (~25 min)

**What got done.** Added a fifth invariant to `tests/test_architecture_doc.py`: every `<submodule>.<symbol>` ref and multi-word CamelCase public type named in `docs/architecture.md` must resolve against the `vector_bench` package (`src/` layout), its submodules, the `backends` subpackage, or the Python builtins. Closes the drift class portfolio-ops #55 catalogued. The doc names 5 package types (`BenchmarkResult`, `LatencyStats`, `LoadCell`, `LoadMatrix`, `PriceTable`) plus `ThreadPoolExecutor` — which resolves cleanly because `vector_bench.load` imports it for the concurrency layer, so no external-symbol exemption was needed (unlike llm-cost-optimizer's `StrategyResult`) — and the builtin `KeyboardInterrupt`. Single-word capitalized and no-boundary tokens (`Backend`, `Workload`, `Makefile`) are excluded to avoid prose false positives. Baked in an inverse-safety-net test plus hard-pins for the skip-extension and subpackage sets. Suite +4 tests, ruff clean.

**Why prioritized.** Third worked issue of the DAY run, continuing #55 propagation after chunking-strategies-lab #104 and prompt-regression-suite #103. vector-search-at-scale (build-seq #7) was the next Python gap repo lacking the lock.

**Open questions / blockers.** None — ready for review.

**Next in this session's loop:** python-async-llm-pipelines is the last Python gap repo (build-seq #8); after it the Python side of #55 is fully propagated.

## 2026-07-05 — Issue #76: escape pipe in cost_table throughput-source cell (~15 min)

**What got done.** `render_markdown()` in `scripts/cost_table.py` interpolated the per-tier throughput-source string straight into a GFM table cell. That string carries the operator-supplied `--load-results TIER=PATH` directory, so a `|` in the path corrupted the table — GFM splits cells on unescaped pipes before it parses inline-code spans, so backticks don't protect it, and a piped path added a spurious column (header declared 9 columns, the data row parsed to 10). Fixed by escaping `|` -> `\|` in that one free-form cell (every other cell is a controlled tier/engine/instance value or a formatted number). Added a regression test mirroring chunking-strategies-lab #100: a piped source produces a data row whose unescaped-pipe count equals the header's, and the literal pipe survives (escaped, not dropped). Full suite green, ruff check + format clean. PR #77.

**Why prioritized.** Night-session run in a saturated portfolio (nearly all repos have zero open actionable issues). Ran parallel dogfood bug-hunt agents across the not-yet-saturated repos; the vector-search-at-scale hunt surfaced this, the same recurring GFM pipe-escaping class already closed in five sibling repos. Reproduced firsthand before filing and fixing per the saturation memory guidance. Three other hunt findings were rejected on inspection: the mcp-server-cookbook sqlGuard underscore-boundary is a deliberate design (so `last_vacuum`/`last_analyze` columns stay queryable, and `_pg_sleep` isn't an executable function); the prompt-regression-suite `len(w) > 3` hint filter is an intentional stopword guard; python-async-llm-pipelines came up clean.

**Open questions / blockers.** None — ready for review.

## 2026-07-09 — Issue #79: qdrant (id, score) contract guards (weaviate #70/#64 sibling) (~25 min)

**What got done.** `QdrantBackend.query` mapped results with `[(r.payload["orig_id"], float(r.score)) ...]` and no contract guard. Because `r.payload["orig_id"]` only raises on a truly *absent* key, a present-but-`None` / non-string payload value silently produced a `(None, score)` / `(123, score)` hit — the exact silent contract violation (deflated recall, no diagnostic) that `WeaviateBackend.query` got guards for in #69/#70 (`orig_id`) and #63/#64 (metric). A `None` score raised a bare `float(None)` `TypeError` instead of a `BackendError`. Added the sibling guards (reject non-string `orig_id` and `None` score with a `BackendError` naming the point; `orig_id` first so its message references a real id), left `pgvector` unchanged (provably exempt: `id TEXT PRIMARY KEY` + computed similarity are never NULL), and added a fake-client test file mirroring `test_weaviate_backend.py`. Four guard tests fail pre-fix; full suite green, ruff clean.

**Why prioritized.** Second worked issue of the DAY run. No priority-tier repo had `priority:high` issues; lco (both open issues JT-gated `decision-revisit`) and vsas's own #71/#78 (both JT-gated) fell through, so I hunted a fresh bug in vsas — the stalest actionable repo (~4.5 days) — via the sibling-incomplete-fix / backend-parity lens. The GFM pipe-escape lens (#77) was checked first and found exhausted (`scale_tier` is strictly validated against `SCALE_TIERS`; `qps_source` was the only free-form cell and is already escaped).

**Open questions / blockers.** None — PR #80 ready for review.

**Next session:** backend result-mapping contract parity is now swept across all three real backends (weaviate #69/#70/#63/#64, qdrant #79, pgvector schema-exempt). Don't re-sweep this class.

## 2026-07-10 — Issue #81: harden vector-bench load concurrency-level validation (~20 min, night)

**What got done.** Two related defects in `vector-bench load`, both reachable from the shipped CLI. (1) **Duplicate concurrency levels silently clobbered per-cell JSON.** `run_under_load` validated each level is `> 0` but not that levels are distinct, and the per-cell dump keys each file on `c{concurrency:03d}.json` — so two cells sharing a concurrency both wrote the same filename (last-writer-wins), losing a measured cell while the command exited 0. The on-disk files no longer round-tripped the in-memory `LoadMatrix`, violating the D-007 "one JSON per run cell, no silent clobber" idempotency contract. (2) **Non-positive concurrency raw-tracebacked at exit 1.** `_do_load` caught the `ValueError` from *parsing* `--concurrency` but not the one `run_under_load` raises for `c <= 0`, so `--concurrency 0,1` escaped as a raw traceback at exit 1.

Fix: a distinctness guard in `run_under_load` (raises `ValueError`, parity with the `c > 0` guard) plus wrapping the `run_under_load` call in `_do_load` with `except ValueError -> exit 2`, which covers both the new distinctness error and the pre-existing `c <= 0` case. Four tests (library rejects duplicates + writes nothing; CLI duplicate/non-positive → exit 2 with no traceback and no clobber; valid distinct levels still run). Full suite green (338 passed); ruff clean. Reproduced firsthand before/after.

**Why prioritized.** Static priority:high queue globally exhausted; found via the sibling-incomplete-fix meta-lens (load-matrix idempotency + exit-code contract). Notably, the hunt agent itself flagged this as borderline degenerate-input, but firsthand verification showed it's silent data loss at exit 0 (a D-007 contract violation, not the degenerate-input-crash pattern the memory cautions about) and *also* surfaced the uncaught non-positive exit-1 traceback — a second clear exit-code gap — so it cleared the quality bar. (vsas #71 HNSW exact-recall and #78 knee staleness remain JT-gated; not touched.)

**Open questions / blockers.** None — PR #82 ready for review.

## 2026-07-10 — Issue #83: run-path exit-code contract (sibling of #81) (~15 min, night)

**What got done.** The `run` subcommand (`_do_run`) had no exception handling around `Workload(...)` + `run_benchmark(...)`, so an invalid workload (`--n 0` → ValueError), a D-011 `--concurrency > 1` request (ValueError), or a run-id collision (FileExistsError) escaped as a raw traceback at exit 1 — breaking the 0/1/2 exit-code contract its `load` sibling now honors (#81). The asymmetry was even locked into `test_cli_run_rejects_duplicate_run_id`, which used `pytest.raises(FileExistsError)`.

Wrapped the calls in `_do_run` with `except (ValueError, FileExistsError) -> clean stderr + exit 2`, mirroring `_do_load`. Rewrote the duplicate-run-id test to assert `rc == 2` + no traceback, and added `--n 0` and `--concurrency 2` exit-2 tests. Full suite green; ruff clean. Reproduced firsthand before/after (all three → exit 2 clean; valid run still exit 0).

**Why prioritized.** Found via a *second-order* sibling hunt on this run's own just-shipped PRs — the meta-lesson "the PRs a run ships are the next hunt's freshest surface" applied within the same run. #81 fixed the load-side; this closes the run-side, so both CLI subcommands now honor the exit-code contract. (#81 and #83 both touch `cli.py` but different functions — `_do_load` vs `_do_run` — so a next-Phase-A serial merge/rebase is trivial.)

**Open questions / blockers.** None — PR ready for review.

## 2026-07-12 — Issue #85: plot_latency missing matrix.json exits 2, not a traceback (~15 min, night)

**What got done.** `scripts/plot_latency.py:main` loaded each operator-supplied `matrix.json` via `_load_matrix → json.loads(path.read_text())` with no `is_file()` pre-check, so a missing path (the documented input `results/load/<id>/matrix.json`, README:145 + docs/architecture.md) escaped as a raw `FileNotFoundError` traceback at **exit 1** — where the sibling plot script `scripts/plot_hnsw_frontier.py:155-157` translates the identical missing-input case to a clean stderr `"<path> not found"` + **exit 2** (#83/#84 exit-code contract).

Pre-check every `args.matrices` path with `is_file()`, report each missing one to stderr, and `return 2`. Scope is the missing-file case only — the sibling doesn't guard malformed JSON either, so that variant isn't a sibling divergence (kept the fix honest). 2 tests (single missing path → rc 2 + stderr names it; nargs="+" reports every missing path). Full suite 342, ruff check/format green (no mypy gate in vsas CI). Reproduced firsthand before/after.

**Why prioritized.** Exit-code contract parity — the last uncovered CLI surface after #84/#83/#82 closed the run/load paths. Found via a targeted exit-code/parity hunt (verified firsthand). Backend-parity lens came up empty (the (id,score) contract is genuinely scoped to weaviate+qdrant, both already enforcing it per #79). Not JT-gated #71/#78.

**Open questions / blockers.** None — PR #86 ready.
## 2026-07-12 — Issue #87: cost_table missing tf-main/results input exits 2, not a traceback (~12 min, night)

**What got done.** `cost_table.py:main` translated one operator-input error to a clean exit 2 (`_parse_load_results_overrides`, lines 383-387) but left two sibling file reads bare: `Path(args.tf_main).read_text()` (line 389) and `load_throughput_qps(...)` (lines 398/402). A missing `--tf-main` or `results/load/<run-id>/c001.json` escaped as a raw `FileNotFoundError` traceback at **exit 1** — burying `load_throughput_qps`'s carefully-worded operator message ("expected ... to exist for the cost table. Re-run the load harness ..."). Wrapped the file-dependent section in `except FileNotFoundError → print(stderr) + return 2`, mirroring the existing guard; the descriptive messages now reach the operator with exit 2. 2 tests. Full suite 342, ruff green. Reproduced both cases firsthand (before: traceback/exit 1; after: clean message/exit 2).

**Why prioritized.** A **second-order sibling hunt** on this run's own #85 (`plot_latency`) fix — the same exit-code contract vein (#83/#84/#85) in a different script with a richer gap (two read surfaces vs one). Notably, the earlier vsas hunt agent had *wrongly* asserted `cost_table.py:384-387` "catches and returns 2"; firsthand verification caught that false negative (the catch only wraps `_parse_load_results_overrides`, not the tf_main/results reads) — the memory lesson to verify firsthand paid off. Not JT-gated #71/#78.

**Open questions / blockers.** None — PR #88 ready.

## 2026-07-12 — Issue #89: architecture.md documents wrong load flag (--clients vs --concurrency)
**Duration:** ~13 min · **Branch:** `session/2026-07-12-0949-issue-89`

- `docs/architecture.md` documented `vector-bench load --clients 1,10,100`, but the CLI flag is `--concurrency` (the token `--clients` appears nowhere else; the README uses `--concurrency`). An operator copy-pasting the doc command hit `error: unrecognized arguments: --clients`. Fixed the doc and added a lock test tying the documented load flag to the CLI source so it can't drift again.

**Why this work, this session:** Sixth hit of the run — the served-output/doc-drift lens applied to architecture.md CLI commands; a genuinely broken copy-pasteable command.

**Open questions / blockers:** none — ready for review.

**Next session:** Phase A merge PR for #89.

## 2026-07-13 (Night) — Issue #91: architecture named 'weaviate-oss' but the module dir is 'weaviate'
**Duration:** ~15 min · **Branch:** `session/2026-07-13-0559-issue-91` · **PR:** #92

- `docs/architecture.md` Layer 1 named the Weaviate Terraform module `weaviate-oss`, but the directory is `terraform/modules/weaviate` and `main.tf` declares `module "weaviate"`. The name was bare prose (not a backtick path), so `test_backtick_paths_resolve_on_disk` never checked it — "arch-doc drift beyond the lock lens".
- Corrected to `weaviate` and added a code-tied lock parsing the "backend modules under `terraform/modules/`: …" sentence, asserting every named module resolves to a real `terraform/modules/<name>` directory, with an inverse injected-drift guard. Verified it flags exactly `weaviate-oss` pre-fix. Full suite 347 pass; ruff clean.

**Why this work, this session:** parallel Explore agent flagged it; verified firsthand. vsas's two open issues (#71/#78) are JT-gated decision-revisits — untouched.

**Open questions / blockers:** none — ready for review.

**Next session:** the vsas arch doc's other count/field claims (Workload/LatencyStats/BenchmarkResult/LoadCell/LoadMatrix to_dict field counts, atomic_write_text 5 call sites, 3-layer/3-backend/port claims) were all audited and are accurate — no further vsas doc-drift.

## Session 2026-07-13 (night) — issue #93: `load` exits 2 on run-id collision, not a raw traceback

`vector-bench load` caught only `ValueError` from `run_under_load`, so the `FileExistsError` it raises on a run-id collision (a `matrix.json` already present under `<results-dir>/<run-id>/` without `--force`) escaped as a raw traceback at exit 1. The sibling `vector-bench run` catches `(ValueError, FileExistsError)` and exits 2 for the identical collision — an asymmetric catch tuple between two CLI handlers that call into the same-raising helpers. Sibling-incomplete-fix of the #81/#83 exit-code-contract work; the existing `run` collision test even comments that it mirrors "the `load` sibling #81", though `load` never actually handled it and had no test for it.

The fix adds a separate `except FileExistsError` clause to `_do_load` printing `error: {e}` + exit 2 (matching `_do_run`), kept apart from the `--concurrency invalid:`-prefixed `ValueError` clause whose prefix would misdescribe a collision. Added a lock test mirroring the `run` sibling. A subtlety: the `_load_args` test helper always appends `--force` (which overwrites rather than colliding), so the test filters it out to exercise the collision path — caught by reproducing firsthand before writing the test. Full suite green, ruff clean.

**Why this work, this session:** Fourth hit of the night run, surfaced by the sibling-incomplete-fix dogfood hunt on vector-search-at-scale and verified firsthand.

**Open questions / blockers:** none — PR #94 ready for review.

**Next session:** Phase A merge PR for #93.

## 2026-07-13 (night) — Issue #95: unreadable input file exits 2, not a traceback (3 scripts)
**Duration:** ~25 min · **Branch:** `session/2026-07-13-1126-issue-95` · **PR:** #96

`cost_table.py`, `plot_latency.py`, and `plot_hnsw_frontier.py` translated a *missing* input file to a clean exit 2 (the #83/#84/#85 exit-code contract) but leaked a raw traceback at exit 1 on a *present-but-unreadable* file. A corrupt file passes the `is_file()`/`FileNotFoundError` pre-check, then `read_text(encoding="utf-8")`/`json.loads(...)` raises `UnicodeDecodeError` (a `ValueError` subclass) or `json.JSONDecodeError` — neither a `FileNotFoundError` — so it escaped the guard. Added `UnicodeDecodeError` handling to cost_table's guard (its operator-supplied `--tf-main` is Terraform text, not JSON; the JSON `c001.json` is harness-generated) and `UnicodeDecodeError` + `json.JSONDecodeError` handling to the two plot scripts (whose operator-supplied file *is* the JSON, so both bad-content modes are in scope). This is the direct cross-repo sibling of the same fix in llm-eval-harness #174, prompt-regression-suite #126, embedding-model-shootout #102, and chunking-strategies-lab #124. Five lock tests; full suite green, ruff clean.

**Why this work, this session:** Third hit of the night run, surfaced by a sibling-incomplete-fix hunt on the cross-repo bad-input-file exit-code lens and verified firsthand (all five repro commands) before and after.

**Open questions / blockers:** none — PR #96 ready for review.

**Next session:** Phase A merge PR for #95.

## 2026-07-14 (night) — Issue #97: plot/cost-table scripts leak a raw OSError on an unwritable output path
**Duration:** ~30 min · **Branch:** `session/2026-07-14-0532-issue-97` · **PR:** #98

`cost_table.py`, `plot_latency.py`, and `plot_hnsw_frontier.py` all guard their input-read seam per the #83/#84 exit-code contract (#96 added non-UTF-8 / malformed-JSON → exit 2), but each left its output-write seam unguarded. A bad `--out`/`--out-dir`/`--out-png` (read-only filesystem, permission denied, or a path component that is a file → `NotADirectoryError`) made `atomic_write_text`/`mkdir`/`savefig` raise `OSError`, which escaped as a raw traceback at exit 1 *after* the table already printed. Fixed by wrapping each output-write call in `try/except OSError` → clean stderr + `return 2`, mirroring the input guards and the write-seam sibling in llm-eval-harness #158/#159 and python-async-llm-pipelines #84. Verified firsthand (pre-fix `cost_table --out <file>/sub/cost.md` raised `NotADirectoryError`, exit 1). Three new lock tests (one per script, matplotlib-guarded for the plot pair) plus the existing cost_table atomic-helper routing test updated from "OSError propagates" to the exit-2 contract; the harness/library routing tests were left unchanged (a library fn has no CLI exit-code contract). Full suite (358) green, ruff clean.

**Why this work, this session:** Fourth hit of the night run — a firsthand cross-repo write-seam sweep after pyasync #84, grepping the output writers across ems/vsas/chunking. ems already guards its seam (cli.py); vsas's three scripts — the exact ones #96 hardened on input — leaked on output.

**Open questions / blockers:** none — PR #98 ready for review.

**Next session:** Phase A merge PR for #97.

## 2026-07-14 (night) — Issue #99: hnsw_grid.py was the 4th operator-facing writer #98 left unguarded
**Duration:** ~15 min · **Branch:** `session/2026-07-14-0727-issue-99` · **PR:** #100

`docs/architecture.md` groups four operator-facing writers as routing through `atomic_write_text` (`load.py`, `harness.py`, `hnsw_grid.py`, `cost_table.py`). #98 added an `OSError → clean exit 2` write-seam guard to the three *script* writers (`cost_table.py`, `plot_latency.py`, `plot_hnsw_frontier.py`) but left `hnsw_grid.py` out — so its `run_grid` writes (`out_dir.mkdir`, per-cell result JSON, `grid.json`) leaked a raw `NotADirectoryError` traceback at exit 1 on an unwritable `--out-dir`, reachable from the documented README reproduce command. Verified firsthand: the same unwritable-target scenario yields exit 1 + raw traceback on `hnsw_grid` vs a clean exit 2 message on `cost_table`/`plot_latency`.

Fixed by wrapping the `run_grid` call in `main()` with `try/except OSError` → stderr + `return 2`, mirroring `cost_table.py`'s guard (`run_grid`'s computation is pure; only the output writes raise `OSError`). One lock test; full suite (359) green, ruff clean.

**Why this work, this session:** Fourth hit of the night run. This one sat right at the churn line — like `chunking-strategies-lab`'s `run_matrix.py` (which I *dismissed* as churn this same run), `hnsw_grid.py` documents no exit-code contract of its own. I shipped it because it has a stronger consistency anchor that `run_matrix` lacks: all three of its direct sibling scripts got the guard in the single #98 commit, `architecture.md` explicitly groups it as a *peer* operator-facing writer, and it's reachable from the documented README reproduce command. Filed priority:low as an honest write-seam parity completion. The vsas operator-facing-writer family is now fully closed.

**Open questions / blockers:** none — PR #100 ready for review.

**Next session:** Phase A merge PR for #99.

## 2026-07-14 (night) — Issue #101: vector-bench run/load leaked a raw OSError on an unwritable --results-dir
**Duration:** ~15 min · **Branch:** `session/2026-07-14-0746-issue-101` · **PR:** #102

`vector-bench run` (`_do_run`) caught `(ValueError, FileExistsError)` and `load` (`_do_load`) caught `ValueError` + `FileExistsError`, translating those to a clean exit 2 per the 0/1/2 contract. But both write output through `atomic_write_text`, and a general `OSError` (unwritable `--results-dir`: `NotADirectoryError`/`PermissionError`/`ENOSPC`/`EROFS`) was not caught — it escaped as a raw traceback at exit 1. The two script writers (#98) and hnsw_grid (#99) were guarded, but the CLI's own `run`/`load` writer subcommands kept only the `FileExistsError` subset. Verified firsthand.

Fixed by widening `_do_run` to `(ValueError, OSError)` and `_do_load`'s `FileExistsError` clause to `OSError` — `FileExistsError` (collision) stays caught (subclass), write-seam `OSError`s now land as a clean exit 2. Two lock tests; full suite (360) green, ruff clean.

**Why this work, this session:** Ninth hit of the night run, surfaced by a **second-order sibling hunt on this run's own #99/#100 hnsw_grid fix** — a per-script write-seam sweep missed the CLI subcommand that wraps the same `run_benchmark` write. (2nd vsas PR this run; MEMORY append conflict with #100 resolved by rebase at merge.)

**Open questions / blockers:** none — PR #102 ready for review.

**Next session:** Phase A merge PR for #101 (rebase after #100).

## 2026-07-14 (night) — Issue #103: atomic_write_text overflows NAME_MAX on a long --run-id basename
**Duration:** ~15 min · **Branch:** `session/2026-07-14-0914-issue-103` · **PR:** #104

`atomic_write_text` built its temp file name as `.<target.name>.<random>.tmp` with no cap on the basename, so a destination basename near `NAME_MAX` (255 bytes) overflowed the limit and raised `OSError` ENAMETOOLONG — even though a plain `write_text` of that same path succeeds. Reachable from a long operator `--run-id` (`vector-bench run` writes `results/<run_id>.json`; `load` writes under `results/load/<run_id>/`). Verified firsthand: a 250-char basename that `write_text` accepts failed via `atomic_write_text`.

This is the identical bug already fixed in `rag#128`, `mcp#96`, and the 2026-07-14 cross-repo sweep (`leh#175`, `prs#127`, `pyasync#86`, `ems#103`, `chunking#128`, `lco#154`). vsas was the **last** Python repo carrying the pre-fix helper — it received the write-seam exit-2 fixes (#99/#101) this run but not the NAME_MAX cap. Fixed by porting the sibling `_cap_base_for_temp` 200-byte cap (trimming on a char boundary since NAME_MAX is a byte limit). Two regression tests; full suite (363) green, ruff clean.

**Why this work, this session:** Surfaced by a second-order sibling hunt on this run's own Phase A merges — the NAME_MAX sweep landed in 6 repos this run but skipped vsas, which got a different write-seam fix the same run. The cross-repo `atomic_write_text` NAME_MAX vein is now fully closed across all 9 Python repos.

**Open questions / blockers:** none — PR #104 ready for review.

**Next session:** Phase A merge PR for #103.

## 2026-07-14 (night, issue #105) — load subcommand raw-tracebacks on invalid workload (sibling of #83)

`_do_load` built its `Workload(...)` outside the try/except that wraps `run_under_load`, so a `Workload.__post_init__` `ValueError` (`--top-k` > `--n`, `--n 0`) escaped as a raw traceback at exit 1 — while the sibling `run` subcommand (`_do_run`) wraps the identical construction and lands the same ValueError as a clean exit 2. The #83 exit-code guard on `run` was never propagated to `load`.

Fix: wrap `load`'s `Workload(...)` in a `try/except ValueError` → `error: {e}` / exit 2, mirroring `_do_run`, kept separate from the `run_under_load` `--concurrency invalid:` clause so a dimension error isn't mislabeled. Verified firsthand: pre-fix `load --n 5 --top-k 20` tracebacked at exit 1 while `run` exited 2; post-fix both exit 2, happy path unchanged. Added a parametrized regression test mirroring the `run` test.

The lens: when an exit-code guard lands on one subcommand, check the sibling subcommands for the same construction placed outside the try — `_do_run` wrapped `Workload`, `_do_load` didn't. Found by the agent hunt (avoiding the two gated areas #71/#78), verified firsthand. Full suite green, ruff clean. Shipped as PR #106.

## 2026-07-17 — Issue #107: plot_latency deserialization exit-2 gap

`scripts/plot_latency.py` translated a matrix file's decode failures
(non-UTF-8, invalid JSON) into a clean exit 2, but the next layer —
`_load_matrix`'s int/float coercion, `LatencyStats(**...)` construction (whose
`__post_init__` runs `math.isfinite` per field), and required-key indexing —
raised TypeError/ValueError/KeyError that the loop didn't catch, so a valid-JSON
but malformed matrix (e.g. `p50_ms: "fast"`) leaked a raw traceback at exit 1. I
reproduced it firsthand, then broadened the except clause to translate those to a
clean "not a valid load matrix" exit 2, matching the load subcommand. Shipped as
PR #108.

## 2026-07-20 (night) — issue #109: boolean numeric fabricated a benchmark value

The `plot_latency.py` `matrix.json` loader — hardened by the just-merged #108 for
structural/decode errors — still silently coerced a boolean numeric into a
fabricated benchmark value. `float(True)` is `1.0`, so a JSON `true` at
`mean_recall_at_k`/`throughput_qps`/`ingest_seconds` became a fake perfect number
before `LoadCell`'s finiteness guards ran, and a boolean nested latency field
reached `LatencyStats(**...)` raw where `math.isfinite(True)` is `True`. Rejected
bool before coercion in `_load_matrix` (→ the #108 exit-2 handler) and added the
bool exclusion to `LatencyStats.__post_init__`. Five tests. Found via a
second-order sibling hunt on #108 combined with the ems bool-before-coercion vein.
PR #110.

_Addendum (same PR #110):_ the same boolean-coercion class was found in a second
external-JSON loader in this repo — `cost_table.py`'s `load_throughput_qps` did
`float(payload["throughput_qps"])`, fabricating `1.0` qps from a JSON `true`.
Folded into #109/#110 (guard before coercion → exit-2) rather than a separate
near-duplicate same-repo PR. Both matrix.json and c001.json loaders now reject
boolean numerics.

## 2026-07-21 — plot_hnsw_frontier numeric cell guard (#111, PR #112)

`scripts/plot_hnsw_frontier.py` is the *other* plotter alongside `plot_latency.py`,
which was hardened in #109/#110 to reject boolean/non-finite numerics in
`matrix.json`. But `plot_hnsw_frontier` reads `grid.json` cells as plain dicts and
uses their numeric fields (`mean_recall_at_k`, `p50_ms`, `p95_ms`) directly — in
the Pareto-frontier computation, the recommended-defaults knee, the scatter plot,
and the published markdown table — with no value guard. Verified firsthand against
the committed `results/hnsw-grid/grid.json`: a JSON `true` at a cell field became a
fabricated `recall=1.000 p95=1.00ms` row and was even selected as the recommended
knee (exit 0, silent — a §10 no-fabricated-benchmarks violation); a non-numeric
string raised a raw `TypeError` in `_dominates` (exit 1, breaking the file's own
exit-2 contract); a NaN token rendered `nan`. Added `_require_finite_number` +
`_validate_cells` at the load boundary in `main()`, routing the error to exit 2 and
also turning the pre-existing missing-field `KeyError` into the same clean exit.
Nine tests. Found via the sibling-incomplete-fix lens on this run's just-merged #110.

## 2026-08-03 — Issue #113: three tests that no install could ever run

Three tests guard on `pytest.importorskip("matplotlib")`, and matplotlib was
declared nowhere in `pyproject.toml` — not in `dependencies`, not in any extra.
So there was no documented install of this repo under which they could run.
They skipped in CI, they skipped for anyone following the README, and the
`python` job reported green through all of it. This is strictly worse than the
sibling case in llm-cost-optimizer#170, where the extra existed and CI simply
did not install it.

What was going unprotected is not marginal. `scripts/plot_latency.py` and
`scripts/plot_hnsw_frontier.py` are both documented with quickstart commands,
and the README calls the committed frontier plot "real". Two of the three tests
exist specifically to cover the write seam — their own comments say so — which
is the same seam class as #107, a bug that was real in that very file.

The part worth carrying forward is the discrimination. The identical skip
pattern is *deliberate* in two sibling repos: chunking-strategies-lab's D-009
puts matplotlib behind a `[notebook]` extra and explicitly rejects "matplotlib
in base install breaks dep-free default", and embedding-model-shootout has the
equivalent decision. Filing a CI-install fix against those would have walked
straight into a non-superseded decision. vector-search-at-scale has no such
decision and did not offer the extra at all, and its base dependencies already
carry numpy, so the dep-free-base rationale does not transfer either. Check
`core_decisions_ai.md` before treating a skipping test as a bug; the two
siblings went to JT as decision-revisits instead.

The lock is derived from the suite rather than hardcoding matplotlib, so the
next `importorskip` cannot repeat this quietly. It asserts two things in order
of sharpness: the guarded module must be provided by some declared extra (a
guard naming an undeclared module is a dead test, not a conditional one), and
CI must install an extra that provides it. There is an `_EXPECTED_DORMANT`
escape hatch so "this test is meant to stay dormant" has to be stated rather
than happen by accident. Both assertions were verified by reverting each change
in turn and reading the diagnostic.

Fresh 3.11 and 3.12 venvs: three skips to zero, full suite green. Shipped as
PR #114.

## 2026-08-05 — the cost-table guards were raising into nothing (#115)

`scripts/cost_table.py` reads two kinds of operator input: the Terraform file
that defines per-tier sizing, and one `c001.json` per tier carrying the measured
`throughput_qps`. `main()` wraps both in a `try` — and that `try` caught
`FileNotFoundError`, `UnicodeDecodeError` and `json.JSONDecodeError`. Nothing
else.

What makes that a bug rather than a gap is what the code inside the block does.
It deliberately raises the exceptions there is no arm for. `load_throughput_qps`
guards against a JSON `true` (because `bool` subclasses `int`, so `float(True)`
would amortize the whole table over a fabricated 1.0 qps), and the comment
introducing that guard says:

> `float()` already raises ValueError on a non-numeric string (caught by
> `main`'s exit-2 handler), but a bool coerces cleanly, so guard it explicitly

There was no such handler. The guard had been raising into nothing since it
landed, and so had `float()`'s own `ValueError`, `parse_terraform_tiers`'
tier-block errors, `float(None)`'s `TypeError`, the `KeyError` from an absent
field, and `cost_per_query`'s positive-and-finite refusal. Nine input shapes,
all verified firsthand, all exiting 1 with a raw traceback:

| input | escaped as |
| --- | --- |
| `{"throughput_qps": true}` | `ValueError: … not a bool` — the guard's own message |
| `{"throughput_qps": "abc"}` | `ValueError: could not convert string to float` |
| `{"throughput_qps": null}` | `TypeError: float() argument must be…` |
| `{"qps": 5}` | `KeyError: 'throughput_qps'` |
| `{"throughput_qps": 0}` | `ValueError: … positive and finite` |
| `{"throughput_qps": Infinity}` | same |
| `[1,2]` | `TypeError: list indices must be…` |
| `--tf-main` with no tier blocks | `ValueError: … missing tier blocks` |
| `--tf-main` with an incomplete tier | `ValueError: … missing one or more required fields` |

The repo has three scripts that load operator JSON and publish a benchmark
artifact, and they should agree on what a malformed input means. Two do:
`plot_latency.py` catches `(TypeError, ValueError, KeyError)` outright, and
`plot_hnsw_frontier.py` funnels every value-level problem through
`_validate_cells` into a `ValueError` it then catches. `cost_table.py` had the
write-seam `OSError` guard from #96 and the two decode arms, and nothing on the
value axis — the sole gap. It even had an `except ValueError → return 2` a few
lines earlier for `_parse_load_results_overrides`, so the contract was
established in the very same function; the load block just never got the arm.

Two details made the fix less mechanical than it looks. `UnicodeDecodeError`
and `json.JSONDecodeError` are both `ValueError` subclasses, so the new broad
arm has to sit *below* them or it swallows their specific messages. And
`cost_per_query`'s check doesn't fire in the load block at all — it fires from
`build_rows`, which runs after it — so the zero/NaN/Infinity cases needed their
own arm at that call site. The general lesson: when adding a catch arm, trace
where each guard actually *raises*, not where the value is read.

Messages now name which input failed, since `main` reads several paths and an
undifferentiated "invalid input" leaves the operator guessing.

Thirteen tests cover all nine shapes plus the happy path. Each asserts exit 2
**and** that stderr carries no `Traceback` — the exit code alone can't
distinguish "handled cleanly" from "handled, then dumped a stack anyway" — and
the unusable-qps cases additionally assert no file was published. The parity
lock is derived rather than restated: it walks both scripts' `ExceptHandler`
nodes with `ast` and asserts both carry the three types, so a future divergence
in either file fails here instead of relying on someone remembering.

Full suite 398 passed; ruff clean under 0.15.13 and 0.16.1.
## 2026-08-05 — the grid generator's guards had no catcher, and an empty axis published a zero-cell benchmark (#117)

`scripts/hnsw_grid.py::main` wrapped `run_grid(...)` in a `try` that caught
`OSError` only. But `run_grid` builds a `Workload` — which validates
`n_vectors`, `dim`, `n_queries` and `top_k` — and calls `make_backend`, and both
report a bad value by raising `ValueError`. So every degenerate grid dimension
escaped as a raw traceback at exit 1: `--n-vectors 0`, `--n-vectors -5`,
`--dim 0`, `--n-queries 0`, `--top-k 0`, `--top-k -3`, `--M 0`, `--ef-search 0`,
and `--backend bogus`.

This repo had already fixed that shape twice and left a comment describing it.
`cli._do_load`:

> `Workload.__post_init__` validates the dimensions (`--n 0`, `--top-k` > `--n`,
> etc.). This construction sat OUTSIDE the `run_under_load` try below, so a bad
> `--n`/`--top-k`/`--queries` leaked a raw traceback at exit 1 — while the
> sibling `run` subcommand (`_do_run`) wraps the identical `Workload(...)` and
> lands the same ValueError as a clean exit 2 (#83).

`hnsw_grid.py` is the fourth entry point that constructs a `Workload` plus a
backend, and the only one that never got that arm.

The tenth shape is worse than the nine tracebacks. `_parse_int_list` drops empty
segments, so `--M ''` parses to `[]` rather than raising. `itertools.product`
over an empty axis yields nothing, so the script wrote a **zero-cell
`grid.json`** and exited 0 — printing `wrote …/grid.json`, exactly as a
successful run does. A degenerate artifact published as a benchmark, and the
failure only surfaced later, in a different script, as `plot_hnsw_frontier`'s
`grid has no cells; nothing to plot`. `_do_load` already treats the analogous
case as an operator error (`--concurrency must contain at least one value`); the
three axis flags here had no such check. They do now, with the same wording,
running before any work.

Twenty-two tests. The empty-axis ones assert **no `grid.json` was written**
rather than only the exit code, because the regression being locked is precisely
that a file appeared — an exit-code assertion alone would have missed it. The
parity lock is AST-derived: it walks `cli.py` for any `Workload(...)` call not
inside a `ValueError`-catching `try`, and asserts `hnsw_grid`'s `main` carries
both arms, so a fifth entry point fails here rather than depending on someone
remembering.

Validation-only. No artifact is regenerated, and the recommended-defaults knee
(JT-gated, #78) and the exact-recall question (#71) are untouched.

Two process notes. This came from the same AST scan of every `main`/`cli`
function's exception arms that produced the `llm-cost-optimizer` fix earlier in
the run — one scan, two repos, two shipped fixes; worth re-running whenever a new
CLI lands. And three rounds of probing were wasted on a shell quirk: zsh does not
word-split unquoted parameter expansions, so a `$BASE` variable holding flags
arrived as a single argument and argparse answered `unrecognized arguments`,
which I briefly misread as a finding. Drive CLI probes from Python by calling
`main(argv)` directly.

Full suite 406 passed; ruff clean under 0.15.13 and 0.16.1.

**Merge note:** #116 and #118 are both open on this repo. They touch different
scripts, so there is no source conflict, but both append to these MEMORY files —
merge #116 first, then rebase #118.

## Session 2026-08-06 — the floor had no floor (#119)

`plot_hnsw_frontier.py` is careful. It guards its file input, its JSON,
every consumed numeric field of every grid cell, and both output paths —
each with a clean exit 2 and a comment explaining the harm it prevents.

It did not guard the one number all those cell values are *compared
against*. `--recall-floor` was a bare `type=float`.

```
$ python scripts/plot_hnsw_frontier.py results/hnsw-grid/grid.json --recall-floor -1
Recommended defaults (knee at recall ≥ -1.00): M=8 ef_construction=50 ef_search=16
  →  recall=0.102  p95=0.08ms
exit=0
```

A published **"Recommended default" with 10.2% recall**: a configuration
that finds the right neighbour one time in ten. A negative floor admits
every cell, so `min(p95)` picks the fastest and therefore the worst.
`-0.95` mistyped for `0.95` gets you there in silence.

The other end is different but no better:

```
--recall-floor nan  →  No grid cell achieves recall ≥ nan; expand the grid (higher ef_search)...
--recall-floor 5    →  No grid cell achieves recall ≥ 5.00; expand the grid (higher ef_search)...
```

Also exit 0. "Expand the grid" is advice the operator can act on — by
spending real benchmark compute — and no grid at any size can satisfy
either floor. Every comparison against `NaN` is False, and recall is a
proportion, so nothing clears 5.0.

The asymmetry in one sentence: on the same run, a `NaN` **in the grid
JSON** exits 2 because it "would render as `nan` in the published
table"; a `NaN` **on the command line** silently changed the published
recommendation and exited 0.

### Deriving the domain

`mean_recall_at_k` is a proportion, and the repo already says so twice:
`BenchmarkResult.__post_init__` enforces "a finite number in [0, 1]",
and `_coerce_number` re-checks finiteness on the read path. The guard
applies that same domain to the flag, and a lock test inspects both
sources so they can't drift apart.

It's checked first in `main`, before the file read, the cell validation
and the chart render — the flag costs nothing to check, so the operator
isn't made to wait to be told it's wrong.

Both endpoints stay accepted: `0.0` is an explicit "no floor", `1.0` a
legitimate "exact recall only" request. The default `0.95` path is
byte-identical, pinned by a test that diffs the default invocation
against the explicit one — #78 (the knee *value*) is a separate,
maintainer-gated question and this must not touch it.

### Why the portfolio sweep missed it

"`type=float` is not validation" was swept portfolio-wide once and
recorded llm-cost-optimizer as the sole gap. That sweep enumerated
library and validator entry points. `scripts/` was never visited.

That's the same miss shape as chunking-strategies-lab#149, filed the
same night: a contract hardened on `chunking_lab/validate.py` while the
flagship script never got it. **When a sweep reports "one gap", check
which entry points it enumerated.** Two of tonight's findings came from
re-running a sweep that had been declared exhausted, over the
directories it never looked at.

The rest of this repo's scripts were probed too — `cost_table.py` and
`hnsw_grid.py` were hardened this week, `plot_latency.py` came back
clean — so this was the last one.

### Two testing notes

`--recall-floor -inf` as a separate argument is rejected by *argparse
itself* ("expected one argument"), because a leading `-` reads as an
option prefix; it never reaches the guard. The tests use
`--recall-floor=-inf` so they actually exercise it.

And the negative-floor harm test first *measures*, through the untouched
library function, that a `-1` floor really does select a lower-recall
cell than `0.95` does — so the assertion that follows means something. My
first draft of it contained an `assert ... or True`, which is exactly the
kind of guard-that-doesn't-guard this PR is about. Caught and rewritten.

435 green, ruff clean. Anti-vacuous check: reverting only the guard block
fails 10 of the 13 new tests; the three that stay green are the in-domain
and default-path locks, true either way by design.

## 2026-08-07 — the chart writer threw away half the comparison and said it hadn't (#121)

`plot_latency.py` named each chart after the backend and the vector count.
It never used `run_id` — the field the whole results tree is keyed by, and
the only thing distinguishing two runs of the same backend at the same
scale. Pass it a baseline and a re-tune and both charts resolve to one path:
the second overwrites the first, stderr prints two "# wrote" lines for the
same file, and the process exits 0.

The tell was inside the script itself. The markdown table it prints rendered
both series correctly, six data columns, everything preserved. One half of
the same command kept the comparison the other half silently dropped. That
kind of internal asymmetry is usually worth chasing.

What turned this from a naming preference into a clear defect was reading the
repo's own decisions. D-007 says results are one JSON file per run_id and
that overwriting is refused without an explicit force flag, and its recorded
rationale is, word for word, a "clear failure mode when operator typos run_id
collision". `plot_latency` renders the very files that decision governs, and
it produced exactly the failure mode the decision exists to prevent. So the
fix isn't a new decision — it brings a downstream renderer into line with an
old one.

The bug hid the same way llm-cost-optimizer#176 did: the docstring's usage
example compares two *different* backends, which is the one input shape where
the names can't collide. The documented invocation was the one that masked
it. The docstring also claimed charts were keyed by backend and scale — which
described the code, but the code didn't honour that contract either. Under a
genuine backend-and-scale contract the two runs would be merged into a single
chart, the way the table merges them. Drawing two figures and discarding one
isn't a contract at all. A docstring describing current behaviour doesn't by
itself make that behaviour deliberate.

Charts are now keyed by run_id, and two inputs that would still collide are
rejected with exit 2 before anything is drawn and before the table prints —
so a rejected run leaves neither a half-written chart set nor a table that
reads as success. Re-running the same command still overwrites its own
output, which matters: D-007's force check is about the results JSON, and
breaking idempotent regeneration of derived plots would have been a worse bug
than the one being fixed.

The anti-vacuous check earned its keep, against my own work. One of the new
tests compared the reported paths to the files on disk as *sets* — and a set
quietly collapses the duplicate "# wrote" line that was the entire symptom,
so it passed on the unfixed tree. Comparing sorted lists instead took it from
passing to failing pre-fix. When the bug class is double-reporting, set
equality is structurally blind to it.

## 2026-08-12 — resolving a judgement call by precedent, not preference (#123)

`render_table` labelled its columns by backend alone, so two runs of one
backend produced six identically-named columns. No data was lost — both series
were there, in input order. What was lost was provenance, and the docstring
says the table is "designed to drop into a README", where input order stops
being visible.

The issue was unusually good: it laid out two defensible readings and declined
to pick. Either the contract is right and comparing two runs of one backend is
out of scope, or the contract is too narrow and the header should disambiguate.

What settled it wasn't preference. The repo had already answered "which run is
this?" twice, the same way — D-007 keys the results tree one JSON file per
`run_id`, and #121 keyed chart filenames by `run_id` for this exact
baseline-vs-re-tune case. Following that identity is different from inventing
one. (Rejecting duplicate backends, the other option, would also have
contradicted `plot_latency`'s `nargs="+"`, which explicitly takes arbitrary
matrices.) When an issue offers two readings, the useful question is where the
codebase already decided the same thing.

The conditional fallback is the entire safety argument: a backend appearing
once keeps its short label, so every existing call renders byte-identically.
The test that turns that from an observation into a guarantee re-renders the
committed README table and compares against the markdown actually in the file —
so a future widening of the condition fails loudly instead of quietly rewriting
published output.

One test shape worth reusing. When the bug is *ambiguity* rather than data
loss, write a test that pins the data is still all there. An implementation
that "resolved" this by dropping a duplicate column would have satisfied every
label assertion while making the table strictly worse.

## 2026-08-13 — Free-form column labels could break the rendered table (#125)

**Duration:** ~30 min · **Issue:** #125 · **PR:** #126

`render_table` builds its separator row from the *length of the header list*, while the header row itself is a rendered string. That asymmetry is the whole bug: a `|` inside a column label adds a rendered column the separator doesn't have, and the two rows desync. GitHub then draws a mangled grid rather than a table — the failure mode that survives review, because a reader sees a broken layout instead of a number they'd question. Measured on main: a pipe in `run_id` gives a 10-column header against a 7-column separator.

Both label shapes were exposed, and #124 — merged an hour earlier in this same run — is what doubled the free-form surface by adding `run_id` into the label. The hazard predated it, but that's where the second source came from.

The useful pattern here repeated something from earlier today: the correct idiom was already in the repo. `scripts/cost_table.py` escapes its one free-form cell exactly this way; `render_table` is just the site it never reached. That's the second time in this run that finding an unescaped or hand-rolled site and then grepping the same repo for the same job done properly turned up the fix sitting a file away.

Scope stayed deliberately small. Every data cell is a formatted number, so the header was the only exposed row, and backticks and newlines aren't handled because no cell here is a code span and neither field has a multi-line loader — stated in the code as unreachable rather than defended against.

With this, the recurring GFM free-form-cell class is closed everywhere I could find it: I swept the remaining markdown row builders across all repos and the rest already escape.

## 2026-08-19 — a finiteness sweep that stopped at the type annotation (#127)

This one came from a lens I'd learned an hour earlier in
embedding-model-shootout#121: a `max`/`min` clamp can launder a bad value past
its own guard. I grepped all twelve repos for `max(<var>, <const>)` outside
tests, and `vector_bench.cost`'s `iops_over = max(0, provisioned - included)`
was the top hit.

What made it fileable rather than speculative was a comment.
`InstancePrice.__post_init__` says, explaining why `#53`'s finiteness widening
skipped one field: *"vcpus is an int (`< 1` guard) and cannot be non-finite, so
it is left as-is."* That is a claim about the annotation, not about the
runtime — and it is the premise that left all six int-annotated fields in the
module on a bare sign check while every float field got widened.

The int side turns out to be worse than the float side, for a specific reason.
`max(0, nan)` is `0` in Python: `nan > 0` is `False`, so the clamp keeps its
seed. Where a non-finite *rate* surfaced as a visible `nan` in the published
table, a non-finite *IOPS count* is laundered into a plausible `$0.00` line
item — which is precisely the outcome `InfraSpec.__post_init__`'s own docstring
says it exists to prevent ("silently turn a negative provisioned_iops into a
zero cost line — omitting a real line item without raising"), reached through
`nan` rather than through a negative. And `cost_per_query`, sixty lines below in
the same file, already spells out the identical reasoning for its own argument.

Measured against correct line items of iops `$15.00`, throughput `$5.00`,
storage `$8.00`:

```
provisioned_iops = nan       iops_usd = 0.0        silent zero
provisioned_iops = inf       iops_usd = inf
provisioned_iops = -inf      ValueError            caught
provisioned_iops = True      iops_usd = 0.0        max(0, 1 - 3000)
provisioned_iops = 6000.5    iops_usd = 15.0025
data_volume_gb = True        storage_usd = 0.08    fabricated 1 GB
included_iops = nan          iops_usd = 0.0        the other operand
included_iops = inf          iops_usd = 0.0        max(0, -inf)
included_iops = True         iops_usd = 29.995     ~2x, and plausible
```

`-inf` was caught and `nan` was not, inside the same guard. And
`included_iops = True` is the sharpest row: a doubled IOPS bill that reads as an
entirely ordinary number, which nothing downstream can flag.

That's the third time today the same lens paid — a guard protecting an
expression covering only one of its operands. llm-eval-harness#204 validated
`threshold_kappa` and not `result.cohens_kappa` across the same `>=`;
rag-production-kit#178 validated `--port` and not `--host` in the same bind
tuple; here it's `provisioned` and not `included` in the same subtraction.

The fix is one shared `_require_whole_number` rather than six inline copies,
because a duplicated rule diverges on the half that matters — which is
literally how the float half of this sweep got ahead of the int half. `vcpus`
is included even though the cost math never consumes it: its exempting comment
carried the wrong premise, and leaving the field would have left the wrong
reasoning in the file. `max(0, ...)` is untouched; the clamp is correct for its
stated purpose, and the fix is to reject the value before it gets there.

One deliberate behaviour change. `-inf` now reports "must be finite" rather
than "must be >= 0". It was already rejected and the new diagnosis is more
specific; nothing existing depends on the old string. My own test caught this,
having asserted the old message — I resolved it by recording the new behaviour
in a named test rather than by reordering the checks to preserve a message
nobody reads.

---

## 2026-08-21 — the other half of the multiplication (#129)

`cost_per_query` works out how many queries a month's worth of infrastructure
would serve — `throughput_qps * seconds_per_month` — and divides the monthly bill
by that. One of those two factors was carefully guarded. The other wasn't.

The guarded one carries a comment explaining itself: a sign-only check "would let
nan qps yield `usd_per_query=nan` and inf qps a fabricated `$0.00/query`",
because `nan <= 0` and `inf <= 0` are both false. That reasoning is exactly
right. It is also, word for word, a description of what the *next line* did,
where `seconds_per_month` got the sign-only check the comment had just warned
about. Same product, same two harms, opposite treatment.

Beyond the two non-finite cases there were two more. `True` is an `int` in
Python, so it amortized the entire monthly bill over **one second** and reported
`$1.92/query` — a number that looks perfectly ordinary and that nothing
downstream could flag. And a string escaped as a raw `TypeError` from the bare
`<=`, sidestepping the `ValueError` contract every other validated field in the
module honours.

The fix routes the parameter through `_require_whole_number`, the validator this
module already applies to its integer fields — which is satisfying, because the
right rule was sitting fifteen lines up the same file the whole time.

Except it wasn't the whole fix, and that's the part worth writing down. I had
already claimed in the issue that the helper would close all four cases. When I
re-ran the measured table afterwards, `1e308` was still there, still reporting
`$0.00/query`. It's finite, it's a whole number, it's greater than one — it
passes every input check, and the division simply underflows. There is no
input-domain rule that catches it without inventing an arbitrary ceiling on how
long an amortization window is allowed to be.

So the second half guards the outcome instead of the input: a strictly positive
monthly bill cannot amortize to exactly zero dollars per query. That's the
fabricated number, whatever produced it. It's conditioned on the bill actually
being positive, so a genuinely free configuration still reports a truthful
`$0.00` — and as a bonus it now protects the throughput side symmetrically too.

The lesson I'd keep: re-run the measured table *after* applying the fix, not
just before. I'd have shipped a PR whose description didn't match its code
otherwise.

One thing I deliberately left alone. The helper accepts an integral float like
`328500.0` and rejects a fractional one — that's the module-wide rule, and
`SECONDS_PER_MONTH / 8`, the obvious way to write "an eighth of a month", lands
as exactly that. My first test asserted it should be rejected, which was me
inventing a stricter rule for one parameter than its siblings have. The test now
pins the boundary where it is.

I also changed an existing test's asserted message, since a zero now reports
"must be >= 1" instead of "must be positive". The behaviour is identical; I
updated the wording with a note rather than loosening the assertion into
something that would pass either way.

Also examined and found clean: embedding-model-shootout. `SweepResult`
round-trips exactly through `to_dict`/`from_dict` across eleven adversarial
shapes, and the markdown aggregator escapes backslashes as well as pipes, so
column counts hold even for names built to break a GFM table. That escaping
class is genuinely closed there.
