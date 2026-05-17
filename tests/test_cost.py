"""Tests for src/vector_bench/cost.py and the prices snapshot."""

from __future__ import annotations

import pytest

from vector_bench.cost import (
    HOURS_PER_MONTH,
    SECONDS_PER_MONTH,
    EbsGp3Price,
    InfraSpec,
    InstancePrice,
    PriceTable,
    UnknownInstanceTypeError,
    cost_per_query,
    monthly_cost,
)
from vector_bench.prices import SNAPSHOT_DATE, aws_us_east_1_snapshot

# ----- Fixtures -----------------------------------------------------------

FIXTURE_INSTANCES = {
    "m6i.large": InstancePrice("m6i.large", "us-east-1", 0.1000, 2, 8.0),
    "r6i.xlarge": InstancePrice("r6i.xlarge", "us-east-1", 0.2000, 4, 32.0),
}
FIXTURE_EBS = EbsGp3Price(
    region="us-east-1",
    usd_per_gb_month=0.10,
    usd_per_iops_month_over_baseline=0.01,
    usd_per_mibps_month_over_baseline=0.05,
)
FIXTURE_PRICES = PriceTable(
    snapshot_date="2026-01-01",
    instances=FIXTURE_INSTANCES,
    ebs=FIXTURE_EBS,
    source_url="https://example.invalid/aws-prices",
)


def _spec(
    *,
    tier: str = "1m",
    engine: str = "pgvector",
    instance_type: str = "m6i.large",
    data_volume_gb: int = 100,
    iops: int = 3000,
    mibps: int = 125,
) -> InfraSpec:
    return InfraSpec(
        scale_tier=tier,
        engine=engine,
        instance_type=instance_type,
        data_volume_gb=data_volume_gb,
        provisioned_iops=iops,
        provisioned_throughput_mibps=mibps,
    )


# ----- Constants ---------------------------------------------------------


def test_hours_per_month_is_730():
    """AWS billing convention; pinning here means our table reproduces
    against Cost Explorer without a 1.4% drift caused by alternative
    month conventions."""
    assert HOURS_PER_MONTH == 730
    assert SECONDS_PER_MONTH == 730 * 3600


# ----- monthly_cost ------------------------------------------------------


def test_monthly_cost_instance_only_when_ebs_at_baseline():
    spec = _spec(data_volume_gb=0, iops=3000, mibps=125)
    breakdown = monthly_cost(spec, FIXTURE_PRICES)
    assert breakdown.instance_usd_month == pytest.approx(0.1000 * 730)
    assert breakdown.storage_usd_month == 0.0
    assert breakdown.iops_usd_month == 0.0
    assert breakdown.throughput_usd_month == 0.0


def test_monthly_cost_storage_line_is_gb_times_rate():
    spec = _spec(data_volume_gb=200, iops=3000, mibps=125)
    breakdown = monthly_cost(spec, FIXTURE_PRICES)
    assert breakdown.storage_usd_month == pytest.approx(200 * 0.10)


def test_monthly_cost_iops_surcharge_only_above_baseline():
    spec = _spec(iops=2000, mibps=125)
    assert monthly_cost(spec, FIXTURE_PRICES).iops_usd_month == 0.0
    spec_at = _spec(iops=3000, mibps=125)
    assert monthly_cost(spec_at, FIXTURE_PRICES).iops_usd_month == 0.0
    spec_over = _spec(iops=6000, mibps=125)
    assert monthly_cost(spec_over, FIXTURE_PRICES).iops_usd_month == pytest.approx(
        (6000 - 3000) * 0.01
    )


def test_monthly_cost_throughput_surcharge_only_above_baseline():
    spec_below = _spec(mibps=100, iops=3000)
    assert monthly_cost(spec_below, FIXTURE_PRICES).throughput_usd_month == 0.0
    spec_at = _spec(mibps=125, iops=3000)
    assert monthly_cost(spec_at, FIXTURE_PRICES).throughput_usd_month == 0.0
    spec_over = _spec(mibps=500, iops=3000)
    assert monthly_cost(spec_over, FIXTURE_PRICES).throughput_usd_month == pytest.approx(
        (500 - 125) * 0.05
    )


def test_monthly_cost_full_stack_sums_correctly():
    spec = _spec(
        instance_type="r6i.xlarge",
        data_volume_gb=200,
        iops=6000,
        mibps=250,
    )
    breakdown = monthly_cost(spec, FIXTURE_PRICES)
    expected_total = (
        0.2000 * 730  # instance
        + 200 * 0.10  # storage
        + (6000 - 3000) * 0.01  # iops over baseline
        + (250 - 125) * 0.05  # throughput over baseline
    )
    assert breakdown.total_usd_month == pytest.approx(expected_total)


def test_monthly_cost_raises_on_unknown_instance_type():
    spec = _spec(instance_type="i-do-not-exist")
    with pytest.raises(UnknownInstanceTypeError) as exc:
        monthly_cost(spec, FIXTURE_PRICES)
    # The message lists known types so the operator can self-correct.
    assert "i-do-not-exist" in str(exc.value)
    assert "m6i.large" in str(exc.value)


# ----- cost_per_query ----------------------------------------------------


def test_cost_per_query_divides_monthly_by_qps_times_seconds():
    spec = _spec(data_volume_gb=50, iops=3000, mibps=125)
    qps = 1000.0
    cpq = cost_per_query(spec, FIXTURE_PRICES, qps)
    expected_monthly = monthly_cost(spec, FIXTURE_PRICES).total_usd_month
    expected_per_query = expected_monthly / (qps * SECONDS_PER_MONTH)
    assert cpq.usd_per_query == pytest.approx(expected_per_query)
    assert cpq.usd_per_million_queries == pytest.approx(expected_per_query * 1_000_000)
    assert cpq.throughput_qps == qps
    assert cpq.seconds_per_month == SECONDS_PER_MONTH


def test_cost_per_query_seconds_per_month_override_changes_result():
    spec = _spec(iops=3000, mibps=125)
    a = cost_per_query(spec, FIXTURE_PRICES, 100.0)
    b = cost_per_query(spec, FIXTURE_PRICES, 100.0, seconds_per_month=SECONDS_PER_MONTH // 2)
    # Halving the seconds-per-month doubles the per-query cost.
    assert b.usd_per_query == pytest.approx(a.usd_per_query * 2)


@pytest.mark.parametrize("bad_qps", [0.0, -1.0, -1e-12])
def test_cost_per_query_rejects_non_positive_qps(bad_qps: float):
    spec = _spec()
    with pytest.raises(ValueError, match="throughput_qps must be positive"):
        cost_per_query(spec, FIXTURE_PRICES, bad_qps)


def test_cost_per_query_rejects_non_positive_seconds_per_month():
    spec = _spec()
    with pytest.raises(ValueError, match="seconds_per_month must be positive"):
        cost_per_query(spec, FIXTURE_PRICES, 100.0, seconds_per_month=0)


def test_cost_per_query_carries_tier_and_engine_through():
    spec = _spec(tier="10m", engine="qdrant")
    cpq = cost_per_query(spec, FIXTURE_PRICES, 500.0)
    assert cpq.scale_tier == "10m"
    assert cpq.engine == "qdrant"


# ----- Default snapshot --------------------------------------------------


def test_default_snapshot_has_terraform_tier_instance_types():
    """Defensive: the snapshot must include every instance_type that
    `terraform/envs/benchmark/main.tf` uses, otherwise the bench's
    cost table can't be generated for that tier."""
    snap = aws_us_east_1_snapshot()
    for required in ("m6i.large", "r6i.xlarge", "r6i.4xlarge"):
        assert required in snap.instances, f"snapshot missing {required}"


def test_default_snapshot_carries_date_and_source_url():
    snap = aws_us_east_1_snapshot()
    assert snap.snapshot_date == SNAPSHOT_DATE
    assert snap.source_url.startswith("https://")
    assert "aws.amazon.com" in snap.source_url


def test_default_snapshot_returns_fresh_copy_each_call():
    """No global mutation; callers can edit their copy safely."""
    a = aws_us_east_1_snapshot()
    b = aws_us_east_1_snapshot()
    # Underlying dicts are distinct objects so a mutation of one doesn't
    # bleed into the other.
    assert a.instances is not b.instances
    a.instances["fake.size"] = InstancePrice("fake.size", "us-east-1", 9.99, 1, 1.0)
    assert "fake.size" not in b.instances


def test_default_snapshot_real_us_east_1_numbers_pass_sanity_checks():
    """Light-touch sanity: instance prices are positive and ordered."""
    snap = aws_us_east_1_snapshot()
    rates = {k: v.usd_per_hour for k, v in snap.instances.items()}
    for k, v in rates.items():
        assert v > 0, f"price for {k} must be positive, got {v}"
    # The three tier instances should be ordered by hourly rate.
    assert rates["m6i.large"] < rates["r6i.xlarge"] < rates["r6i.4xlarge"]
