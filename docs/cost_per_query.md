# Cost per query

Amortized USD per query at each (tier, engine), computed from the infrastructure Terraform brings up plus the throughput the load harness measures.

## Assumptions

- **Pricing snapshot**: AWS public on-demand list, region us-east-1, as of 2026-05-17. Source: <https://aws.amazon.com/ec2/pricing/on-demand/>. Operators with contracted rates (Reserved, Spot, EDP) override the `PriceTable` and re-run the script.
- **Hours per month**: 730 (AWS billing convention, 8760 / 12).
- **Amortization basis**: monthly cost ÷ (throughput_qps × 2,628,000 s). If your workload doesn't run 24/7, multiply by (24 / avg_active_hours_per_day).
- **Throughput**: from `results/load/<run_id>/c001.json` (single-client p50; the conservative basis). For each tier the source is listed in the table.
- **Instance sizing**: read live from [`terraform/envs/benchmark/main.tf`](../terraform/envs/benchmark/main.tf) so this doc and the infra layer can't drift.

## Per-tier table

| Scale | Engine | Instance | EBS | Monthly $ | qps | $/query | $/M queries | Throughput source |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 1m | pgvector | m6i.large | $4.00/mo storage | $74.08 | 1623.5 | $0.000000 | $0.02 | `results/load/stub-10k/c001.json` (simulated) |
| 1m | qdrant | m6i.large | $4.00/mo storage | $74.08 | 1623.5 | $0.000000 | $0.02 | `results/load/stub-10k/c001.json` (simulated) |
| 1m | weaviate | m6i.large | $4.00/mo storage | $74.08 | 1623.5 | $0.000000 | $0.02 | `results/load/stub-10k/c001.json` (simulated) |
| 10m | pgvector | r6i.xlarge | $16.00/mo storage + $15.00/mo IOPS + $5.00/mo throughput | $219.96 | 1623.5 | $0.000000 | $0.05 | `results/load/stub-10k/c001.json` (simulated) |
| 10m | qdrant | r6i.xlarge | $16.00/mo storage + $15.00/mo IOPS + $5.00/mo throughput | $219.96 | 1623.5 | $0.000000 | $0.05 | `results/load/stub-10k/c001.json` (simulated) |
| 10m | weaviate | r6i.xlarge | $16.00/mo storage + $15.00/mo IOPS + $5.00/mo throughput | $219.96 | 1623.5 | $0.000000 | $0.05 | `results/load/stub-10k/c001.json` (simulated) |
| 100m | pgvector | r6i.4xlarge | $120.00/mo storage + $45.00/mo IOPS + $15.00/mo throughput | $915.84 | 1623.5 | $0.000000 | $0.21 | `results/load/stub-10k/c001.json` (simulated) |
| 100m | qdrant | r6i.4xlarge | $120.00/mo storage + $45.00/mo IOPS + $15.00/mo throughput | $915.84 | 1623.5 | $0.000000 | $0.21 | `results/load/stub-10k/c001.json` (simulated) |
| 100m | weaviate | r6i.4xlarge | $120.00/mo storage + $45.00/mo IOPS + $15.00/mo throughput | $915.84 | 1623.5 | $0.000000 | $0.21 | `results/load/stub-10k/c001.json` (simulated) |

## What the numbers say (and don't)

- The infra bill is **identical across engines per tier** because they share the same instance type + EBS sizing (see the Terraform locals). The cost-per-query differences between engines therefore come from throughput differences, not from hardware differences.
- The amortization assumes a 24/7 sustained workload at the throughput in the `qps` column. A bursty production workload that runs 8 hours/day will see ~3× the per-query cost; the README's writeup leads with that.
- 100M-tier numbers in the table are flagged when the throughput source isn't real-engine data; a `(simulated)` annotation means the c001.json under `results/load/` came from the stub or HNSW simulator. Real-engine numbers require `make up` + the load harness; the operator commits new throughput files and re-runs the script.

Refresh this doc with `python scripts/cost_table.py --dry --out docs/cost_per_query.md` after any change to `terraform/envs/benchmark/main.tf`, `src/vector_bench/prices.py`, or the load-harness throughput files.
