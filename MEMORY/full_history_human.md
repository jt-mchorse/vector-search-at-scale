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
