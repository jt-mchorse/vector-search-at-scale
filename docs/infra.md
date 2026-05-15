# Benchmark infrastructure

Terraform-managed AWS infra used by every benchmark in this repo. Three vector
backends (pgvector, Qdrant, Weaviate) at three scale tiers (1M, 10M, 100M
vectors), all in one VPC, single-AZ, single-node per backend.

> The numbers below are **on-demand list prices in `us-east-1` as of 2026-05**
> and should be re-checked before each benchmark run. They are sized for the
> raw-storage + HNSW-index memory budget assuming **384-dimensional float32
> vectors** (matches the canonical embedding model in
> [`chunking-strategies-lab`](https://github.com/jt-mchorse/chunking-strategies-lab)).
> If your workload uses a different embedding dimension, re-derive the
> per-tier sizing rather than reusing this table verbatim.

## Per-tier sizing

Each tier deploys three EC2 instances of identical type — one per backend —
plus one EBS data volume per backend. The shared VPC + subnet + IGW are
negligible cost.

| Tier  | Vectors  | Raw vector bytes | EC2 type    | vCPU | RAM    | EBS gp3 |
|-------|----------|------------------|-------------|------|--------|---------|
| `1m`  | 1×10⁶    | ~1.5 GiB         | `m6i.large` | 2    | 8 GiB  | 50 GiB  |
| `10m` | 10×10⁶   | ~15 GiB          | `r6i.xlarge`| 4    | 32 GiB | 200 GiB |
| `100m`| 100×10⁶  | ~150 GiB         | `r6i.4xlarge`| 16  | 128 GiB| 1500 GiB|

**Sizing assumptions.**
- Raw vector bytes = `N × dim × 4` (float32). With `dim = 384` that's `~1.5
  KiB/vector` for the raw payload.
- HNSW indexes typically run **1.5–2.5×** the raw payload in RAM (graph + IDs +
  per-layer pointers). The 1m / 10m tiers fit comfortably in RAM; 100m
  intentionally does *not* — it's the on-disk-index regime, which is what we
  actually want to measure for the largest tier (the interesting question at
  100m is "how badly does each engine degrade when the index spills").
- EBS sizing leaves 2–3× headroom over the on-disk index for restores,
  snapshots, and per-engine bookkeeping.

## Per-tier on-demand cost (us-east-1)

EC2 instances are billed per second; EBS gp3 is billed per GB-month plus
provisioned IOPS/throughput above the 3000/125 baseline.

| Tier  | EC2 (3× $/hr)              | EBS (3× $/hr)¹  | Total $/hr | $ for an 8h benchmark window |
|-------|----------------------------|------------------|------------|-------------------------------|
| `1m`  | 3× $0.096 = $0.288         | ~$0.05           | ~$0.34     | ~$2.70                        |
| `10m` | 3× $0.252 = $0.756         | ~$0.27           | ~$1.03     | ~$8.20                        |
| `100m`| 3× $1.008 = $3.024         | ~$1.45           | ~$4.47     | ~$35.80                       |

¹ EBS hourly cost approximated from the per-GB-month list prices for gp3
(`$0.08/GB-month` storage, `$0.005/IOPS-month` over 3000, `$0.04/MiB/s-month`
over 125), divided by 730 hours/month. The 10m and 100m tiers also pay for
provisioned-above-baseline IOPS and throughput.

**Out of these numbers (intentionally).** Data transfer (in is free; out is
$0.09/GB after the first 100 GiB/month), NAT gateway (we don't run one — the
public-subnet design uses an IGW directly), CloudWatch, and IAM. None
materially move the per-tier total for short benchmark windows.

## Change protocol

Sizing or cost numbers in this doc are derived from real AWS list prices and
the rules of thumb above. **Don't change a number without re-deriving it from
the underlying inputs.** If a change makes the table inconsistent with what
the Terraform actually deploys (`terraform/envs/benchmark/main.tf` has the
canonical `tiers` map), update both in the same PR.

## What the modules ship

```
terraform/
├── modules/
│   ├── common-network/   # VPC, IGW, public subnet, optional SSH SG
│   ├── pgvector/         # EC2 + EBS + SG (5432) + Docker pgvector/pgvector:pg16
│   ├── qdrant/           # EC2 + EBS + SG (6333/6334) + Docker qdrant/qdrant:vX
│   └── weaviate/         # EC2 + EBS + SG (8080/50051) + Docker weaviate:X.Y.Z
├── envs/
│   └── benchmark/        # composes the modules per tier
└── scripts/
    └── health.sh         # poll all three readiness endpoints
```

Service ports are restricted to the VPC CIDR (`10.42.0.0/16`) — no public
ingress on the database ports. SSH ingress is opt-in (default empty) per
operator.

## Running it

See the [README Quickstart](../README.md#quickstart) and the
[Makefile](../Makefile) for the operator workflow. The only required
environment variable is `AWS_PROFILE` (or equivalent AWS credential).
