# Architecture

The benchmark substrate has three layers that reuse each other top-to-
bottom: infrastructure brings up the engines, the harness drives them
through a uniform workload, and the per-axis studies project the
resulting JSON into HNSW / latency / cost narratives. All three layers
ship; the only thing still operator-supplied is the AWS-side
`terraform apply` (real-backend runs cost real money), which the
stub-mode harness substitutes for in CI so the analysis pipelines have
deterministic JSON to exercise on every PR.

```mermaid
flowchart LR
  classDef shipped fill:#dcffe4,stroke:#22863a,color:#000
  classDef opkey   fill:#fff5b4,stroke:#c69400,color:#000

  subgraph Operator
    OP[operator workstation]:::shipped
  end

  subgraph AWS["AWS · single region · single AZ"]
    subgraph VPC["VPC 10.42.0.0/16"]
      subgraph SUB["public subnet 10.42.1.0/24"]
        PG[pgvector EC2<br/>:5432]:::opkey
        QD[qdrant EC2<br/>:6333 :6334]:::opkey
        WV[weaviate EC2<br/>:8080 :50051]:::opkey
        BENCH[(benchmark client<br/>vector_bench harness<br/>(#2))]:::shipped
      end
      IGW((IGW)):::shipped
    end
  end

  OP -- terraform apply --> AWS
  OP -. ssh .-> PG
  OP -. ssh .-> QD
  OP -. ssh .-> WV
  BENCH --> PG
  BENCH --> QD
  BENCH --> WV
  IGW --- SUB
```

Green nodes ship in code and run in CI (the harness runs against an
in-process stub backend on every PR). Yellow nodes are *wired*
end-to-end — Terraform brings them up in one `make up`, the harness
hits them on the documented ports — but the actual EC2 launch is
operator-supplied (per-tier hourly cost in [`docs/infra.md`](./infra.md)).

## Layer 1 — Infra (#1)

- One VPC, one public subnet, one IGW. Single AZ on purpose: any cross-AZ
  millisecond would muddy the latency comparison between engines.
- Three backend modules under `terraform/modules/`. Each module is
  *self-contained*: EC2 + EBS data volume + service-specific security
  group + `user_data.sh` that brings the service up via Docker on first
  boot.
- The benchmark `env` (`terraform/envs/benchmark/`) composes the modules
  at one of three scale tiers (`1m`, `10m`, `100m`); the tier drives
  instance type and EBS sizing per [`docs/infra.md`](./infra.md).
- Service ports are restricted to the VPC CIDR. Optional SSH ingress is
  default-empty so `make up` doesn't accidentally expose the engines.
- Operator workflow lives in the `Makefile`: `make up SCALE=1m` /
  `make down SCALE=1m`.

## Layer 2 — Benchmark harness (#2)

Single-script harness driven by the `vector-bench` console script
(`src/vector_bench/`). Ingests N vectors at a configured `dim`, runs
queries with a configured concurrency, and emits structured JSON per
run. Same workload runs against all three backends so the only thing
differing between runs is the engine.

- **`vector-bench run`** — single-client ingest + query workload.
  Records ground-truth recall against an exact-search oracle. Refuses
  `--concurrency > 1` (use `vector-bench load` instead) per D-011 so
  it can't silently report serial latency tagged as concurrent.
- **`vector-bench load`** — multi-client concurrency sweep
  (`--clients 1,10,100`). Emits one JSON per concurrency level.
- **Backends.** Real backends under `src/vector_bench/backends/`
  (pgvector / qdrant / weaviate). Stub backend exercises every code
  path hermetically — that's what runs in CI on every PR.
- Output JSON shape is stable and locked by
  `tests/test_harness.py` + `tests/test_load.py`; downstream studies
  bind to that shape, not to the backend's wire format.

## Layer 3 — Per-axis studies

Each study reuses Layers 1 + 2 unmodified. Output is one reproducible
markdown per study; the README's "Benchmarks / Results" section
embeds the same numbers and is locked to the scripts by
`tests/test_readme_snapshot.py` + `tests/test_hnsw_recommended_defaults_snapshot.py`.

- **HNSW parameter tuning (#3).** `scripts/hnsw_grid.py` sweeps
  `M`, `ef_construction`, `ef_search` for each backend; the knee
  finder picks the recommended-defaults row published in the README's
  "HNSW parameter tuning" table. `scripts/plot_hnsw_frontier.py`
  renders [`docs/hnsw/frontier.{png,svg}`](./hnsw/). The recommended-
  defaults row in the README is snapshot-tested against the live
  grid + knee logic (#14).
- **Latency under load (#4).** `scripts/plot_latency.py` consumes
  `results/load/<run_id>/c<NNN>.json` (the `vector-bench load`
  output) and writes the latency curves the README's "Latency under
  load" section quotes. Until real-backend runs exist, the stub-mode
  curves drive the in-CI snapshot.
- **Cost per query (#5).** `scripts/cost_table.py` joins
  `src/vector_bench/prices.py` (AWS on-demand snapshot;
  operator-overridable for Reserved / Spot / EDP rates) with the
  load harness's throughput numbers and emits
  [`docs/cost_per_query.md`](./cost_per_query.md). Sizing is read live
  from `terraform/envs/benchmark/main.tf` so the cost doc and the
  infra layer can't drift.

## What's still operator-supplied

- **Real-backend benchmark runs.** `terraform apply` + AWS credit
  lives with the operator; the harness has the code to drive them but
  the wall-clock cost is real. The in-CI baseline is the stub backend,
  which exercises every code path deterministically. `docs/benchmarks.md`
  carries the honest "real numbers pending" framing.
- **Captured 60-second walkthrough** (#12) — a deterministic recording
  via `scripts/capture_demo.sh` is shipped; the GIF/video binary is
  the operator's recording step.

## Where to look next

- **Infra** — `terraform/envs/benchmark/`, `terraform/modules/`,
  [`docs/infra.md`](./infra.md).
- **Harness** — `src/vector_bench/`, `tests/test_harness.py`,
  `tests/test_load.py`.
- **HNSW study** — `scripts/hnsw_grid.py`,
  `scripts/plot_hnsw_frontier.py`, [`docs/hnsw/`](./hnsw/),
  `tests/test_hnsw_grid.py`, `tests/test_hnsw_recommended_defaults_snapshot.py`.
- **Latency study** — `scripts/plot_latency.py`, `tests/test_load.py`.
- **Cost study** — `scripts/cost_table.py`, `src/vector_bench/prices.py`,
  [`docs/cost_per_query.md`](./cost_per_query.md), `tests/test_cost_table.py`.
- **Public surface** — `tests/test_public_surface.py` (#16) locks the
  `vector_bench` package's top-level exports and console script
  registration.
- **Design decisions** — `MEMORY/core_decisions_human.md` for prose,
  `MEMORY/core_decisions_ai.md` for the structured log.
