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
