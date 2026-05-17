"""Amortized $/query cost model (issue #5).

What this is: a small, dep-free, callable cost model that combines two
inputs — the **infrastructure** the benchmark runs on (EC2 instance type
+ EBS volume sizing) and the **query workload** the harness measures
(throughput_qps from `results/load/<run_id>/c01.json`) — into one
number per (tier, engine): the **amortized USD per query** at the
operator's chosen pricing.

Why a model and not a single magic number: pricing moves, regions
differ, and a contracted price isn't the same thing as public list.
The model takes a `PriceTable` (caller-supplied or the documented
snapshot from `vector_bench.prices`) and an `InfraSpec` (the same
sizing tuple Terraform reads from `envs/benchmark/main.tf`), so the
operator can swap a list-price snapshot for their actual rates
without touching anything downstream.

Cost decomposition:

  monthly_usd = instance_hours_per_month
              + ebs_storage_gb_month
              + ebs_iops_above_3000_month
              + ebs_throughput_above_125_mibps_month

  cost_per_query = monthly_usd / (qps × seconds_per_month)

Two notes the README repeats but the module enforces:

- **Hours per month is `730`**, not `720` or `744`. AWS's billing month
  is `8760 / 12 = 730 hours` (their term, not ours); pinning here means
  the table's headline numbers are reproducible against AWS Cost
  Explorer's own arithmetic.
- **Seconds per month follows hours**: `730 × 3600 = 2,628,000`.
  Operators who care about strict 30-day or 31-day months override
  `seconds_per_month` on `cost_per_query`.

No fabricated numbers anywhere: every price ships with a `source_url`
and a `snapshot_date`, and an unknown instance type raises rather
than guessing. Same posture as `llm-cost-optimizer.pricing` (D-003).
"""

from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class InstancePrice:
    """On-demand USD per hour for a single EC2 instance type.

    `vcpus` and `memory_gib` are carried through to the table for
    operator review; the cost math only consumes `usd_per_hour`.
    """

    instance_type: str
    region: str
    usd_per_hour: float
    vcpus: int
    memory_gib: float


@dataclass(frozen=True)
class EbsGp3Price:
    """gp3 EBS pricing surface. Three components, billed separately.

    Defaults to AWS's "first 3000 IOPS and 125 MiB/s included" rule;
    callers with a different baseline (e.g., gp3 in a non-default
    region) override the `included_*` fields.
    """

    region: str
    usd_per_gb_month: float
    usd_per_iops_month_over_baseline: float
    usd_per_mibps_month_over_baseline: float
    included_iops: int = 3000
    included_throughput_mibps: int = 125


@dataclass(frozen=True)
class PriceTable:
    """Operator-facing price snapshot. Constant in this module's
    perspective — passed in, never mutated.

    `instances` is keyed by `instance_type` (e.g., ``r6i.xlarge``).
    `ebs` is a single entry per region — extend to a dict if multiple
    storage classes are needed (gp3 + io2, etc.).
    """

    snapshot_date: str  # ISO-8601 day, e.g. "2026-05-17"
    instances: dict[str, InstancePrice]
    ebs: EbsGp3Price
    source_url: str

    def get_instance(self, instance_type: str) -> InstancePrice:
        try:
            return self.instances[instance_type]
        except KeyError as exc:
            known = ", ".join(sorted(self.instances))
            raise UnknownInstanceTypeError(
                f"No price recorded for {instance_type!r} in price table "
                f"snapshotted {self.snapshot_date}. Known: {known}. "
                f"Pass a PriceTable with the entry filled in to override."
            ) from exc


class UnknownInstanceTypeError(KeyError):
    """Raised when the price table has no entry for the requested instance type."""


@dataclass(frozen=True)
class InfraSpec:
    """One row in the per-tier infra table.

    Mirrors the locals in `terraform/envs/benchmark/main.tf`. The
    operator keeps the two in sync via the bump procedure documented
    in the README; tests pin the spec values used by `cost_table.py`
    against the Terraform locals so a Terraform-side bump that doesn't
    update this module fails CI.
    """

    scale_tier: str  # "1m" | "10m" | "100m"
    engine: str  # "pgvector" | "qdrant" | "weaviate"
    instance_type: str
    data_volume_gb: int
    provisioned_iops: int
    provisioned_throughput_mibps: int


@dataclass(frozen=True)
class CostBreakdown:
    """Itemized monthly cost for one (tier, engine)."""

    instance_usd_month: float
    storage_usd_month: float
    iops_usd_month: float
    throughput_usd_month: float

    @property
    def total_usd_month(self) -> float:
        return (
            self.instance_usd_month
            + self.storage_usd_month
            + self.iops_usd_month
            + self.throughput_usd_month
        )


@dataclass(frozen=True)
class CostPerQuery:
    """Result of one cost-per-query computation."""

    scale_tier: str
    engine: str
    monthly_cost: CostBreakdown
    throughput_qps: float
    seconds_per_month: int
    usd_per_query: float
    usd_per_million_queries: float


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# AWS bills 730 hours per month (8760 / 12). This is the number AWS
# Cost Explorer uses; pinning here means the table reproduces against
# their own arithmetic without a 1.4% drift caused by 720- or
# 744-hour-month conventions.
HOURS_PER_MONTH: int = 730
SECONDS_PER_MONTH: int = HOURS_PER_MONTH * 3600  # 2,628,000


# ----------------------------------------------------------------------
# Math
# ----------------------------------------------------------------------


def monthly_cost(infra: InfraSpec, prices: PriceTable) -> CostBreakdown:
    """Aggregate the four cost lines for one (tier, engine).

    Storage is `gb × $/GB-month`. IOPS over the 3000 baseline are
    billed per provisioned IOPS-month. Throughput over 125 MiB/s is
    billed per provisioned MiB/s-month. The "over the baseline" math
    is `max(0, provisioned - baseline)` so a tier sized at or below
    the baseline contributes nothing on that line.
    """
    instance = prices.get_instance(infra.instance_type)
    ebs = prices.ebs

    instance_usd = instance.usd_per_hour * HOURS_PER_MONTH
    storage_usd = infra.data_volume_gb * ebs.usd_per_gb_month
    iops_over = max(0, infra.provisioned_iops - ebs.included_iops)
    iops_usd = iops_over * ebs.usd_per_iops_month_over_baseline
    mibps_over = max(0, infra.provisioned_throughput_mibps - ebs.included_throughput_mibps)
    throughput_usd = mibps_over * ebs.usd_per_mibps_month_over_baseline

    return CostBreakdown(
        instance_usd_month=round(instance_usd, 4),
        storage_usd_month=round(storage_usd, 4),
        iops_usd_month=round(iops_usd, 4),
        throughput_usd_month=round(throughput_usd, 4),
    )


def cost_per_query(
    infra: InfraSpec,
    prices: PriceTable,
    throughput_qps: float,
    *,
    seconds_per_month: int = SECONDS_PER_MONTH,
) -> CostPerQuery:
    """Amortized $/query for one (tier, engine) at the measured throughput.

    `throughput_qps` is what the load harness measured (one of the
    `c01.json` files under `results/load/<run_id>/`). The amortization
    spreads the monthly cost across the queries the system would serve
    if it ran at that throughput for the whole month.

    The number is honest only to the extent the throughput is sustained
    — a workload that runs three hours per day instead of 24 should
    multiply the per-query result by 8. The README leads with that.
    """
    if throughput_qps <= 0:
        raise ValueError(f"throughput_qps must be positive, got {throughput_qps}")
    if seconds_per_month <= 0:
        raise ValueError(f"seconds_per_month must be positive, got {seconds_per_month}")
    breakdown = monthly_cost(infra, prices)
    monthly_queries = throughput_qps * seconds_per_month
    per_query = breakdown.total_usd_month / monthly_queries
    return CostPerQuery(
        scale_tier=infra.scale_tier,
        engine=infra.engine,
        monthly_cost=breakdown,
        throughput_qps=throughput_qps,
        seconds_per_month=seconds_per_month,
        usd_per_query=per_query,
        usd_per_million_queries=per_query * 1_000_000,
    )


__all__ = [
    "HOURS_PER_MONTH",
    "SECONDS_PER_MONTH",
    "CostBreakdown",
    "CostPerQuery",
    "EbsGp3Price",
    "InfraSpec",
    "InstancePrice",
    "PriceTable",
    "UnknownInstanceTypeError",
    "cost_per_query",
    "monthly_cost",
]
