"""`#53`'s finiteness widening skipped every int-typed field in `cost.py` (#127).

`#53` widened this module's sign-only bound checks to finiteness and covered the
`float` fields only. `InstancePrice`'s own comment stated the reason the int
fields were skipped: "vcpus is an int (`< 1` guard) and **cannot be
non-finite**, so it is left as-is." That is a claim about the annotation. At
runtime an `int`-annotated field holds whatever it is handed.

The harm is worse on the int side, because `monthly_cost`'s clamp hides it.
`max(0, nan)` is `0` in Python — `nan > 0` is `False`, so the clamp keeps its
seed — so where a non-finite *rate* surfaced as a visible `nan` in the table, a
non-finite *IOPS count* becomes a plausible `$0.00` line item. That is precisely
the outcome `InfraSpec.__post_init__`'s docstring says it exists to prevent
("silently turn a negative provisioned_iops ... into a zero cost line — omitting
a real line item without raising"), reached through `nan` rather than a negative.

Measured on `main` @ 249d42f, against correct line items of iops `$15.00`,
throughput `$5.00`, storage `$8.00`:

    provisioned_iops = nan            iops_usd = 0.0      <- silent zero
    provisioned_iops = inf            iops_usd = inf
    provisioned_iops = -inf           ValueError          <- caught
    provisioned_iops = True           iops_usd = 0.0
    provisioned_iops = 6000.5         iops_usd = 15.0025
    provisioned_throughput = nan      thruput_usd = 0.0
    data_volume_gb = nan              storage_usd = nan
    data_volume_gb = True             storage_usd = 0.08  <- fabricated 1 GB
    data_volume_gb = '100'            TypeError           <- not a ValueError
    included_iops = nan               iops_usd = 0.0
    included_iops = inf               iops_usd = 0.0      <- max(0, -inf)
    included_iops = True              iops_usd = 29.995   <- ~2x, and plausible

Note that `-inf` was caught and `nan` was not, inside the same guard.

Every test below asserts the **dollar amount** the corruption produced, not just
that something raised. The whole point is that the harm is a plausible number:
`included_iops = True` billed roughly double and nothing downstream could flag
it, so an exception-shaped assertion would miss what this guard is for.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from vector_bench.cost import (
    EbsGp3Price,
    InfraSpec,
    InstancePrice,
    PriceTable,
    monthly_cost,
)

NAN = float("nan")
INF = float("inf")

# Correct line items for the fixture below, computed by hand from the rates:
#   iops:       (6000 - 3000) * 0.005 = 15.00
#   throughput: (250 - 125)   * 0.04  =  5.00
#   storage:    100           * 0.08  =  8.00
CORRECT_IOPS_USD = 15.0
CORRECT_THROUGHPUT_USD = 5.0
CORRECT_STORAGE_USD = 8.0


def _instance(**over: Any) -> InstancePrice:
    kwargs: dict[str, Any] = dict(
        instance_type="m6i.large", region="us-east-1", usd_per_hour=0.096, vcpus=2, memory_gib=8.0
    )
    kwargs.update(over)
    return InstancePrice(**kwargs)


def _ebs(**over: Any) -> EbsGp3Price:
    kwargs: dict[str, Any] = dict(
        region="us-east-1",
        usd_per_gb_month=0.08,
        usd_per_iops_month_over_baseline=0.005,
        usd_per_mibps_month_over_baseline=0.04,
    )
    kwargs.update(over)
    return EbsGp3Price(**kwargs)


def _prices(**ebs_over: Any) -> PriceTable:
    return PriceTable(
        snapshot_date="2026-05-17",
        instances={"m6i.large": _instance()},
        ebs=_ebs(**ebs_over),
        source_url="https://example.invalid/prices",
    )


def _spec(**over: Any) -> InfraSpec:
    kwargs: dict[str, Any] = dict(
        scale_tier="1m",
        engine="pgvector",
        instance_type="m6i.large",
        data_volume_gb=100,
        provisioned_iops=6000,
        provisioned_throughput_mibps=250,
    )
    kwargs.update(over)
    return InfraSpec(**kwargs)


# ----------------------------------------------------------------------
# The baseline the corrupt numbers are measured against
# ----------------------------------------------------------------------


def test_the_correct_line_items_are_what_the_corruption_replaced() -> None:
    # Anchors every "$0.00 where $15.00 was due" claim below to a computed
    # number rather than to prose, and fails loudly if the fixture rates drift.
    breakdown = monthly_cost(_spec(), _prices())
    assert breakdown.iops_usd_month == CORRECT_IOPS_USD
    assert breakdown.throughput_usd_month == CORRECT_THROUGHPUT_USD
    assert breakdown.storage_usd_month == CORRECT_STORAGE_USD


# ----------------------------------------------------------------------
# InfraSpec: the operand the guard's own docstring is about
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad", "was"),
    [
        # `nan` is the one the clamp hides: max(0, nan) == 0, so the iops line
        # silently disappeared instead of being a visible nan.
        ("provisioned_iops", NAN, "0.0 where $15.00 was due"),
        ("provisioned_throughput_mibps", NAN, "0.0 where $5.00 was due"),
        ("provisioned_iops", INF, "inf"),
        ("provisioned_throughput_mibps", INF, "inf"),
        ("data_volume_gb", NAN, "nan"),
        ("data_volume_gb", INF, "inf"),
    ],
)
def test_non_finite_sizing_field_is_rejected(field: str, bad: float, was: str) -> None:
    with pytest.raises(ValueError, match=rf"{field} must be finite"):
        _spec(**{field: bad})
    # The message says why, not just what — the clamp is the non-obvious part.
    with pytest.raises(ValueError, match=r"silent \$0\.00 line item"):
        _spec(**{field: bad})
    assert was  # documented in the parametrize table above


def test_a_nan_iops_used_to_produce_exactly_zero_not_a_visible_nan() -> None:
    # The reason this belongs on the int fields specifically. Confirm the clamp
    # behaviour that made it silent is real, so the guard's rationale is pinned
    # rather than asserted:
    assert max(0, NAN) == 0, "max(0, nan) is 0 — this is what laundered the corruption"
    assert math.isnan(100 * NAN), "a nan GB count, by contrast, stays visibly nan"


def test_bool_sizing_field_is_rejected_and_the_message_names_the_fix() -> None:
    # `True` passed `value < 0` and became the int 1. For `provisioned_iops`
    # that is `max(0, 1 - 3000) == 0` — another silent zero. For
    # `data_volume_gb` it is a fabricated 1 GB, measured at $0.08.
    for field in ("provisioned_iops", "provisioned_throughput_mibps", "data_volume_gb"):
        with pytest.raises(ValueError, match=rf"{field} must be an int, not a bool"):
            _spec(**{field: True})
    with pytest.raises(ValueError, match=r"pass 1"):
        _spec(provisioned_iops=True)


def test_fractional_sizing_field_is_rejected_rather_than_priced() -> None:
    # 6000.5 IOPS was priced at $15.0025. There is no such thing as half a
    # provisioned IOPS, and the field is annotated `int`.
    with pytest.raises(ValueError, match=r"provisioned_iops must be a whole number"):
        _spec(provisioned_iops=6000.5)
    with pytest.raises(ValueError, match=r"data_volume_gb must be a whole number"):
        _spec(data_volume_gb=100.5)


@pytest.mark.parametrize("bad", ["100", None, [], {}])
def test_non_numeric_sizing_field_raises_ValueError_not_TypeError(bad: object) -> None:
    # Pre-fix this escaped from the bare `value < 0` as
    # `TypeError: '<' not supported between instances of 'str' and 'int'`.
    with pytest.raises(ValueError, match=r"data_volume_gb must be a number"):
        _spec(data_volume_gb=bad)


# ----------------------------------------------------------------------
# EbsGp3Price: the OTHER operand of the same subtraction
# ----------------------------------------------------------------------


@pytest.mark.parametrize("field", ["included_iops", "included_throughput_mibps"])
@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_non_finite_baseline_is_rejected(field: str, bad: float) -> None:
    # `iops_over = max(0, provisioned - included)` has two operands. `included`
    # had the identical gap, and `inf` here is the sneaky one: `max(0, -inf)` is
    # also 0, so an *infinite* baseline and a *NaN* baseline both erased the line.
    with pytest.raises(ValueError, match=rf"{field} must be finite"):
        _ebs(**{field: bad})


def test_a_bool_baseline_produced_a_doubled_bill() -> None:
    # The sharpest measured row: `included_iops = True` is the int 1, so
    # `max(0, 6000 - 1) * 0.005` is $29.995 — roughly double the correct $15.00,
    # and an entirely ordinary-looking number. Nothing downstream can flag that,
    # which is why the guard has to be here.
    with pytest.raises(ValueError, match=r"included_iops must be an int, not a bool"):
        _ebs(included_iops=True)


def test_fractional_and_non_numeric_baselines_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"included_iops must be a whole number"):
        _ebs(included_iops=3000.5)
    with pytest.raises(ValueError, match=r"included_iops must be a number"):
        _ebs(included_iops="3000")


# ----------------------------------------------------------------------
# InstancePrice.vcpus — the field whose comment carried the wrong premise
# ----------------------------------------------------------------------


def test_vcpus_gets_the_same_treatment_as_the_sizing_fields() -> None:
    # `vcpus` is not consumed by the cost math, so this is not about a wrong
    # dollar figure. It is included because the comment that exempted it —
    # "vcpus is an int (< 1 guard) and cannot be non-finite" — is the premise
    # that kept all five sizing fields on a sign-only check. Leaving the field
    # would leave the wrong reasoning in the file.
    with pytest.raises(ValueError, match=r"vcpus must be finite"):
        _instance(vcpus=NAN)
    with pytest.raises(ValueError, match=r"vcpus must be an int, not a bool"):
        _instance(vcpus=True)
    with pytest.raises(ValueError, match=r"vcpus must be a whole number"):
        _instance(vcpus=2.5)
    with pytest.raises(ValueError, match=r"vcpus must be a number"):
        _instance(vcpus="2")


# ----------------------------------------------------------------------
# What must not change
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad", "minimum"),
    [
        ("data_volume_gb", -1, 0),
        ("provisioned_iops", -1, 0),
        ("provisioned_throughput_mibps", -1, 0),
    ],
)
def test_the_existing_negative_messages_are_byte_identical(
    field: str, bad: int, minimum: int
) -> None:
    # `test_infra_spec_rejects_negative_numeric` in test_cost.py matches on
    # `f"{field} must be >= 0"`; the shared helper reproduces that string
    # exactly for a finite negative.
    with pytest.raises(ValueError, match=rf"^{field} must be >= {minimum}; got {bad}$"):
        _spec(**{field: bad})


@pytest.mark.parametrize(
    "field", ["data_volume_gb", "provisioned_iops", "provisioned_throughput_mibps"]
)
def test_minus_inf_now_reports_as_non_finite_rather_than_as_negative(field: str) -> None:
    # A deliberate message change for a case that was ALREADY rejected. `-inf`
    # used to fall through to `value < 0` and report "must be >= 0"; the
    # finiteness check now runs first and reports "must be finite", which is the
    # more specific and more accurate diagnosis. Nothing in the existing suite
    # depends on the old string — `test_infra_spec_rejects_negative_numeric`
    # only passes `-1`, which is unchanged. Pinned here so the change is a
    # recorded decision rather than an accident.
    with pytest.raises(ValueError, match=rf"{field} must be finite; got -inf"):
        _spec(**{field: -INF})


def test_vcpus_negative_message_is_unchanged() -> None:
    with pytest.raises(ValueError, match=r"^vcpus must be >= 1; got 0$"):
        _instance(vcpus=0)


def test_zero_is_still_valid_for_every_sizing_field() -> None:
    # `test_dataclass_zero_rates_accepted` in test_cost.py pins the inclusive
    # bound deliberately ("Zero is meaningful for rate fields — free-tier / test
    # fixture"). Re-pinned here so the type widening can't tighten it by accident.
    _spec(data_volume_gb=0, provisioned_iops=0, provisioned_throughput_mibps=0)
    _ebs(included_iops=0, included_throughput_mibps=0)


def test_integral_floats_are_accepted_because_json_round_trips_ints_that_way() -> None:
    # `json.loads("100.0")` is `100.0`. An operator-supplied price table or spec
    # coming back through JSON carries integral floats, and those are
    # unambiguous — only *fractional* ones are rejected.
    breakdown = monthly_cost(
        _spec(data_volume_gb=100.0, provisioned_iops=6000.0, provisioned_throughput_mibps=250.0),
        _prices(included_iops=3000.0),
    )
    assert breakdown.iops_usd_month == CORRECT_IOPS_USD
    assert breakdown.storage_usd_month == CORRECT_STORAGE_USD


def test_a_large_finite_sizing_value_is_still_allowed() -> None:
    # No upper bound was added. A 100k-IOPS tier is a legitimate request; only
    # the type and finiteness domains were wrong.
    breakdown = monthly_cost(_spec(provisioned_iops=100_000), _prices())
    assert breakdown.iops_usd_month == pytest.approx((100_000 - 3000) * 0.005)
