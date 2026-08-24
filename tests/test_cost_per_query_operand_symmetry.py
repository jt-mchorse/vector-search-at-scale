"""Both factors of `monthly_queries` must be guarded, not just one.

`cost_per_query` computes ``monthly_queries = throughput_qps * seconds_per_month``
and divides a monthly bill by it. `throughput_qps` was checked for finiteness,
with a comment spelling out why: a sign-only check "would let nan qps yield
``usd_per_query=nan`` and inf qps a fabricated ``$0.00/query``".

`seconds_per_month` — the other factor of the same product — was sign-checked
only, so it produced exactly those two outcomes (#129).

The fix is in two parts, because the input domain alone doesn't cover it:

* `_require_whole_number` closes `nan`, `inf`, `True`, `2.5` and `"..."` — the
  rule the rest of this module already uses for its integer fields.
* `1e308` passes every input check (finite, whole, >= 1) and still underflows
  the division to `0.0`. No input rule catches that without inventing an
  arbitrary ceiling on the amortization window, so the *outcome* is guarded: a
  strictly positive monthly bill cannot amortize to exactly $0.00 per query.
"""

from __future__ import annotations

import math

import pytest

from vector_bench.cost import (
    SECONDS_PER_MONTH,
    EbsGp3Price,
    InfraSpec,
    InstancePrice,
    PriceTable,
    cost_per_query,
)

INFRA = InfraSpec("1m", "hnsw", "r6i.xlarge", 100, 3000, 125)


def _prices(usd_per_hour: float = 0.252, ebs_rate: float = 0.08) -> PriceTable:
    return PriceTable(
        "2026-01-01",
        {"r6i.xlarge": InstancePrice("r6i.xlarge", "us-east-1", usd_per_hour, 4, 32.0)},
        EbsGp3Price("us-east-1", ebs_rate, 0.005, 0.04, 3000, 125),
        "https://example.invalid/prices",
    )


PRICES = _prices()

# The two harms the guarded operand's comment enumerates, applied to each
# factor of the same product. Before #129 the `seconds_per_month` column of
# this table read `nan` and `0.0` where it now raises.
BAD_SECONDS = [
    ("nan", float("nan"), "finite"),
    ("+inf", float("inf"), "finite"),
    ("-inf", float("-inf"), "finite"),
    ("bool True", True, "not a bool"),
    ("non-integral float", 2.5, "whole number"),
    ("string", "2592000", "must be a number"),
    ("zero", 0, ">= 1"),
    ("negative", -1, ">= 1"),
]


@pytest.mark.parametrize(("label", "value", "expected_fragment"), BAD_SECONDS)
def test_seconds_per_month_rejects(label: str, value: object, expected_fragment: str) -> None:
    with pytest.raises(ValueError, match=expected_fragment):
        cost_per_query(INFRA, PRICES, 100.0, seconds_per_month=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(("label", "value", "_frag"), BAD_SECONDS[:3])
def test_the_sibling_operand_rejects_the_same_values(label: str, value: float, _frag: str) -> None:
    """The asymmetry is what this file exists to prevent coming back.

    Only the non-finite rows apply: `throughput_qps` is a float by nature, so
    `2.5` and `True` are legitimate there.
    """
    with pytest.raises(ValueError, match="throughput_qps"):
        cost_per_query(INFRA, PRICES, value)


def test_a_finite_whole_seconds_per_month_can_still_fabricate_a_zero() -> None:
    """`1e308` is finite, whole and >= 1 — the input domain cannot catch it.

    This is why the fix has an outcome guard as well as an input guard, and why
    copying the sibling's `isfinite` check across would not have been enough.
    """
    assert math.isfinite(1e308)
    assert (1e308).is_integer()
    with pytest.raises(ValueError, match="underflowed"):
        cost_per_query(INFRA, PRICES, 100.0, seconds_per_month=int(1e308))


def test_a_genuinely_free_configuration_still_reports_zero() -> None:
    """The outcome guard must not reject a truthful $0.00.

    Every price is zero here, so $0.00/query is the honest answer, not an
    underflow. The guard is conditioned on a strictly positive monthly bill for
    exactly this reason.
    """
    free = _prices(usd_per_hour=0.0, ebs_rate=0.0)
    result = cost_per_query(INFRA, free, 100.0)
    assert result.monthly_cost.total_usd_month == 0.0
    assert result.usd_per_query == 0.0


def test_ordinary_values_are_unchanged() -> None:
    result = cost_per_query(INFRA, PRICES, 100.0)
    assert result.seconds_per_month == SECONDS_PER_MONTH
    assert result.usd_per_query > 0.0
    assert result.usd_per_million_queries == pytest.approx(result.usd_per_query * 1_000_000)


@pytest.mark.parametrize("seconds", [1, 3600, 86_400, SECONDS_PER_MONTH, 31_536_000])
def test_positive_int_windows_all_work(seconds: int) -> None:
    """Including the partial-window amortization the docstring invites."""
    result = cost_per_query(INFRA, PRICES, 100.0, seconds_per_month=seconds)
    assert result.usd_per_query > 0.0
    assert result.seconds_per_month == seconds


def test_an_integral_float_is_accepted_a_fractional_one_is_not() -> None:
    """The module's rule is "whole number", not "is an `int`" — pinned as-is.

    `SECONDS_PER_MONTH / 8` is the natural way to express "an eighth of a
    month", which is exactly the partial-window amortization the docstring
    invites, and it lands as an integral float. `_require_whole_number` accepts
    it and rejects `2.5`, which is how every other integer field in this module
    behaves. Adopting a stricter rule here alone would make this parameter
    diverge from its siblings, so the boundary is pinned rather than moved.
    """
    eighth = SECONDS_PER_MONTH / 8
    assert isinstance(eighth, float)
    assert eighth.is_integer()
    result = cost_per_query(INFRA, PRICES, 100.0, seconds_per_month=eighth)  # type: ignore[arg-type]
    assert result.usd_per_query > 0.0

    with pytest.raises(ValueError, match="whole number"):
        cost_per_query(INFRA, PRICES, 100.0, seconds_per_month=eighth + 0.5)  # type: ignore[arg-type]


def test_a_large_but_representable_throughput_still_works() -> None:
    """The outcome guard must not fire on values that merely produce small numbers."""
    result = cost_per_query(INFRA, PRICES, 1e12)
    assert 0.0 < result.usd_per_query < 1e-10
