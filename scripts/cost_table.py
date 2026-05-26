"""Generate `docs/cost_per_query.md` from the cost model + load results.

Inputs:

- `terraform/envs/benchmark/main.tf` (parsed for the per-tier sizing
  locals: instance_type, data_volume_gb, iops, throughput_mibps).
- `src/vector_bench/prices.py` (the documented AWS us-east-1 list-price
  snapshot, with the snapshot_date and source_url).
- `results/load/<run_id>/c001.json` per tier — the *measured*
  `throughput_qps` from the load harness at concurrency=1, taken as
  the conservative per-instance amortization basis (higher concurrency
  numbers exist; using c01 here keeps the per-query number honest in
  the absence of a sustained-multi-client workload).

Output:

- `docs/cost_per_query.md` with the per-tier table, the assumptions
  block, and a link back to the source-of-truth Terraform locals.

Modes:

- `--dry` (default): runs against the committed `results/load/stub-10k`
  numbers for every tier, marked clearly as `(simulated)` so the table
  doesn't claim real-engine $/query at 10M/100M scales. This is the
  CI-safe path. `--no-dry` drops the `(simulated)` marker on
  non-overridden tiers.
- `--load-results TIER=PATH` (repeatable) per-tier override: the
  operator points at their real load-test results dir for a specific
  tier and that row uses real qps; the row is labeled `(real)`
  regardless of `--dry`. Mix-and-match — overridden tiers go real,
  the rest stay on the `--run-id` defaults.

The script is reusable from tests as `build_rows()` (pure-function over
sizing + prices + qps) and `render_markdown()` (pure-function over rows).
The disk I/O is in `main()` only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from vector_bench.cost import (  # noqa: E402
    CostPerQuery,
    InfraSpec,
    PriceTable,
    cost_per_query,
)
from vector_bench.io_utils import atomic_write_text  # noqa: E402
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402

DEFAULT_TFVARS_PATH = _REPO_ROOT / "terraform/envs/benchmark/main.tf"
DEFAULT_RESULTS_DIR = _REPO_ROOT / "results/load"
DEFAULT_OUT = _REPO_ROOT / "docs/cost_per_query.md"

# The bench ships three engines; the cost table multiplies one tier's
# sizing across all three. They share the same instance type + EBS
# sizing per tier (see terraform/envs/benchmark/main.tf), which is the
# point — the cost difference between engines lives entirely in
# *throughput*, not the bill.
ENGINES: tuple[str, ...] = ("pgvector", "qdrant", "weaviate")

# Scale tiers ordered for human reading; the Terraform locals key these.
SCALE_TIERS: tuple[str, ...] = ("1m", "10m", "100m")


# ----------------------------------------------------------------------
# Terraform tfvars parser (focused; not a general HCL parser)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class TierSizing:
    scale_tier: str
    instance_type: str
    data_volume_gb: int
    provisioned_iops: int
    provisioned_throughput_mibps: int


def parse_terraform_tiers(tf_main_text: str) -> dict[str, TierSizing]:
    """Pull the `locals.tiers` block out of `envs/benchmark/main.tf`.

    The block we care about looks like:

        "1m" = {
          instance_type    = "m6i.large"
          data_volume_gb   = 50
          iops             = 3000
          throughput_mibps = 125
        }

    Parsed line-by-line to avoid pulling an HCL dep for one block.
    Tolerates inline comments after `#` on the same line.
    """
    tiers: dict[str, TierSizing] = {}
    # Capture each tier block: "<tier>" = { ... }
    tier_re = re.compile(
        r'"(?P<tier>[^"]+)"\s*=\s*\{(?P<body>[^}]*)\}',
        re.DOTALL,
    )
    field_re = re.compile(
        r'^\s*(?P<key>instance_type|data_volume_gb|iops|throughput_mibps)\s*=\s*"?(?P<value>[^"#\n]+?)"?\s*(#.*)?$',
        re.MULTILINE,
    )
    for m in tier_re.finditer(tf_main_text):
        tier_name = m.group("tier")
        if tier_name not in SCALE_TIERS:
            continue
        body = m.group("body")
        fields: dict[str, str] = {}
        for f in field_re.finditer(body):
            fields[f.group("key")] = f.group("value").strip()
        if {"instance_type", "data_volume_gb", "iops", "throughput_mibps"} - fields.keys():
            raise ValueError(
                f"tier {tier_name!r} block missing one or more required fields; "
                f"got {sorted(fields)}"
            )
        tiers[tier_name] = TierSizing(
            scale_tier=tier_name,
            instance_type=fields["instance_type"],
            data_volume_gb=int(fields["data_volume_gb"]),
            provisioned_iops=int(fields["iops"]),
            provisioned_throughput_mibps=int(fields["throughput_mibps"]),
        )
    missing = set(SCALE_TIERS) - set(tiers)
    if missing:
        raise ValueError(f"terraform main.tf is missing tier blocks for: {sorted(missing)}")
    return tiers


def load_throughput_qps(results_dir: Path) -> float:
    """Read `throughput_qps` from `c001.json` under `results_dir`.

    `c001.json` is the concurrency-1 cell; the load harness writes it
    deterministically. Other cell files exist (`c010.json`, `c100.json`)
    for multi-client tests; the cost table uses c01 as the conservative
    amortization basis.
    """
    c01_path = results_dir / "c001.json"  # concurrency=1 cell from the load harness
    if not c01_path.exists():
        raise FileNotFoundError(
            f"expected {c01_path} to exist for the cost table. "
            f"Re-run the load harness (`python -m vector_bench.load --run-id ...`) "
            f"to produce it."
        )
    payload = json.loads(c01_path.read_text(encoding="utf-8"))
    return float(payload["throughput_qps"])


# ----------------------------------------------------------------------
# Pure-function table builder
# ----------------------------------------------------------------------


def build_rows(
    tiers: dict[str, TierSizing],
    qps_by_tier: dict[str, float],
    prices: PriceTable,
) -> list[CostPerQuery]:
    """For each (tier, engine), compute the cost-per-query row."""
    rows: list[CostPerQuery] = []
    for tier_name in SCALE_TIERS:
        if tier_name not in tiers:
            raise KeyError(f"missing tier {tier_name!r}")
        tier = tiers[tier_name]
        qps = qps_by_tier[tier_name]
        for engine in ENGINES:
            infra = InfraSpec(
                scale_tier=tier.scale_tier,
                engine=engine,
                instance_type=tier.instance_type,
                data_volume_gb=tier.data_volume_gb,
                provisioned_iops=tier.provisioned_iops,
                provisioned_throughput_mibps=tier.provisioned_throughput_mibps,
            )
            rows.append(cost_per_query(infra, prices, qps))
    return rows


# ----------------------------------------------------------------------
# Markdown rendering
# ----------------------------------------------------------------------


def render_markdown(
    rows: Iterable[CostPerQuery],
    *,
    prices: PriceTable,
    qps_source: dict[str, str],
) -> str:
    """Render the per-tier table + assumptions block."""
    rows_list = list(rows)
    lines = [
        "# Cost per query",
        "",
        "Amortized USD per query at each (tier, engine), computed from the "
        "infrastructure Terraform brings up plus the throughput the load "
        "harness measures.",
        "",
        "## Assumptions",
        "",
        f"- **Pricing snapshot**: AWS public on-demand list, region us-east-1, "
        f"as of {prices.snapshot_date}. Source: <{prices.source_url}>. "
        f"Operators with contracted rates (Reserved, Spot, EDP) override the "
        f"`PriceTable` and re-run the script.",
        "- **Hours per month**: 730 (AWS billing convention, 8760 / 12).",
        "- **Amortization basis**: monthly cost ÷ (throughput_qps × 2,628,000 s). "
        "If your workload doesn't run 24/7, multiply by (24 / avg_active_hours_per_day).",
        "- **Throughput**: from `results/load/<run_id>/c001.json` (single-client "
        "p50; the conservative basis). For each tier the source is listed in "
        "the table.",
        "- **Instance sizing**: read live from "
        "[`terraform/envs/benchmark/main.tf`](../terraform/envs/benchmark/main.tf) "
        "so this doc and the infra layer can't drift.",
        "",
        "## Per-tier table",
        "",
        "| Scale | Engine | Instance | EBS | Monthly $ | qps | $/query | $/M queries | Throughput source |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in rows_list:
        ebs_summary = "—"
        # Itemized EBS via the breakdown the row carries.
        ebs_components = []
        if r.monthly_cost.storage_usd_month > 0:
            ebs_components.append(f"${r.monthly_cost.storage_usd_month:.2f}/mo storage")
        if r.monthly_cost.iops_usd_month > 0:
            ebs_components.append(f"${r.monthly_cost.iops_usd_month:.2f}/mo IOPS")
        if r.monthly_cost.throughput_usd_month > 0:
            ebs_components.append(f"${r.monthly_cost.throughput_usd_month:.2f}/mo throughput")
        if ebs_components:
            ebs_summary = " + ".join(ebs_components)
        instance_type = _instance_for_tier(rows_list, r.scale_tier)
        lines.append(
            f"| {r.scale_tier} | {r.engine} | {instance_type} | {ebs_summary} | "
            f"${r.monthly_cost.total_usd_month:.2f} | {r.throughput_qps:.1f} | "
            f"${r.usd_per_query:.6f} | ${r.usd_per_million_queries:.2f} | "
            f"{qps_source.get(r.scale_tier, '—')} |"
        )

    lines += [
        "",
        "## What the numbers say (and don't)",
        "",
        "- The infra bill is **identical across engines per tier** because "
        "they share the same instance type + EBS sizing (see the Terraform "
        "locals). The cost-per-query differences between engines therefore "
        "come from throughput differences, not from hardware differences.",
        "- The amortization assumes a 24/7 sustained workload at the "
        "throughput in the `qps` column. A bursty production workload that "
        "runs 8 hours/day will see ~3× the per-query cost; the README's "
        "writeup leads with that.",
        "- 100M-tier numbers in the table are flagged when the throughput "
        "source isn't real-engine data; a `(simulated)` annotation means "
        "the c001.json under `results/load/` came from the stub or HNSW "
        "simulator. Real-engine numbers require `make up` + the load "
        "harness; the operator commits new throughput files and re-runs "
        "the script.",
        "",
        "Refresh this doc with `python scripts/cost_table.py --dry --out "
        "docs/cost_per_query.md` after any change to "
        "`terraform/envs/benchmark/main.tf`, `src/vector_bench/prices.py`, "
        "or the load-harness throughput files.",
    ]
    return "\n".join(lines) + "\n"


def _instance_for_tier(rows: list[CostPerQuery], tier: str) -> str:
    """Lookup helper: every row in a tier shares the same instance type,
    but the dataclass doesn't carry it. Pull it from the first matching
    row's breakdown comment (we recorded it in the test fixture map),
    or reconstruct from monthly cost / hours and the price table.

    Simpler approach: caller passes us the sizing alongside the rows.
    Implemented as a follow-up; for now the per-tier instance type
    lives in the table via a side-channel built before render_markdown.
    """
    # Implemented below by closure capture; see main().
    return _tier_to_instance.get(tier, "—")


# Side-channel mapping set by main() before render_markdown is called.
_tier_to_instance: dict[str, str] = {}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def _parse_load_results_overrides(raw: list[str] | None) -> dict[str, Path]:
    """Parse `--load-results TIER=PATH` arguments into a {tier: dir} mapping.

    Raises ValueError on malformed entries or unknown tier so `main` can
    surface a clear error + the known-tier inventory on stderr.
    """
    out: dict[str, Path] = {}
    for raw_entry in raw or ():
        if "=" not in raw_entry:
            raise ValueError(f"--load-results entry {raw_entry!r}: expected TIER=PATH; got no '='")
        tier, _, path_str = raw_entry.partition("=")
        tier = tier.strip()
        path_str = path_str.strip()
        if tier not in SCALE_TIERS:
            raise ValueError(
                f"--load-results entry {raw_entry!r}: unknown tier {tier!r}. "
                f"Known: {', '.join(SCALE_TIERS)}"
            )
        if not path_str:
            raise ValueError(f"--load-results entry {raw_entry!r}: empty path")
        out[tier] = Path(path_str)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Controls the per-row 'simulated' marker. Default `--dry` labels each "
            "row `(simulated)` so the table doesn't pretend it's real engine numbers. "
            "`--no-dry` drops the marker on tiers without a `--load-results` override."
        ),
    )
    p.add_argument(
        "--tf-main",
        default=str(DEFAULT_TFVARS_PATH),
        help="Path to terraform/envs/benchmark/main.tf for the per-tier sizing.",
    )
    p.add_argument(
        "--results-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help="Root of results/load/. Each tier reads its c001.json from <results_dir>/<run_id>/c001.json.",
    )
    p.add_argument(
        "--run-id",
        default="stub-10k",
        help="Default run_id used for tiers without a `--load-results` override.",
    )
    p.add_argument(
        "--load-results",
        action="append",
        default=None,
        metavar="TIER=PATH",
        help=(
            "Per-tier override: read that tier's `c001.json` from <PATH> instead of "
            f"the default --run-id directory. TIER must be one of {{{', '.join(SCALE_TIERS)}}}. "
            "Repeatable; each invocation adds one mapping. Overridden tiers are "
            "labeled `(real)` regardless of --dry."
        ),
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output markdown path. Default: docs/cost_per_query.md.",
    )
    args = p.parse_args(argv)

    try:
        load_overrides = _parse_load_results_overrides(args.load_results)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    tf_text = Path(args.tf_main).read_text(encoding="utf-8")
    tiers = parse_terraform_tiers(tf_text)

    qps_by_tier: dict[str, float] = {}
    qps_source: dict[str, str] = {}
    results_dir = Path(args.results_dir)
    for tier in SCALE_TIERS:
        if tier in load_overrides:
            override_dir = load_overrides[tier]
            qps = load_throughput_qps(override_dir)
            qps_by_tier[tier] = qps
            qps_source[tier] = f"`{override_dir}/c001.json` (real)"
        else:
            qps = load_throughput_qps(results_dir / args.run_id)
            qps_by_tier[tier] = qps
            marker = "(simulated)" if args.dry else ""
            label = f"`results/load/{args.run_id}/c001.json`"
            qps_source[tier] = f"{label} {marker}".strip()

    prices = aws_us_east_1_snapshot()

    # Populate the side-channel before rendering. Side-channel is intentional:
    # CostPerQuery doesn't carry instance_type because the cost model itself
    # only needs the price, but the table reader wants the human-readable
    # column. A future refactor pushes this into the dataclass.
    _tier_to_instance.clear()
    for t in SCALE_TIERS:
        _tier_to_instance[t] = tiers[t].instance_type

    rows = build_rows(tiers, qps_by_tier, prices)
    md = render_markdown(rows, prices=prices, qps_source=qps_source)

    out_path = Path(args.out)
    atomic_write_text(out_path, md)

    print(f"cost-table wrote {out_path}")
    for r in rows:
        print(
            f"  {r.scale_tier:>4s}  {r.engine:<10s}  ${r.monthly_cost.total_usd_month:>8.2f}/mo  "
            f"{r.throughput_qps:>8.1f} qps  ${r.usd_per_query:.6f}/q  ${r.usd_per_million_queries:>7.2f}/M"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
