# Core Decisions

Strategic decisions for this repo, with reasoning. Append-only — superseded decisions are marked, not removed.

## D-001 — Scope locked to portfolio handoff §2 (2026-05-10)
**Decision:** Scope of this repo is fixed by the portfolio handoff document, section 2.

**Why:** The handoff spec was deliberated; ad-hoc scope expansion within a session is the failure mode this prevents.

**Alternatives considered:** None — this is a baseline.

**Reversibility:** Expensive. Scope changes require a deliberate revisit and a new decision entry.

**Related issues:** —

## D-002 — AWS, single region, single AZ for the benchmark substrate (2026-05-15)
**Decision:** All benchmark infra runs on AWS, defaulted to `us-east-1`, in a single AZ (`us-east-1a` by default).

**Why:** The benchmark exists to compare *engines*, not cloud regions. Single AZ removes a confounding millisecond of cross-AZ network latency from every measurement. Single region removes egress costs and provider-bias from the cost comparison. AWS specifically because it's the lowest-friction provider for short ephemeral EC2 workloads with predictable on-demand pricing.

**Alternatives considered:**
- Multi-AZ for realism — rejected because realism here trades blast-radius (which we don't care about for an ephemeral benchmark) for noise (which we do).
- GCP — rejected as roughly equivalent; AWS picked for incumbency.
- Multi-cloud — rejected as out of scope; the comparison axis we care about is engine, not provider.

**Reversibility:** Cheap. Region/AZ are `terraform.tfvars` values; the modules don't bake them in.

**Related issues:** #1

## D-003 — Third backend is Weaviate (open-source, self-hosted) (2026-05-15)
**Decision:** The third vector backend, alongside pgvector and Qdrant, is Weaviate. Pinecone is explicitly out.

**Why:** pgvector and Qdrant are both self-hosted open-source engines. To run a fair latency / cost / recall comparison, the third backend has to live on the same EC2 instance type with the same EBS volume — i.e., self-hostable. Weaviate fits; Pinecone doesn't (it's SaaS-only, so a "Pinecone row" would be measuring AWS-vs-Pinecone-cloud network plumbing rather than engine behavior). If JT wants a managed-SaaS contrast row later, that's a separate D-NNN with separate sizing math.

**Alternatives considered:**
- Pinecone — rejected as above.
- Milvus — rejected as a heavier-weight cluster engine; out of scope at single-node.
- Vespa — rejected for similar weight + setup-complexity reasons.

**Reversibility:** Cheap. The module pattern accommodates new backends as siblings; adding a fourth is roughly the same shape as the existing three.

**Related issues:** #1

## D-004 — Single-node per backend, no replication, pinned Docker tags (2026-05-15)
**Decision:** Every backend deploys as one container on one EC2 instance with one EBS data volume. No replication. All Docker tags are pinned (`pgvector/pgvector:pg16`, `qdrant/qdrant:v1.14.0`, `semitechnologies/weaviate:1.34.0`).

**Why:** Reproducibility is the load-bearing constraint of the entire repo. A future operator running `make up` six months from now needs to get the same engine version we benchmarked, full stop. Replication adds a confound (replica lag, leader election overhead) that's outside the questions this repo is trying to answer.

**Alternatives considered:**
- Hand-managed installs (apt / pgrepo) — rejected because Docker pinning is more reliable across distro upgrades.
- Floating `:latest` tags — rejected as the opposite of reproducible.
- Replicated clusters — rejected as a separate study; if/when the question becomes "how does each engine behave under replication," that's its own repo or its own decision.

**Reversibility:** Cheap. Tags are module variables; clustering is a module-level rewrite but the modules are small.

**Related issues:** #1

## D-005 — Backend is a Protocol with `ingest` + `query`, lazy SDK imports per extra (2026-05-15)
**Decision:** `vector_bench.types.Backend` is a runtime-checkable Protocol with `name`, `ingest(vectors, ids)`, `query(vector, k)`, and `close()`. Each real engine adapter (`pgvector`, `qdrant`, `weaviate`) lives in its own module under `src/vector_bench/backends/` and lazy-imports its SDK, with a clear `BackendError` message pointing at the missing pip extra.

**Why:** Same single-method-protocol pattern as `eval-harness.Backend`, `rag-production-kit.{Embedder,Reranker,Generator}`, and `embedding-model-shootout.Embedder`. The harness can score any backend the same way; tests don't need a real engine.

**Alternatives considered:**
- Abstract base class — rejected: Python `Protocol` is structural so test stubs don't need to inherit.
- Single concrete class with branching — rejected: makes the swappable-backends story muddier as engines are added.
- LangChain-style chain — rejected: heavy dep tree for a two-method contract.

**Reversibility:** Cheap. New backends are net-adds; the Protocol grows kwargs without breaking existing implementers.

**Related issues:** #2

## D-006 — Stub backend ships in base install for hermetic CI (2026-05-15)
**Decision:** `StubBackend` is a pure-numpy backend that IS the ground truth (by construction it uses the same cosine similarity the harness uses for the ground-truth top-k). Recall@k on the stub is 1.0 by design. It's the default in CI and serves as a sanity baseline for real engines.

**Why:** Same hermetic-CI rationale as `rag-production-kit`'s `LexicalOverlapReranker` (D-006 in that repo) and `eval-harness`'s deterministic judge stub. The harness needs to be exercisable end-to-end without AWS bring-up; the stub does that. Real engines should approach recall@k = 1.0 as their HNSW parameters are tuned up — useful as a calibration target.

**Alternatives considered:**
- Require AWS for any test — rejected: makes CI brittle, slow, and expensive.
- Mock engine clients in each test file — rejected: spreads the mock surface across tests instead of concentrating it in one well-documented backend.

**Reversibility:** Cheap. The stub stays even after real engines land; it's the recall ceiling.

**Related issues:** #2

## D-007 — One JSON file per `run_id` under `results/`, refuse overwrite without `--force` (2026-05-15)
**Decision:** `run_benchmark(..., run_id=R, results_dir=results)` writes `results/<run_id>.json`. If the file exists, the harness raises `FileExistsError` unless `force=True` (CLI: `--force`). No per-run history database, no append-only JSONL.

**Why:** Idempotency by filesystem, not by parsing a prior JSON. Catches the most common operator typo (re-using an old `run_id`) loudly rather than silently overwriting. Two backends comparing the same workload differ only by `run_id`, so the operator's mental model is "one file per backend × workload × scale × revision."

**Alternatives considered:**
- SQLite history (cf. `eval-harness`) — rejected: overkill for the per-engine workload this harness exercises, and the `eval-harness` use case is per-row diffing which doesn't apply here.
- Append-only JSONL — rejected: per-run atomicity is harder to argue for; partial writes pollute the history.
- No persistence — rejected: the issue requires structured JSON output as an acceptance criterion.

**Reversibility:** Cheap. Output format is a pure-function of `BenchmarkResult.to_json()`.

**Related issues:** #2, #3, #4, #5

## D-008 — Latency-under-load driven by ThreadPoolExecutor over the `Backend` Protocol, not k6/locust (2026-05-16)
**Decision:** The latency-under-load study (#4) drives concurrent queries via Python's `ThreadPoolExecutor` against the existing `Backend` Protocol (D-005), at concurrency levels `1, 10, 100`. Output is one JSON per concurrency cell plus a `matrix.json` index, under `results/load/<run_id>/`. The k6/locust formulation in the issue body is re-scoped to this equivalent Python driver.

**Why:** k6 (the issue body's first suggestion) is HTTP-only, but **`pgvector` talks the PostgreSQL wire protocol** — driving load to it through k6 would require an HTTP shim, which is a translation layer that itself introduces latency and is not part of the production query path. Locust would work, but a second top-level Python tool that talks through a different abstraction than the harness already uses means two different views on "is this run idempotent" / "is this workload deterministic." Running through the `Backend` Protocol keeps the abstraction count at one across `vector-bench run` (per-backend single-thread) and `vector-bench load` (per-backend concurrent) — apples-to-apples across all three backends with zero translation.

The three backend SDKs we adapter against (`psycopg2` for pgvector, `qdrant-client`, `weaviate-client`) are sync clients. Threads are the natural concurrency primitive; `asyncio.to_thread` would buy nothing over `ThreadPoolExecutor` and would add an extra layer to debug.

**Alternatives considered:**
- k6 + HTTP shim for pgvector — rejected; adds latency and an extra surface to maintain, and the shim itself becomes a benchmark target.
- Locust with two separate drivers (Postgres + REST) — rejected; loses the single-abstraction story and doubles the per-engine maintenance surface.
- asyncio-based driver — rejected; the SDKs are sync, so this just means `asyncio.to_thread` under the hood; no concurrency win, more debugging surface.

**Reversibility:** Cheap. The load module (`src/vector_bench/load.py`) is ~250 lines; replacing it with k6 + a shim is a swap-out, not a refactor. The output `matrix.json` schema is stable and downstream consumers (the `plot_latency.py` script, downstream issues #3 / #5) don't care how the cells were produced.

**Related issues:** #4

## D-009 — `HnswSimBackend` is a pure-numpy *simulation* of HNSW's tradeoff, not a real HNSW implementation (2026-05-17)
**Decision:** The HNSW parameter-tuning study (#3) is exercised in CI against a pure-numpy backend named `HnswSimBackend` that *simulates* the recall/latency behavior of the three canonical HNSW parameters (`M`, `ef_construction`, `ef_search`). The module docstring and the README "HNSW parameter tuning" section are explicit that this is a simulation: it produces a qualitatively correct curve (low ef_search → low recall + low latency, monotone increase, plateau near 1.0 recall at large ef_search) so the grid + frontier script are testable hermetically; the same scripts apply unchanged to real engines (qdrant / weaviate / pgvector) via `--backend` when the AWS bring-up is done.

**Why:** Three reasons compose. (1) The acceptance criteria asks for "grid script runnable" and "frontier plot committed" — both of those need a backend with HNSW knobs that runs without external infra. The real engines need `make up SCALE=1m` per D-004, which is operator-cost-bearing and out of scope for a single session. (2) Vendoring `hnswlib` adds a C extension to a portfolio meant to be `pip install -e .` clean; the simulation gives us the same parameter behavior without the dependency. (3) The simulation produces *real* numbers from a transparent model — the curves are reproducible, the math is one ~120-line module, and the framing is honest. A reader can see exactly why ef_search=128 gives ~99% recall vs ef_search=16 giving ~10% on this workload.

**Alternatives considered:**
- Require AWS bring-up for any HNSW grid run — rejected; breaks dep-free default, makes the script untestable in CI, and locks the study behind operator dollars before the script is even validated.
- Vendor `hnswlib` as a required dep — rejected; adds a C extension for a demo repo; the absolute numbers it produces only matter when the real engines are also running, at which point we're using `--backend qdrant` anyway.
- Leave the script to operate against real engines only — rejected; no baseline curve means the script's correctness can't be unit-tested, and a reader without AWS access has nothing to look at.

**Reversibility:** Cheap. `HnswSimBackend` is one module (~120 lines); the grid + plot scripts call it via the same `Backend` protocol every other backend uses, so swapping in `hnswlib` later is one new file plus a registration in `backends/__init__.py`.

**Related issues:** #3

## D-010 — Cost model ships a documented AWS us-east-1 list-price snapshot with caller override (2026-05-17)
**Decision:** `src/vector_bench/cost.py` accepts a `PriceTable` on every callsite. The default `PriceTable` comes from `src/vector_bench/prices.aws_us_east_1_snapshot()`, which carries a `snapshot_date`, a `source_url`, and three instance entries (m6i.large, r6i.xlarge, r6i.4xlarge) plus gp3 EBS pricing. Unknown instance types raise with the known list and a pointer at the override path — never silently use a fabricated price.

**Why:** Three constraints. **First**, the no-fabricated-numbers rule (portfolio handoff §10) extends to prices the same way it extends to benchmark figures — if the table says "$0.05/M queries at 10M scale", the operator needs to be able to verify each input that produced that number. The snapshot's `source_url` and `snapshot_date` let a reviewer click through and confirm. **Second**, the cost table has to regenerate in CI, which means the prices have to live somewhere the build can read — wiring prices on every callsite would make `scripts/cost_table.py` non-runnable without an environment variable, which would break the "the README is what the script produced" property the rest of the repo enforces. **Third**, operators with real contracts (Reserved Instances, Spot, EDP, non-us-east-1 regions) have different rates — they should be able to compute the same table against their own prices without editing repo state. Caller override on every cost function gives them that without touching the snapshot defaults.

**Alternatives considered:**
- Ship no defaults; require operator to wire prices every run — rejected. CI can't regenerate the table without prices, which means the committed `docs/cost_per_query.md` would either go stale silently or have to be hand-maintained.
- Build pricing into the model code (constants inline) — rejected. Loses `snapshot_date` and `source_url` visibility; an inline `0.0960` doesn't tell a reviewer when it was true or where to verify.
- Online price lookup against AWS's pricing API — rejected. CI DNS flakes; rates move slowly enough (months between meaningful changes) that an offline snapshot with a documented bump procedure is the honest answer; the operator owns the bump on a deliberate PR.

**Reversibility:** Cheap. The snapshot is one file with a dict + a date string. Bumping it is "edit the file, bump `SNAPSHOT_DATE`, re-run `scripts/cost_table.py`, commit both"; the bump procedure ships in the script's module docstring and the README.

**Related issues:** #5

## D-011 — `run_benchmark` refuses `workload.concurrency != 1`

- **Date.** 2026-05-22
- **Decision.** `run_benchmark` raises `ValueError` immediately when `workload.concurrency != 1`, with an error message that points the caller at `vector_bench.load.run_under_load` (or `vector-bench load` on the CLI). `Workload.concurrency` remains a field — the load module uses it per cell — but the single-shot serial entry point will not run with concurrency > 1.
- **Why.** `run_benchmark` executed queries serially in a `for i in range(workload.n_queries)` loop but recorded `workload.concurrency` on the output JSON verbatim. Calling `vector-bench run --concurrency 8 ...` produced a results JSON whose `workload.concurrency == 8` and whose `query_latency.p95_ms` was a single-threaded number. For a repo whose tagline is "the kind of doc you'd cite in an architecture review", a latency stat that lies about its concurrency is the credibility leak to close. The fix is the same shape as `chunking-strategies-lab`'s D-011 (which also closed today): documented-only constraints fail silently, so promote them to runtime enforcement. Closes #19.
- **Alternatives considered.**
  - *Make `run_benchmark` actually parallelize.* Rejected: duplicates `vector_bench.load`'s ThreadPoolExecutor logic and confuses the two-subcommand surface. `load` is the well-shaped concurrent entry point (D-008); routing concurrency there is the right contract.
  - *Remove `Workload.concurrency` entirely.* Rejected: the `load` module's `LoadCell` records the per-cell concurrency on its output, and the `Workload` field is the structural bridge between the matrix runner and the per-cell harness. Removing it breaks that.
  - *Document-only enforcement.* Rejected: the same shape as the bug we're closing. Documented constraints fail silently in operator code.
- **Reversibility.** Cheap. If a future caller wants to deliberately produce a serial run that records `workload.concurrency = N`, an explicit `allow_misreport=True` flag can be added — but YAGNI.
