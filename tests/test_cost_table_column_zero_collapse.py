"""No cost column publishes a strictly positive value as zero (#148).

`#141` fixed `$/query`, which read `$0.000000` on all nine rows, and stated its
rule over the value rather than over dollars:

    "keep enough decimals that a strictly positive number never renders as all
     zeros"

The cell immediately to its right carried **the same quantity times 1e6** --
`usd_per_million_queries = per_query * 1_000_000`, one line in `cost.py` -- at a
fixed `.2f`. Above 5,638 qps on the committed 1m tier it published `$0.00` while
its neighbour read `$0.00000000470`, so the row contradicted itself. The
committed 1623.5 qps comes from the stub every row annotates `(simulated)`; a
real ANN engine on an `m6i.large` clears that cliff.

`throughput_qps` is the same shape from the other direction: a strictly positive
throughput below `0.05` renders `0.0`, and `cost_per_query` refuses `qps <= 0`
(#129), so a *small positive* throughput is exactly the reachable case.

The arms below pin the **contradiction**, never the threshold. A hard-coded
5,638 would restate the arithmetic the renderer performs and would go stale the
moment a price moves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import (  # noqa: E402
    DEFAULT_TFVARS_PATH,
    ENGINES,
    SCALE_TIERS,
    build_rows,
    format_qps,
    format_usd_per_query,
    parse_terraform_tiers,
    render_markdown,
    uniform_qps,
    uniform_source,
)
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402

#: The committed 1m-tier monthly bill and the amortization basis, so the
#: regimes below are this repo's own numbers rather than invented ones.
MONTHLY_1M_USD = 74.08
SECONDS_PER_MONTH = 2_628_000


def _per_query(qps: float, monthly: float = MONTHLY_1M_USD) -> float:
    return monthly / (qps * SECONDS_PER_MONTH)


# ----------------------------------------------------------------------
# The defect: the two dollar columns are one quantity and must agree
# ----------------------------------------------------------------------


@pytest.mark.parametrize("qps", [1623.5, 3000.0, 6000.0, 10_000.0, 50_000.0])
def test_the_two_dollar_columns_never_disagree_about_being_nonzero(qps: float) -> None:
    """A row cannot say `$0.00000000470` per query and `$0.00` per million.

    Derived from the pair, not from a threshold: whatever the numbers are, if one
    column shows a non-zero cost the other must too, because they are the same
    measurement in different units. Red against the pre-#148 `.2f` at 6,000 qps
    and above.
    """
    per_query = _per_query(qps)
    per_million = per_query * 1_000_000
    left = format_usd_per_query(per_query)
    right = format_usd_per_query(per_million)
    left_is_zero = float(left.lstrip("$")) == 0.0
    right_is_zero = float(right.lstrip("$")) == 0.0
    assert left_is_zero == right_is_zero, (
        f"at {qps} qps the row reads {left}/query and {right}/M — the same cost, "
        f"one column claiming it is free"
    )
    assert not right_is_zero, f"a strictly positive per-million cost rendered as {right}"


def test_the_cliff_the_issue_measured_is_gone() -> None:
    """The specific regime, by value. 6,000 qps on the committed 1m tier."""
    per_million = _per_query(6000.0) * 1_000_000
    assert per_million > 0.0
    assert f"${per_million:.2f}" == "$0.00"  # what shipped
    assert format_usd_per_query(per_million) == "$0.00470"


# ----------------------------------------------------------------------
# What must NOT move
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("qps", "monthly", "expected"),
    [
        (1623.5, 74.08, "$0.02"),
        (1623.5, 219.96, "$0.05"),
        (1623.5, 915.84, "$0.21"),
    ],
    ids=["1m", "10m", "100m"],
)
def test_the_three_committed_rows_are_byte_identical(
    qps: float, monthly: float, expected: str
) -> None:
    """Why `docs/cost_per_query.md` regenerates unchanged.

    Green on both trees, and that is the point: it is what rejects a fix that
    widens every cell instead of only the collapsing ones.
    """
    per_million = _per_query(qps, monthly) * 1_000_000
    assert f"${per_million:.2f}" == expected
    assert format_usd_per_query(per_million) == expected


def test_a_genuinely_free_tier_still_reads_as_cents() -> None:
    """`$0.00` is honest for a cost of exactly zero, and must stay narrow.

    Green on both trees; it rejects the neighbour that widens unconditionally
    and turns a real zero into a row of zeros claiming precision.
    """
    assert format_usd_per_query(0.0) == "$0.00"


# ----------------------------------------------------------------------
# The sibling column
# ----------------------------------------------------------------------


def test_a_small_positive_throughput_is_not_published_as_zero() -> None:
    """`0.0` qps beside a non-zero `$/query` is the same contradiction.

    `cost_per_query` refuses `qps <= 0`, so every throughput that reaches the
    table is strictly positive — which makes `0.0` in this column always a
    rendering artefact and never a measurement.
    """
    for value in (0.04, 0.004, 1e-6):
        narrow = f"{value:.1f}"
        rendered = format_qps(value)
        assert float(narrow) == 0.0, "the narrow form no longer collapses; this arm is stale"
        assert float(rendered) > 0.0, f"{value!r} rendered as {rendered}"
        # And it still *is* the measurement, not a nearby one.
        assert float(rendered) == pytest.approx(value, rel=1e-2)
    # One exact value as a control on the widening width itself.
    assert format_qps(0.04) == "0.0400"


def test_ordinary_throughputs_keep_the_narrow_form() -> None:
    """Every committed row, and the width the column is sized for."""
    for value in (1623.5, 0.1, 12.0, 45678.9):
        assert format_qps(value) == f"{value:.1f}"


# ----------------------------------------------------------------------
# The RENDERED row, not the formatter
# ----------------------------------------------------------------------
#
# The arms above call `format_usd_per_query` directly, and that function was
# already correct before #148 -- the defect was that `render_markdown` did not
# call it for this column. Built and run: a plain revert of the call sites left
# every one of those arms GREEN. A test of the function passes against a broken
# call site, which is the whole reason these three exist.


def _cells(markdown: str, column: str) -> list[str]:
    """The named column's cells, located by header rather than by index."""
    lines = [ln for ln in markdown.splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    assert column in header, f"no {column!r} column in {header}"
    idx = header.index(column)
    out = []
    for line in lines[2:]:
        parts = [c.strip() for c in line.strip("|").split("|")]
        if len(parts) == len(header):
            out.append(parts[idx])
    return out


def _render(qps: float) -> str:
    if not DEFAULT_TFVARS_PATH.exists():
        pytest.skip(f"{DEFAULT_TFVARS_PATH} not present")
    tiers = parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))
    prices = aws_us_east_1_snapshot()
    return render_markdown(
        build_rows(tiers, uniform_qps({t: qps for t in SCALE_TIERS}), prices),
        prices=prices,
        qps_source=uniform_source({t: "results/load/stub-10k/c001.json" for t in SCALE_TIERS}),
    )


def test_the_rendered_per_million_column_is_non_zero_past_the_cliff() -> None:
    """The defect, at the render site. Red on a call-site revert."""
    md = _render(6000.0)
    cells = _cells(md, "$/M queries")
    assert len(cells) == len(SCALE_TIERS) * len(ENGINES), cells
    for cell in cells:
        assert float(cell.lstrip("$")) > 0.0, (
            f"the $/M queries column rendered {cell!r} at 6,000 qps, while "
            f"$/query on the same row is non-zero"
        )


def test_the_rendered_row_does_not_contradict_itself() -> None:
    """Both dollar columns, read off the same rendered row."""
    md = _render(6000.0)
    per_query = _cells(md, "$/query")
    per_million = _cells(md, "$/M queries")
    assert len(per_query) == len(per_million)
    for left, right in zip(per_query, per_million, strict=True):
        assert (float(left.lstrip("$")) > 0.0) == (float(right.lstrip("$")) > 0.0), (
            f"one row reads {left}/query and {right}/M — the same cost, one "
            f"column claiming it is free"
        )


def test_the_committed_qps_renders_the_committed_cells() -> None:
    """The three shipped values, off the rendered table.

    Green on both trees, and this is what rejects the plausible wrong fix #141
    already named: a fixed `.6f` renders these as `$0.017363` / `$0.051554` /
    `$0.214655` and changes the committed document.
    """
    md = _render(1623.5)
    assert sorted(set(_cells(md, "$/M queries"))) == ["$0.02", "$0.05", "$0.21"]
    assert set(_cells(md, "qps")) == {"1623.5"}


def test_the_rendered_qps_column_is_non_zero_for_a_small_measurement() -> None:
    """The sibling column, also at the render site.

    Same reason as the two arms above: `format_qps` can be correct while
    `render_markdown` still interpolates `{qps:.1f}`. Built and run — a
    call-site revert leaves every formatter arm green and turns this one red.

    0.04 qps is a slow-but-real measurement: `cost_per_query` refuses `qps <= 0`
    (#129), so everything that reaches this column is strictly positive, which
    makes a rendered `0.0` always an artefact and never a measurement.
    """
    cells = _cells(_render(0.04), "qps")
    assert len(cells) == len(SCALE_TIERS) * len(ENGINES), cells
    for cell in cells:
        assert float(cell) > 0.0, f"the qps column rendered {cell!r} for a 0.04 qps run"
