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
