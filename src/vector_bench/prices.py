"""Documented AWS pricing snapshot for the cost model (issue #5).

This module is the **default** `PriceTable` callers get if they don't
pass one explicitly. The numbers are AWS's *public on-demand list
prices* for `us-east-1` as of the `snapshot_date` below. They are not
contracted prices, not Reserved or Spot prices, and not a forecast.
The README explains the difference; this module just records the
numbers.

The rule for editing this file: do not change a price unless you
also (a) update the `snapshot_date`, (b) update `source_url` if the
underlying page moved, and (c) re-run `scripts/cost_table.py` so the
committed `docs/cost_per_query.md` matches what the model now
produces. The CI test `test_cost_table_doc_matches_committed`
enforces (c).
"""

from __future__ import annotations

from vector_bench.cost import EbsGp3Price, InstancePrice, PriceTable

# Snapshot date for all the prices in this file. Bump together with
# the prices themselves.
SNAPSHOT_DATE = "2026-05-17"

# AWS publishes per-region per-instance hourly rates; this snapshot is
# the us-east-1 figure. Operators in other regions instantiate their
# own PriceTable.
SOURCE_URL = "https://aws.amazon.com/ec2/pricing/on-demand/"


_DEFAULT_PRICES: dict[str, InstancePrice] = {
    "m6i.large": InstancePrice(
        instance_type="m6i.large",
        region="us-east-1",
        usd_per_hour=0.0960,
        vcpus=2,
        memory_gib=8.0,
    ),
    "r6i.xlarge": InstancePrice(
        instance_type="r6i.xlarge",
        region="us-east-1",
        usd_per_hour=0.2520,
        vcpus=4,
        memory_gib=32.0,
    ),
    "r6i.4xlarge": InstancePrice(
        instance_type="r6i.4xlarge",
        region="us-east-1",
        usd_per_hour=1.0080,
        vcpus=16,
        memory_gib=128.0,
    ),
}


_DEFAULT_EBS = EbsGp3Price(
    region="us-east-1",
    usd_per_gb_month=0.08,
    usd_per_iops_month_over_baseline=0.005,
    usd_per_mibps_month_over_baseline=0.040,
)


def aws_us_east_1_snapshot() -> PriceTable:
    """The committed snapshot. Returns a fresh `PriceTable` each call
    so callers can mutate their copy without side effects."""
    return PriceTable(
        snapshot_date=SNAPSHOT_DATE,
        instances=dict(_DEFAULT_PRICES),
        ebs=_DEFAULT_EBS,
        source_url=SOURCE_URL,
    )


__all__ = [
    "SNAPSHOT_DATE",
    "SOURCE_URL",
    "aws_us_east_1_snapshot",
]
