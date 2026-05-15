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
