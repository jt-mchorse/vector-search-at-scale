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

import math
from dataclasses import dataclass
from typing import Any

# ----------------------------------------------------------------------
# Shared field guard
# ----------------------------------------------------------------------


def _require_whole_number(value: Any, name: str, *, minimum: int) -> None:
    """Reject anything an ``int``-annotated sizing field can't actually be (#127).

    ``#53`` widened this module's sign-only bound checks to finiteness and
    covered the ``float`` fields only. Every ``int``-annotated field kept a bare
    ``value < N``, on the reasoning ``InstancePrice`` states outright: "vcpus is
    an int (``< 1`` guard) and **cannot be non-finite**, so it is left as-is."
    That is a claim about the annotation. At runtime an ``int``-annotated field
    holds whatever it is handed, and all six of them did.

    The harm is worse on the int side than it was on the float side, because the
    clamp in ``monthly_cost`` hides it. ``max(0, nan)`` is ``0`` in Python
    (``nan > 0`` is ``False``, so the clamp keeps its seed), so where a
    non-finite *rate* surfaced as a visible ``nan`` in the table, a non-finite
    *IOPS count* is laundered into a plausible ``$0.00`` line item — exactly the
    outcome ``InfraSpec.__post_init__``'s docstring says it exists to prevent
    ("silently turn a negative provisioned_iops ... into a zero cost line —
    omitting a real line item without raising"), reached through ``nan`` rather
    than through a negative. Measured, against a correct ``$15.00``:

        provisioned_iops = nan   -> iops_usd_month = 0.0
        provisioned_iops = inf   -> iops_usd_month = inf
        provisioned_iops = True  -> iops_usd_month = 0.0    (max(0, 1 - 3000))
        provisioned_iops = 6000.5-> iops_usd_month = 15.0025
        included_iops    = nan   -> iops_usd_month = 0.0
        included_iops    = inf   -> iops_usd_month = 0.0    (max(0, -inf))
        included_iops    = True  -> iops_usd_month = 29.995 (~2x, and plausible)

    ``included_iops = True`` is the sharpest of those: a doubled IOPS bill that
    reads as an ordinary number, which nothing downstream can flag.

    One helper rather than six inline copies. A duplicated rule diverges on the
    half that matters — which is how the ``float`` half of this same sweep ended
    up ahead of the ``int`` half in the first place.

    ``bool`` is excluded explicitly (it subclasses ``int``, and it is the row
    that produces the doubled bill). A non-integral ``float`` is rejected rather
    than truncated, matching the ``int`` annotation. A ``str``/``None`` raises
    this ``ValueError`` instead of a ``TypeError`` from the bare ``<``.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an int, not a bool; got {value!r} (pass {int(value)})")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number; got {value!r}")
    if not math.isfinite(value):
        raise ValueError(
            f"{name} must be finite; got {value!r} — the max(0, ...) clamp in monthly_cost "
            "turns a non-finite count into a silent $0.00 line item"
        )
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a whole number; got {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {value}")


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

    def __post_init__(self) -> None:
        # D-010 prevents silent-zero via UnknownInstanceTypeError; this guard
        # extends the same posture to silent-negative. A negative usd_per_hour
        # flows through monthly_cost() at line 194 and inverts the sign of
        # total_usd_month in the published cost table.
        #
        # The sign-only check is widened to finiteness (#53), matching the
        # downstream cost_per_query() guard (#51) and the sibling
        # llm-cost-optimizer.pricing sweep (#71): `nan < 0.0` and
        # `float("inf") < 0.0` are both False, so a non-finite usd_per_hour or
        # memory_gib slipped past the negative guard and poisoned monthly_cost()
        # -> total_usd_month -> cost_per_query (a nan rate makes usd_per_query
        # nan, +Inf makes it Inf), surfacing a fabricated row in the published
        # cost table with no diagnostic.
        #
        # This comment used to end "vcpus is an int (< 1 guard) and cannot be
        # non-finite, so it is left as-is." That was a claim about the
        # annotation, not the runtime, and it is what kept every int-typed field
        # in this module on a sign-only check while the float fields were
        # widened. Corrected in #127; `vcpus` now goes through
        # `_require_whole_number` like the five sizing fields below it.
        if not self.instance_type:
            raise ValueError("instance_type must be a non-empty string")
        if not self.region:
            raise ValueError("region must be a non-empty string")
        if not math.isfinite(self.usd_per_hour) or self.usd_per_hour < 0.0:
            raise ValueError(
                f"usd_per_hour must be a finite number >= 0.0; got {self.usd_per_hour}"
            )
        # Was `if self.vcpus < 1`, on the premise stated three lines up that an
        # int "cannot be non-finite" (#53). That premise is about the annotation,
        # not the runtime — see `_require_whole_number` (#127).
        _require_whole_number(self.vcpus, "vcpus", minimum=1)
        if not math.isfinite(self.memory_gib) or self.memory_gib < 0.0:
            raise ValueError(f"memory_gib must be a finite number >= 0.0; got {self.memory_gib}")


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

    def __post_init__(self) -> None:
        # See InstancePrice.__post_init__ — same D-010 sign-flip guard plus the
        # #53 finiteness widening, applied to the storage-side cost surface.
        # A non-finite usd_per_gb_month / per-IOPS / per-MiBps rate poisons the
        # storage, iops, and throughput cost lines the same way.
        if not self.region:
            raise ValueError("region must be a non-empty string")
        for name, value in (
            ("usd_per_gb_month", self.usd_per_gb_month),
            ("usd_per_iops_month_over_baseline", self.usd_per_iops_month_over_baseline),
            ("usd_per_mibps_month_over_baseline", self.usd_per_mibps_month_over_baseline),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be a finite number >= 0.0; got {value}")
        # These two are the OTHER operand of `monthly_cost`'s subtractions, and
        # they had the same int-field gap (#127): `included_iops = nan` or `inf`
        # both make `max(0, provisioned - included)` return 0, and `True` turns a
        # 3000-IOPS baseline into 1 — measured as $29.995 where $15.00 was due.
        _require_whole_number(self.included_iops, "included_iops", minimum=0)
        _require_whole_number(
            self.included_throughput_mibps, "included_throughput_mibps", minimum=0
        )


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

    def __post_init__(self) -> None:
        # `max(0, ...)` clamps at monthly_cost() lines 196 and 198 silently
        # turn a negative provisioned_iops or provisioned_throughput into a
        # zero cost line — omitting a real line item without raising. Guard
        # at the spec construction site instead.
        for name, value in (
            ("scale_tier", self.scale_tier),
            ("engine", self.engine),
            ("instance_type", self.instance_type),
        ):
            if not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name, value in (
            ("data_volume_gb", self.data_volume_gb),
            ("provisioned_iops", self.provisioned_iops),
            ("provisioned_throughput_mibps", self.provisioned_throughput_mibps),
        ):
            # `value < 0` closed only the negative branch of the harm this
            # docstring names. `max(0, nan)` is 0, so the NaN branch produced the
            # SAME silent zero cost line and walked straight through (#127).
            _require_whole_number(value, name, minimum=0)


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
    # Reject non-finite too: `nan <= 0` and `inf <= 0` are both False, so a
    # sign-only check would let nan qps yield `usd_per_query=nan` and inf qps a
    # fabricated `$0.00/query`. `run_under_load` emits `inf` throughput when
    # query time rounds to 0, and that round-trips through JSON into this API.
    if not math.isfinite(throughput_qps) or throughput_qps <= 0:
        raise ValueError(f"throughput_qps must be positive and finite, got {throughput_qps}")
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
