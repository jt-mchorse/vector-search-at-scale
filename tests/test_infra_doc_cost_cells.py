"""docs/infra.md's cost table and the README's teardown figure match the cost model (#280).

The infra.md "Per-tier on-demand cost" table was hand-written in #1 and never
re-derived. Measured on 75fa9e6 against `monthly_cost` over the Terraform tiers
(x3 instances, /730):

    tier   EBS $/hr  (doc)    Total $/hr  (doc)    8h      (doc)
    1m     0.0164    ~0.05    0.3044      ~0.34    2.4355  ~2.70
    10m    0.1479    ~0.27    0.9039      ~1.03    7.2316  ~8.20
    100m   0.7397    ~1.45    3.7637      ~4.47    30.1098 ~35.80

and the README Quickstart repeated the 100m `~$4.47/hr`. The doc's own change
protocol says "don't change a number without re-deriving it from the underlying
inputs"; nothing checked that it had been. These tests recompute every cell from
the same inputs `scripts/cost_table.py` uses, so a price, a tier, or a doc edit
that disagrees fails here instead of drifting.

Cells are cents-rounded with a `~`, so each is compared to the model within half
a cent. That admits only the correct cent: every stale value above was off by at
least 3.5 cents.
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
    SCALE_TIERS,
    parse_terraform_tiers,
)
from vector_bench.cost import HOURS_PER_MONTH, InfraSpec, monthly_cost  # noqa: E402
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402

_INFRA_MD = _REPO_ROOT / "docs" / "infra.md"
_README = _REPO_ROOT / "README.md"

#: One EC2 instance plus one data volume per backend (infra.md: "Each tier
#: deploys three EC2 instances of identical type — one per backend").
_INSTANCES_PER_TIER = 3
_WINDOW_HOURS = 8
_HALF_CENT = 0.005 + 1e-9


def _model() -> dict[str, dict[str, float]]:
    """Per-tier $/hr figures, computed the way the table claims to be."""
    tiers = parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))
    prices = aws_us_east_1_snapshot()
    out: dict[str, dict[str, float]] = {}
    for name in SCALE_TIERS:
        t = tiers[name]
        b = monthly_cost(
            InfraSpec(
                scale_tier=name,
                engine="any",
                instance_type=t.instance_type,
                data_volume_gb=t.data_volume_gb,
                provisioned_iops=t.provisioned_iops,
                provisioned_throughput_mibps=t.provisioned_throughput_mibps,
            ),
            prices,
        )
        ebs_month = b.storage_usd_month + b.iops_usd_month + b.throughput_usd_month
        total_hr = _INSTANCES_PER_TIER * b.total_usd_month / HOURS_PER_MONTH
        out[name] = {
            "instance_hr": prices.get_instance(t.instance_type).usd_per_hour,
            "ec2_hr": _INSTANCES_PER_TIER * b.instance_usd_month / HOURS_PER_MONTH,
            "ebs_hr": _INSTANCES_PER_TIER * ebs_month / HOURS_PER_MONTH,
            "total_hr": total_hr,
            "window": _WINDOW_HOURS * total_hr,
        }
    return out


def _table_rows(md: str, header_prefix: str) -> dict[str, list[str]]:
    """Rows of the first markdown table whose header starts with `header_prefix`,
    keyed by the backticked tier name in the first cell."""
    lines = md.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(header_prefix))
    rows: dict[str, list[str]] = {}
    for ln in lines[start + 2 :]:
        if not ln.startswith("|"):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells
    return rows


def _dollars(cell: str) -> list[float]:
    return [float(x) for x in re.findall(r"\$(\d+(?:\.\d+)?)", cell)]


def _cost_rows() -> dict[str, list[str]]:
    return _table_rows(_INFRA_MD.read_text(encoding="utf-8"), "| Tier  | EC2 (3× $/hr)")


def test_cost_table_lists_exactly_the_terraform_tiers() -> None:
    assert sorted(_cost_rows()) == sorted(SCALE_TIERS)


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_ec2_cell_matches_price_snapshot(tier: str) -> None:
    m = _model()[tier]
    per_instance, total = _dollars(_cost_rows()[tier][1])
    assert per_instance == pytest.approx(m["instance_hr"], abs=1e-9)
    assert total == pytest.approx(m["ec2_hr"], abs=_HALF_CENT)


@pytest.mark.parametrize(
    ("tier", "column", "key"),
    [
        (tier, column, key)
        for tier in SCALE_TIERS
        for column, key in ((2, "ebs_hr"), (3, "total_hr"), (4, "window"))
    ],
)
def test_cost_cell_matches_cost_model(tier: str, column: int, key: str) -> None:
    cell = _cost_rows()[tier][column]
    (value,) = _dollars(cell)
    expected = _model()[tier][key]
    assert abs(value - expected) <= _HALF_CENT, (
        f"docs/infra.md {tier} {key} cell {cell!r} != cost model {expected:.4f}; "
        "re-derive it from monthly_cost over the Terraform tiers"
    )


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_sizing_table_matches_terraform_tiers(tier: str) -> None:
    """The cost cells are priced off the Terraform tiers; the sizing table that
    the reader sees beside them names the same instance type and volume size."""
    t = parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))[tier]
    row = _table_rows(_INFRA_MD.read_text(encoding="utf-8"), "| Tier  | Vectors")[tier]
    assert row[3].strip("`") == t.instance_type
    assert row[6] == f"{t.data_volume_gb} GiB"


def test_readme_teardown_figure_matches_largest_tier_total() -> None:
    readme = _README.read_text(encoding="utf-8")
    found = re.findall(r"the (\w+) tier costs ~\$(\d+\.\d+)/hr", readme)
    assert found, "README Quickstart no longer quotes a per-tier $/hr"
    for tier, value in found:
        expected = _model()[tier]["total_hr"]
        assert abs(float(value) - expected) <= _HALF_CENT, (
            f"README says the {tier} tier costs ~${value}/hr; the cost model says {expected:.4f}"
        )
