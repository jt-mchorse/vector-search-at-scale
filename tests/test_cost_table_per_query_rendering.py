"""A positive `usd_per_query` must never render as a string of zeros (#141).

`docs/cost_per_query.md` is titled "Cost per query" and opens with "Amortized
USD per query at each (tier, engine)". Its `$/query` column read **`$0.000000`
on all nine rows**, and the regenerated output was byte-identical to the
committed doc — so that was the reproducible state, not a stale artifact.

Both render sites used `f"${x:.6f}"`. Six decimals cannot hold this quantity;
the committed snapshot's own three tiers are::

    1m    $ 74.08/mo / 4,266,558,000 queries = $0.000000017363/query
    10m   $219.96/mo                         = $0.000000051554/query
    100m  $915.84/mo                         = $0.000000214655/query

**And this repo already treats that exact string as the name of a defect.**
`test_cost_per_query_operand_symmetry.py`'s own module docstring says the #129
guard exists because a sign-only check

    "would let nan qps yield ``usd_per_query=nan`` and inf qps a fabricated
     ``$0.00/query``"

so #129 hardened the *computation* against producing a fake zero while the
*presentation* produced one for every real value. Every existing assertion in
this repo is on the computed float; the harm #129 names is a **string**. That
gap is what this file closes.

The lock is stated over the rendered text and over the *value*, not over a
format string. `test_plot_hnsw_frontier.py` learned that lesson the expensive
way in #139: it grepped `inspect.getsource` for the literal
`"math.isfinite(...)"`, which pinned the guard's SPELLING rather than its
DOMAIN — and could never have caught the hole that motivated the fix, because
the literal it required was precisely the expression that accepted `True`.
"""

from __future__ import annotations

import re
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
    format_usd_per_query,
    parse_terraform_tiers,
    render_markdown,
    uniform_qps,
    uniform_source,
)
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402


def _real_tier_sizings():
    """The REAL Terraform sizings, not the sample string the sibling tests use.

    The rendered-artifact assertions below have to match the committed doc, so
    they read the same source of truth `main()` does.
    """
    if not DEFAULT_TFVARS_PATH.exists():
        pytest.skip(f"{DEFAULT_TFVARS_PATH} not present")
    return parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))


DOC = _REPO_ROOT / "docs" / "cost_per_query.md"

# Every value must render with at least one non-zero digit. `1e-10` is the
# smallest magnitude this repo's own suite contemplates —
# `test_cost_per_query_operand_symmetry.py` asserts `0.0 < usd_per_query < 1e-10`
# for one regime — and the rows below it are what make any FIXED-width format
# falsifiable, which is why this is a significant-figures rule.
POSITIVE_VALUES = [
    123.456,
    1.0,
    0.5,
    0.01,
    0.004,
    1e-4,
    2.14655e-07,  # the committed 100m tier
    5.1554e-08,  # the committed 10m tier
    1.7363e-08,  # the committed 1m tier
    1e-10,
    1e-11,
    1e-15,
    5e-323,  # denormal
]


def _has_nonzero_digit(rendered: str) -> bool:
    return any(ch in "123456789" for ch in rendered)


@pytest.mark.parametrize("value", POSITIVE_VALUES, ids=[repr(v) for v in POSITIVE_VALUES])
def test_a_positive_per_query_cost_never_renders_as_zeros(value: float) -> None:
    """The rule, over the value rather than over a format string.

    Any fixed `.Nf` fails this table at some row: `.6f` fails from 2.1e-07
    down, and `.12f` still fails at 1e-15 and at the denormal. That is the
    point of stating it this way.
    """
    rendered = format_usd_per_query(value)
    assert _has_nonzero_digit(rendered), (
        f"{value!r} rendered as {rendered!r} — a strictly positive per-query "
        "cost read as zero, which is the exact string #129 calls a fabricated "
        "$0.00/query"
    )
    # And it stays a readable table cell.
    assert len(rendered) <= 16, f"{value!r} rendered {len(rendered)} chars: {rendered!r}"


def test_exactly_zero_is_the_one_input_that_may_render_zeros() -> None:
    """The boundary the guard is stated around.

    A genuinely zero per-query cost is a real answer, and it is the one input
    for which a string of zeros is honest. Without this row the rule above
    could be satisfied by refusing to render zero at all.
    """
    assert format_usd_per_query(0.0) == "$0.00"
    assert not _has_nonzero_digit(format_usd_per_query(0.0))


def test_a_fixed_six_decimal_format_fails_this_table() -> None:
    """The neighbour, run rather than argued.

    `.6f` is what shipped. Naming the rows it destroys keeps the reason for a
    significant-figures rule in the suite rather than in a commit message.
    """
    destroyed = [v for v in POSITIVE_VALUES if not _has_nonzero_digit(f"${v:.6f}")]
    assert destroyed, "the probe table no longer contains any value .6f zeroes out"
    # All three committed tiers are in there.
    assert 2.14655e-07 in destroyed
    assert 5.1554e-08 in destroyed
    assert 1.7363e-08 in destroyed


def test_a_wider_fixed_format_also_fails_it() -> None:
    """And so does the obvious over-correction.

    Bumping `.6f` to a wider fixed precision is the plausible wrong fix. `.12f`
    is generous and still reads zero at a magnitude this repo's own tests
    contemplate, which is why the shipped rule counts significant figures.
    """
    destroyed = [v for v in POSITIVE_VALUES if not _has_nonzero_digit(f"${v:.12f}")]
    assert destroyed, ".12f zeroes out no row in the probe table"
    assert all(v < 1e-12 for v in destroyed), destroyed


# ----- the rendered artifact, not the formatter ---------------------------


def _per_query_cells(markdown: str) -> list[str]:
    """The `$/query` column of every data row, read out of the rendered table.

    Derived from the header rather than a fixed index, so a column inserted
    before it cannot make this test silently read the wrong cell.
    """
    lines = [ln for ln in markdown.splitlines() if ln.startswith("|")]
    assert len(lines) >= 3, "no markdown table found"
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    assert "$/query" in header, f"no $/query column in {header}"
    idx = header.index("$/query")
    cells = []
    for line in lines[2:]:  # skip header and separator
        parts = [c.strip() for c in line.strip("|").split("|")]
        if len(parts) == len(header):
            cells.append(parts[idx])
    return cells


def test_every_rendered_row_of_the_real_table_shows_a_non_zero_cost() -> None:
    tiers = _real_tier_sizings()
    prices = aws_us_east_1_snapshot()
    qps_by_tier = {t: 1623.5 for t in SCALE_TIERS}
    md = render_markdown(
        build_rows(tiers, uniform_qps(qps_by_tier), prices),
        prices=prices,
        qps_source=uniform_source({t: "results/load/stub-10k/c001.json" for t in SCALE_TIERS}),
    )
    cells = _per_query_cells(md)
    assert len(cells) == len(SCALE_TIERS) * len(ENGINES), cells
    for cell in cells:
        assert _has_nonzero_digit(cell), (
            f"the $/query column rendered {cell!r} — the column this document "
            "is named after read as zero"
        )


def test_the_three_tiers_are_distinguishable_in_the_per_query_column() -> None:
    """Non-zero is the floor; distinguishable is the point.

    `docs/cost_per_query.md` and `README.md` both say "the cost-per-query
    differences between engines therefore come from *throughput* differences",
    and a column holding one repeated value supports no such reading at all.
    Engines share the stub qps here, so the differences that must be visible
    are the ones between TIERS.
    """
    tiers = _real_tier_sizings()
    prices = aws_us_east_1_snapshot()
    md = render_markdown(
        build_rows(tiers, uniform_qps({t: 1623.5 for t in SCALE_TIERS}), prices),
        prices=prices,
        qps_source=uniform_source({t: "x" for t in SCALE_TIERS}),
    )
    assert len(set(_per_query_cells(md))) == len(SCALE_TIERS), (
        "the $/query column does not distinguish the three scale tiers: "
        f"{sorted(set(_per_query_cells(md)))}"
    )


def test_the_committed_doc_is_regenerated() -> None:
    """The artifact on disk, not just the renderer.

    #141 was found by running the shipped generator and reading the numbers;
    the doc was byte-identical to its regeneration and still wrong. This pins
    that the committed file carries the fix rather than the fix living only in
    code someone has to remember to re-run.
    """
    text = DOC.read_text(encoding="utf-8")
    cells = _per_query_cells(text)
    assert cells, "no data rows in the committed doc"
    for cell in cells:
        assert _has_nonzero_digit(cell), f"committed doc still shows {cell!r}"
    assert "$0.000000 " not in text, "the committed doc still contains a zeroed $/query cell"
    # And the zeroed spelling is gone from the prose too, not just the table.
    assert not re.search(r"\$0\.0{6,}\b", text), "a zeroed dollar figure survives in the doc"
