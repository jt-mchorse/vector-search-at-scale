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

- By default every tier reads the committed `results/load/stub-10k`
  numbers, and each row's marker says what that measurement is — the
  stub run records `backend: "stub"`, so its rows read `(simulated)`
  and the table doesn't claim real-engine $/query at 10M/100M scales.
  This is the CI-safe path.
- `--load-results TIER=PATH` (repeatable) per-tier override: the
  operator points at their real load-test results dir for a specific
  tier and that row uses the measured qps. The marker comes from the
  file (#144): `(real)` for the engine it was measured on, "measured
  on X, not Y" for the other two. A run whose `workload.n_vectors` is
  not the tier's own count says so in the same marker (#291):
  `(real, measured at n=1,000, not 100m)`. Mix-and-match — overridden
  tiers use their files, the rest stay on the `--run-id` defaults.
- `--dry` / `--no-dry` **has no effect** (#156). It selected the marker
  before #144 made provenance a property of the data; it is still
  accepted so the documented `--dry` commands keep working, and
  `--no-dry` says on stderr that it changed nothing.

The script is reusable from tests as `build_rows()` (pure-function over
sizing + prices + qps) and `render_markdown()` (pure-function over rows).
The disk I/O is in `main()` only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
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
    UnknownInstanceTypeError,
    cost_per_query,
)
from vector_bench.io_utils import atomic_write_text, loads_json  # noqa: E402
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

# The corpus size each tier is named for and sized for (docs/infra.md: 1x10^6,
# 10x10^6, 100x10^6 vectors). A measurement is that tier's only when its
# `workload.n_vectors` is this count (#291).
TIER_N_VECTORS: dict[str, int] = {"1m": 1_000_000, "10m": 10_000_000, "100m": 100_000_000}


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
            f"expected {c01_path} to exist for the cost table: the concurrency-1 "
            f"cell of a load run. Produce it with the load harness, keeping "
            f"concurrency 1 in the run (the default `--concurrency 1,10,100` does): "
            f"`vector-bench load --backend <backend> --n <N> --run-id <run_id> "
            f"--results-dir <dir>` writes `<dir>/<run_id>/c001.json`."
            # It named `python -m vector_bench.load`, which has no __main__ and
            # exits 0 having written nothing; and a run without concurrency 1
            # writes no c001.json at all (#176).
        )
    # The cell must belong to this run (#182): a `load --force` rerun without
    # concurrency 1 used to leave the previous run's `c001.json` in place, and
    # it was amortized over as this run's throughput. `matrix.json` lists the
    # run's cells, so a `c001.json` it does not list is stale.
    matrix_path = results_dir / "matrix.json"
    if matrix_path.exists():
        matrix = loads_json(matrix_path.read_text(encoding="utf-8"))
        # A `matrix.json` that is not an object raised AttributeError, past every
        # exit-2 arm in `main` (#217).
        if not isinstance(matrix, dict):
            raise ValueError(f"{matrix_path} must be a JSON object; got {type(matrix).__name__}")
        cells = matrix.get("cells", [])
        levels = sorted(c.get("concurrency") for c in cells if isinstance(c, dict))
        if 1 not in levels:
            raise FileNotFoundError(
                f"{c01_path} is not part of this run: {matrix_path} lists concurrency "
                f"{levels}, no concurrency-1 cell, so the c001.json beside it is left "
                f"over from an earlier run. Re-run the load harness with concurrency 1 "
                f"in `--concurrency` to measure the cost-table basis."
            )
    payload = loads_json(c01_path.read_text(encoding="utf-8"))
    qps = payload["throughput_qps"]
    # The value as the file holds it: a real number, not a bool and not a string
    # (#243). `bool` subclasses `int`, so `float(True)` is `1.0` -- a JSON `true`
    # would amortize the cost table over a fabricated 1.0 qps (handoff §10). And
    # `float()` *accepts* a numeric string, padding and `_` separators included,
    # so `" 1_623.5\n"` was published as 1623.5 qps at exit 0: the coercion #241
    # removed from `plot_latency._load_matrix` for the same field of the same
    # file. Both refusals reach `main`'s `(TypeError, ValueError, KeyError)` arm
    # and become a clean exit 2 (#115). Non-finite and non-positive values are
    # refused by `cost_per_query`.
    if isinstance(qps, bool) or not isinstance(qps, (int, float)):
        raise ValueError(f"throughput_qps must be a number; got {type(qps).__name__} {qps!r}")
    return float(qps)


def load_throughput_backend(results_dir: Path) -> str | None:
    """The engine that produced `c001.json`, or ``None`` if it does not say.

    The load harness records which backend it ran against, and the cost table
    used to ignore the field entirely: it read one `c001.json` per *tier* and
    published that throughput as all three engines' (#144). With
    `--load-results`, a real pgvector measurement was republished as qdrant's
    and weaviate's and labelled ``(real)`` — a benchmark number attributed to
    something that did not produce it (handoff §10).

    Measured on the unguarded version, a `c001.json` with
    ``backend: "pgvector"`` at 842 qps passed for the 1m tier::

        | 1m | pgvector | ... | 842.0 | $0.0000000335 | ... (real) |
        | 1m | qdrant   | ... | 842.0 | $0.0000000335 | ... (real) |
        | 1m | weaviate | ... | 842.0 | $0.0000000335 | ... (real) |

    Returns the raw string rather than validating it against `ENGINES`: the
    committed stub run records ``"stub"``, which is a truthful answer that names
    no engine, and `_provenance_marker` is where the comparison happens.
    """
    payload = loads_json((results_dir / "c001.json").read_text(encoding="utf-8"))
    backend = payload.get("backend")
    return backend if isinstance(backend, str) else None


def load_n_vectors(results_dir: Path) -> int | None:
    """The corpus size `c001.json` was measured at, or ``None`` if it does not say.

    The cost table read `throughput_qps` and `backend` and never this field, so
    a run at *any* scale was published as the tier's measurement (#291).
    Measured with a real local pgvector run at ``--n 1000``::

        | 100m | pgvector | r6i.4xlarge | ... | 4497.1 | $0.0000000775 | ... (real) |

    The load harness always writes ``workload.n_vectors``; ``None`` is a file
    that does not record it, and the marker says so rather than guessing. A
    value that is there but is not an integer is refused, as `throughput_qps`
    is (#243): `bool` subclasses `int`, and a string is not a count.
    """
    payload = loads_json((results_dir / "c001.json").read_text(encoding="utf-8"))
    workload = payload.get("workload")
    if workload is None:
        return None
    if not isinstance(workload, dict):
        raise ValueError(f"workload must be a JSON object; got {type(workload).__name__}")
    n_vectors = workload.get("n_vectors")
    if n_vectors is None:
        return None
    if isinstance(n_vectors, bool) or not isinstance(n_vectors, int):
        raise ValueError(
            f"workload.n_vectors must be an integer; got {type(n_vectors).__name__} {n_vectors!r}"
        )
    return n_vectors


def _scale_note(tier: str, n_vectors: int | None) -> str | None:
    """What the marker must add when the run was not measured at *tier*'s scale.

    ``None`` when ``n_vectors`` is the tier's own count, so a run measured at
    the tier's scale keeps the marker it always had.
    """
    if n_vectors is None:
        return "scale unrecorded"
    if n_vectors != TIER_N_VECTORS[tier]:
        return f"measured at n={n_vectors:,}, not {tier}"
    return None


def _provenance_marker(backend: str | None, engine: str, scale_note: str | None = None) -> str:
    """What this row's throughput number is, for *this* engine.

    The marker used to be a property of the *tier* — ``(real)`` when
    `--load-results` supplied the file, ``(simulated)`` under `--dry`. Whether a
    number is real is a property of the (tier, **engine**) pair: a file measured
    on pgvector is real for pgvector and borrowed for the other two (#144).

    - the file names *this* engine  -> ``(real)``
    - the file names another engine -> says whose it is, so the row cannot read
      as its own measurement
    - the file names a non-engine backend (the committed ``stub-10k`` run
      records ``"stub"``) -> ``(simulated)``, which is what every row of the
      shipped table already said, so the published artifact is unchanged
    - the file records no backend at all -> says so, rather than guessing

    ``scale_note`` (from `_scale_note`) is added to the two markers that name an
    engine (#291): ``(real, measured at n=1,000, not 100m)``. Those are the rows
    that claim to be a measurement, so they are the rows a 1,000-vector run
    must not pass for a 100M one. ``(simulated)`` and ``(provenance
    unrecorded)`` already say the row is no engine's measurement of the tier;
    they are unchanged, which keeps the committed stub-10k table byte-identical.

    **The marker comes from the data, not from a CLI flag.** It used to be
    ``(real)`` whenever ``--load-results`` supplied the file and ``(simulated)``
    only under ``--dry``, so the same stub measurement was marked
    ``(simulated)`` with ``--dry`` and carried **no marker at all** with
    ``--no-dry`` — a flag the operator chooses decided whether a number looked
    real. That is the same defect as the engine mislabelling, one level out:
    provenance is a property of the measurement. (This paragraph used to end
    "``--dry`` still selects *which* inputs are used"; nothing reads the flag, so
    it selects nothing either -- #156.)
    """
    if backend is None:
        return "(provenance unrecorded)"
    if backend == engine:
        label = "real"
    elif backend in ENGINES:
        label = f"measured on {backend}, not {engine}"
    else:
        return "(simulated)"
    if scale_note is not None:
        # `;` after a borrowed label, which already holds a comma.
        label += f"; {scale_note}" if backend != engine else f", {scale_note}"
    return f"({label})"


# ----------------------------------------------------------------------
# Pure-function table builder
# ----------------------------------------------------------------------


def uniform_qps(qps_by_tier: dict[str, float]) -> dict[tuple[str, str], float]:
    """Expand a per-tier throughput map to the per-(tier, engine) map `build_rows` takes.

    The explicit name is the point. Before #145 the only thing `build_rows`
    could express was "one number for the whole tier", so every caller got that
    silently; now a caller that wants it says so. Used by `main` for the tiers
    where no per-engine measurement was supplied, and by the tests whose subject
    is the *cost* model rather than the throughput plumbing.
    """
    return {(tier, engine): qps for tier, qps in qps_by_tier.items() for engine in ENGINES}


def uniform_source(source_by_tier: dict[str, str]) -> dict[tuple[str, str], str]:
    """`uniform_qps`'s sibling for the `render_markdown` source/backend maps."""
    return {(tier, engine): src for tier, src in source_by_tier.items() for engine in ENGINES}


def build_rows(
    tiers: dict[str, TierSizing],
    qps_by_tier_engine: dict[tuple[str, str], float],
    prices: PriceTable,
) -> list[CostPerQuery]:
    """For each (tier, engine), compute the cost-per-query row.

    Throughput is keyed by `(tier, engine)`, not by tier (#145, D-013). The
    infra bill is identical across engines within a tier — they share the
    instance type and EBS sizing — so throughput is the *only* input that can
    make the per-engine cost comparison this document exists for come out
    differently. While this map had no engine dimension, the three rows in a
    tier were identical by construction, and #144 made them say so.

    Use `uniform_qps()` to pass one number per tier deliberately.
    """
    rows: list[CostPerQuery] = []
    for tier_name in SCALE_TIERS:
        if tier_name not in tiers:
            raise KeyError(f"missing tier {tier_name!r}")
        tier = tiers[tier_name]
        for engine in ENGINES:
            qps = qps_by_tier_engine[tier_name, engine]
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


#: Widest fixed-point cell `format_usd_per_query` will produce before falling
#: back to scientific notation (#141). 12 decimals covers 1e-10 with two
#: significant figures to spare, and `tests/test_cost_per_query_operand_symmetry.py`
#: asserts `0.0 < usd_per_query < 1e-10` for one regime -- the smallest
#: magnitude this repo's own suite contemplates.
_MAX_USD_DECIMALS = 12


def format_usd_per_query(value: float) -> str:
    """Render a per-query dollar figure without truncating it to zero (#141).

    Both render sites used ``f"${x:.6f}"``. Six decimals cannot represent this
    quantity: the committed snapshot's own three tiers are

        1m    $74.08/mo / 4,266,558,000 queries = $0.000000017363/query
        10m   $219.96/mo                        = $0.000000051554/query
        100m  $915.84/mo                        = $0.000000214655/query

    so every row of `docs/cost_per_query.md` -- a document titled "Cost per
    query", whose opening line is "Amortized USD per query" -- rendered
    ``$0.000000``. Nine rows, one value, in the column the document is named
    after.

    And this repo already treats that exact string as the name of a defect.
    ``tests/test_cost_per_query_operand_symmetry.py``'s own docstring says the
    #129 guard exists because a sign-only check "would let nan qps yield
    ``usd_per_query=nan`` and inf qps a fabricated ``$0.00/query``". #129
    hardened the *computation* against producing a fake zero; the *presentation*
    was producing one for every real value.

    **Significant figures, not a fixed width.** A wider fixed precision is the
    plausible wrong fix and it is falsifiable by a value this repo's own suite
    already contemplates -- the same test asserts ``0.0 < usd_per_query < 1e-10``
    for one regime, which any fixed ``.Nf`` with N <= 10 renders as zeros. So
    the rule is stated over the *value*: keep enough decimals that a strictly
    positive number never renders as all zeros.

    Zero itself renders ``$0.00``: a genuinely zero per-query cost is a real
    answer (a free tier, or a monthly bill of exactly 0), and it is the one
    input for which a string of zeros is honest. That is the boundary the
    guard is stated around -- *strictly positive* must not read as zero.
    """
    if not math.isfinite(value):
        # Unreachable from `cost_per_query`, which raises on a non-finite
        # result (#129). Rendered rather than crashed, so a future caller
        # handing this a raw float gets a legible cell instead of a traceback
        # out of a formatter.
        return f"${value}"
    if value <= 0.0:
        return f"${value:.2f}"
    if round(value, 2) > 0.0:
        # Cents, whenever cents say something. This is every ordinary dollar
        # amount and keeps the common case reading like money.
        return f"${value:.2f}"
    # Cents would round to zero, so widen to the first significant digit plus
    # two more -- non-zero is the floor, distinguishable is the point. For
    # 2.1e-7 that is 7 + 2 = 9 decimals -> "$0.000000215", and the three
    # committed tiers become 0.0000000174 / 0.0000000516 / 0.000000215.
    decimals = -math.floor(math.log10(value)) + 2
    if decimals > _MAX_USD_DECIMALS:
        # Past that width a fixed-point cell stops being readable -- a denormal
        # renders ~330 characters of zeros, which is a worse table than the one
        # this fix replaced. Scientific notation is non-zero AND compact, which
        # are the two properties that matter. Reachable only from a value the
        # cost model itself would refuse, so this is the shape of the fallback
        # rather than a case in the published table.
        return f"${value:.2e}"
    return f"${value:.{decimals}f}"


def format_qps(value: float) -> str:
    """Render a throughput without truncating a strictly positive value to zero (#148).

    `#141`'s rule, stated over the value rather than over dollars: "keep enough
    decimals that a strictly positive number never renders as all zeros". It was
    written for `$/query` and it is not about dollars -- `f"{qps:.1f}"` has the
    same shape from the other direction, and `0.0` sits next to a **non-zero**
    `$/query` on the same row, because `cost_per_query` divides by this number
    and refuses `qps <= 0` (#129). So a small positive throughput is precisely
    the reachable case, not a hypothetical one: a 100M-tier exact-search
    measurement is the realistic source.

    `.1f` stays the narrow form. Tenths of a query per second is the resolution
    a load harness reports and the width the column is sized for; every
    committed row keeps it byte-identically.

    A genuine `0.0` cannot reach the published table -- `cost_per_query` raises
    first -- but it is rendered narrowly rather than widened anyway, so a future
    caller handing this a zero gets `0.0` and not a row of zeros claiming to be
    a measurement.
    """
    if not math.isfinite(value):
        # Same posture as `format_usd_per_query`: legible cell, not a traceback
        # out of a formatter. Unreachable through `cost_per_query` (#129).
        return str(value)
    if value <= 0.0:
        return f"{value:.1f}"
    narrow = f"{value:.1f}"
    if float(narrow) != 0.0:
        return narrow
    decimals = -math.floor(math.log10(value)) + 2
    if decimals > _MAX_USD_DECIMALS:
        return f"{value:.2e}"
    return f"{value:.{decimals}f}"


def _shown(path: Path) -> str:
    """A path as report TEXT: a non-UTF-8 filesystem byte shown as `\\xff`.

    Such a byte is a lone surrogate in the decoded path, and the markdown is
    written as UTF-8, so printing the path raw raised UnicodeEncodeError on any
    filesystem that accepted the name (ext4) -- a traceback rather than the
    OSError the write seam handles (#174).
    """
    text = str(path).encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    # A line break in the path split its table row in the published
    # `docs/cost_per_query.md` (#288, the surface #281 did not list: it refused
    # control characters in `run_id`, while `--results-dir` and `--load-results`
    # are unchecked). A path is real, so it is shown, not refused: every control
    # character and line/paragraph separator as a visible escape, the way the
    # undecodable byte above is.
    return "".join(_escaped(ch) for ch in text)


def _escaped(ch: str) -> str:
    if unicodedata.category(ch) in ("Cc", "Zl", "Zp"):
        return ch.encode("unicode_escape").decode("ascii")
    return ch


def render_markdown(
    rows: Iterable[CostPerQuery],
    *,
    prices: PriceTable,
    qps_source: dict[tuple[str, str], str],
    qps_backend: dict[tuple[str, str], str | None] | None = None,
    qps_n_vectors: dict[tuple[str, str], int | None] | None = None,
    results_dir_display: str = "results/load",
) -> str:
    """Render the per-tier table + assumptions block.

    `results_dir_display` is the default-run directory as the method bullet
    names it: repo-relative for the committed `results/load`, and the
    operator's `--results-dir` otherwise (#174).

    `qps_source` and `qps_backend` are keyed by `(tier, engine)` (#145, D-013).
    They were keyed by tier, which was correct only while every engine in a
    tier carried the same number from the same file — the limitation #145
    removed. `uniform_source()` builds the per-tier-everywhere form for callers
    that want it.

    `qps_n_vectors` is the same key's ``workload.n_vectors`` (#291). An entry
    that is absent reads as unrecorded, as an absent `qps_backend` entry does.
    """
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
        # The measured rate, not a p50-derived one (#180): `load.py` sets
        # `throughput_qps = n_queries / query_elapsed_s`, which pays the
        # per-query overhead a 1000 / p50 rate leaves out (committed c001:
        # 1623.5 qps measured vs 1634.2 from its p50).
        f"- **Throughput**: from `{results_dir_display}/<run_id>/c001.json`: the "
        "single-client measured rate, `n_queries` ÷ the query phase's "
        "wall-clock at concurrency 1 (the conservative basis; it includes the "
        "per-query overhead a p50-derived rate would leave out). For each tier "
        "the source is listed in the table.",
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
        # `qps_source` is the one free-form cell — it carries the operator-supplied
        # `--load-results TIER=PATH` directory (see `main`). Every other cell is a
        # controlled tier/engine/instance value or a formatted number. Backticks do
        # NOT protect a literal `|`: GFM splits table cells on unescaped pipes
        # *before* it parses inline-code spans, so a piped path injects a spurious
        # column and corrupts the whole table's alignment. Escape `|` -> `\|`
        # (GitHub renders `\|` as a literal pipe, inside a code span in a table too)
        # so the cell contributes zero column delimiters — same recurring GFM fix as
        # `comment._row_to_md` (llm-eval-harness #130), `calibration.render_report`
        # (#134), `aggregate_markdown` (embedding-model-shootout #79), and
        # `run_matrix._render_summary` (chunking-strategies-lab #100), applied here (#76).
        # The marker is computed per row, because "real" is a property of the
        # (tier, engine) pair rather than of the tier (#144).
        key = (r.scale_tier, r.engine)
        marker = _provenance_marker(
            (qps_backend or {}).get(key),
            r.engine,
            _scale_note(r.scale_tier, (qps_n_vectors or {}).get(key)),
        )
        source_cell = f"{qps_source.get((r.scale_tier, r.engine), '—')} {marker}".strip().replace(
            "|", "\\|"
        )
        lines.append(
            f"| {r.scale_tier} | {r.engine} | {instance_type} | {ebs_summary} | "
            # `$/M queries` through the same renderer as `$/query`, because it is
            # the same quantity: `usd_per_million_queries = per_query * 1e6`, one
            # line in `cost.py`. At `.2f` it published `$0.00` above 5,638 qps on
            # the committed 1m tier -- while the cell to its left read
            # `$0.00000000470`, so the row contradicted itself (#148). The
            # committed 1623.5 qps comes from the stub every row annotates
            # `(simulated)`; a real ANN engine on an m6i.large clears that cliff.
            f"${r.monthly_cost.total_usd_month:.2f} | {format_qps(r.throughput_qps)} | "
            f"{format_usd_per_query(r.usd_per_query)} | "
            f"{format_usd_per_query(r.usd_per_million_queries)} | "
            f"{source_cell} |"
        )

    lines += [
        "",
        "## What the numbers say (and don't)",
        "",
        "- The infra bill is **identical across engines per tier** because "
        "they share the same instance type + EBS sizing (see the Terraform "
        "locals). Cost-per-query differences between engines therefore come "
        "entirely from throughput, and the table consumes throughput per "
        "engine: pass `--load-results TIER=PATH` once per engine's run "
        "directory, and each row is built from the file whose `backend` names "
        "that engine (#145). The `Throughput source` column says whose "
        "measurement each row is carrying. `(real)` means this engine was "
        "measured; `(measured on <other engine>)` is borrowing a number, not "
        "reporting one — which happens when a single run directory is supplied "
        "for a tier, since one source is unambiguous to borrow from. Supply "
        "two or more and an engine named by none of them falls back to the "
        "default run rather than borrowing, because choosing which of several "
        "measurements to attribute to it would be arbitrary (#144).",
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


def _parse_load_results_overrides(raw: list[str] | None) -> dict[str, list[Path]]:
    """Parse `--load-results TIER=PATH` arguments into a {tier: [dir, ...]} mapping.

    Repeatable **per tier** (#145, D-013): one run directory is one engine's
    measurement, because `LoadMatrix` is "all cells for one `(backend, workload)`
    pair" and a `run_id` directory holds exactly that. So supplying three
    directories for one tier is how an operator supplies three engines, and the
    engine binding is read out of each file's own `backend` field rather than
    restated in the flag. #144 decided that provenance is a property of the
    measurement and not of a CLI flag; a `TIER:ENGINE=PATH` syntax would put it
    back on the flag.

    This returned a `dict[str, Path]` and assigned `out[tier] = ...`, so a
    repeated tier **silently kept the last one** — the natural way to express
    "pgvector and qdrant, both at 1m" quietly discarded the first file.

    Order is preserved: it is the order the operator wrote, and it is what the
    resolution rule reports when it has to name the files it could not
    disambiguate.

    Raises ValueError on malformed entries or unknown tier so `main` can
    surface a clear error + the known-tier inventory on stderr.
    """
    out: dict[str, list[Path]] = {}
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
        out.setdefault(tier, []).append(Path(path_str))
    return out


@dataclass(frozen=True)
class _Measurement:
    """One supplied `c001.json`: where it came from, what it says, whose it is."""

    directory: Path
    qps: float
    backend: str | None
    n_vectors: int | None = None

    @property
    def engine(self) -> str | None:
        """The engine this measurement belongs to, or None if it names none.

        The committed stub run records ``"stub"`` — a truthful answer that names
        no engine — so a file is engine-attributed only when its backend is one
        of `ENGINES`.
        """
        return self.backend if self.backend in ENGINES else None


def resolve_engine_throughput(
    supplied: list[_Measurement],
    default: _Measurement,
) -> dict[str, _Measurement]:
    """Which measurement each engine's row in one tier is built from (#145, D-013).

    The rule, in one sentence: **use the supplied file that names this engine;
    failing that, the tier's single unambiguous fallback — the one supplied
    file, when exactly one was supplied; failing that, the `--run-id`
    default.**

    "Exactly one" is the load-bearing phrase, and it is what keeps this change
    from moving anything that already shipped:

    - **One file supplied** (every invocation that existed before #145):
      identical behaviour. The file's own engine gets it as `(real)`; the other
      two borrow it and are labelled `(measured on X, not Y)` by #144's marker.
      Borrowing from a single source is unambiguous — there is nothing to
      choose between.
    - **Two or more supplied:** each engine uses the one that names it. An
      engine named by none falls back to the default run rather than borrowing,
      because picking which of several measurements to attribute to it is an
      *arbitrary attribution* — and arbitrary attribution is exactly the defect
      #144 exists to prevent. Silently borrowing pgvector's number for weaviate
      because pgvector happened to be typed first is the same class of error as
      publishing one run as all three.

    A file that names no engine (the stub) can still be the single unambiguous
    fallback; it cannot be engine-attributed, and its marker stays
    ``(simulated)``. Among two or more it has no row to land in, so it is
    refused rather than read and dropped (#249).

    Raises:
        ValueError: if two supplied files claim the same engine. That is an
            operator mistake with no defensible resolution — one of the two
            measurements would have to be discarded silently, which is what the
            old dict-assignment did. Also if two or more files are supplied and
            one names no engine: it can be neither engine-attributed nor the
            single fallback, so the old ``continue`` discarded it at exit 0.
    """
    by_engine: dict[str, _Measurement] = {}
    for measurement in supplied:
        engine = measurement.engine
        if engine is None:
            if len(supplied) > 1:
                raise ValueError(
                    f"--load-results supplied {len(supplied)} results for one tier and "
                    f"{measurement.directory}/c001.json names no engine (backend "
                    f"{measurement.backend!r}; expected one of {list(ENGINES)}). With "
                    "two or more files per tier each must name the engine it measured, "
                    "or it would be read and silently left out of the table."
                )
            continue
        if engine in by_engine:
            raise ValueError(
                f"--load-results supplied two results for engine {engine!r} in the same "
                f"tier: {by_engine[engine].directory}/c001.json and "
                f"{measurement.directory}/c001.json. Each engine takes at most one "
                "run directory per tier."
            )
        by_engine[engine] = measurement

    # The single unambiguous fallback, or None when there is a choice to make.
    fallback = supplied[0] if len(supplied) == 1 else default

    return {engine: by_engine.get(engine, fallback) for engine in ENGINES}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "No effect since #144: each row's marker comes from the measurement it "
            "reads, not from this flag. Accepted so the documented `--dry` commands "
            "keep working; use `--load-results` to supply real measurements."
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
            "Repeatable; each invocation adds one mapping. The row's marker comes "
            "from the file: `(real)` for the engine it was measured on, and it says "
            "so when the run's workload.n_vectors is not the tier's count."
        ),
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output markdown path. Default: docs/cost_per_query.md.",
    )
    args = p.parse_args(argv)
    # `--dry` is a documented compatibility no-op (#156, D-016). `--no-dry` is the
    # spelling an operator reaches for when they want unmarked "real" rows, and it
    # used to change nothing silently; say so, and say what does.
    if not args.dry:
        print(
            "note: --no-dry has no effect; each row's marker comes from the "
            "measurement it reads. Pass --load-results TIER=PATH for real rows.",
            file=sys.stderr,
        )

    try:
        load_overrides = _parse_load_results_overrides(args.load_results)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # The operator-supplied `--tf-main` and the load-harness `c001.json` files
    # are missing-input, not internal, errors: translate a FileNotFoundError to a
    # clean exit 2 (mirroring the _parse_load_results_overrides guard above),
    # rather than let it escape as a raw traceback (#83/#84/#85). This also lets
    # `load_throughput_qps`'s carefully-worded "Re-run the load harness" message
    # actually reach the operator instead of being buried in a stack trace.
    # Names whichever input the block below is currently reading, so the
    # value-axis handler can say *which* file was bad. `main` loads the
    # terraform file and then one `c001.json` per tier, so an undifferentiated
    # "invalid input" would leave the operator to guess among several paths.
    current_input = args.tf_main
    try:
        tf_text = Path(args.tf_main).read_text(encoding="utf-8")
        tiers = parse_terraform_tiers(tf_text)

        # All three of these are keyed by (tier, ENGINE) rather than by tier
        # (#145, D-013). The marker already had to be per-engine — whether a
        # throughput number is "real" depends on which engine produced it
        # (#144) — and now the *number itself* does too, which is what lets a
        # tier's three rows differ at all.
        qps_by_tier_engine: dict[tuple[str, str], float] = {}
        qps_source: dict[tuple[str, str], str] = {}
        qps_backend: dict[tuple[str, str], str | None] = {}
        qps_n_vectors: dict[tuple[str, str], int | None] = {}
        results_dir = Path(args.results_dir)

        def _read(directory: Path) -> _Measurement:
            return _Measurement(
                directory=directory,
                qps=load_throughput_qps(directory),
                backend=load_throughput_backend(directory),
                n_vectors=load_n_vectors(directory),
            )

        def _display(directory: Path) -> str:
            """The source cell: the file this row's throughput was read from.

            Repo-relative only for the committed default directory. It used to
            ask whether `directory` was `results_dir / run_id`, which is true for
            every default-run directory *whatever* `--results-dir` was, and then
            print a hard-coded `results/load/` -- so `--results-dir /tmp/vr`
            credited the committed `results/load/stub-10k/c001.json` (1623.5 qps)
            for the 50.0 qps it read from `/tmp/vr` (#174).
            """
            if directory.resolve() == (DEFAULT_RESULTS_DIR / args.run_id).resolve():
                return f"`results/load/{args.run_id}/c001.json`"
            return f"`{_shown(directory)}/c001.json`"

        for tier in SCALE_TIERS:
            default_dir = results_dir / args.run_id
            current_input = f"{default_dir}/c001.json"
            default_measurement = _read(default_dir)
            supplied: list[_Measurement] = []
            for override_dir in load_overrides.get(tier, []):
                current_input = f"{override_dir}/c001.json"
                supplied.append(_read(override_dir))
            for engine, measurement in resolve_engine_throughput(
                supplied, default_measurement
            ).items():
                qps_by_tier_engine[tier, engine] = measurement.qps
                qps_source[tier, engine] = _display(measurement.directory)
                qps_backend[tier, engine] = measurement.backend
                qps_n_vectors[tier, engine] = measurement.n_vectors
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        # A present-but-non-UTF-8 `--tf-main` (or a `c001.json` load-results file)
        # is unreadable input, the same missing-input class the FileNotFoundError
        # branch handles: `read_text(encoding="utf-8")` raises UnicodeDecodeError,
        # which subclasses ValueError — NOT FileNotFoundError — so it escaped the
        # branch above and leaked a raw traceback at exit 1. Translate it to a
        # clean exit 2, completing the #83/#84/#85 exit-code contract (sibling of
        # llm-eval-harness#174 / prompt-regression-suite#126).
        print(f"input file is not valid UTF-8: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        # A `c001.json` supplied via `--load-results TIER=PATH` is operator input
        # (they point at their real load-results dir); a present-but-malformed one
        # raises json.JSONDecodeError, which — like the plot scripts' matrix/grid
        # reads in this same change — is bad input, not an internal error. Exit 2,
        # not a raw traceback. (Deep schema validation of a well-formed-JSON but
        # wrong-shape c001.json stays out of scope, matching the plot scripts.)
        print(f"input file is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except (TypeError, ValueError, KeyError) as exc:
        # A file that parses as JSON (or as terraform) but isn't a valid input is
        # the same class of bad operator input as the decode failures above —
        # and unlike them, this block *deliberately raises these very
        # exceptions*: `load_throughput_qps`'s bool guard and `float()` coercion
        # raise ValueError, a null/array payload raises TypeError out of
        # `float(None)` / `payload["throughput_qps"]`, an absent key raises
        # KeyError, `cost_per_query`'s finiteness check raises ValueError for a
        # zero/NaN/Infinity qps, and `parse_terraform_tiers` raises ValueError
        # for a `--tf-main` missing tier blocks or tier fields. Every one of them
        # used to escape as a raw traceback at exit 1, so the guards were raising
        # into nothing (#115). Translate to a clean exit 2, matching
        # `plot_latency.main`'s `(TypeError, ValueError, KeyError)` arm verbatim
        # and the `except ValueError` arm `_parse_load_results_overrides` already
        # has a few lines up, per the #83/#84/#85 exit-code contract.
        #
        # Arm order is load-bearing: `UnicodeDecodeError` and
        # `json.JSONDecodeError` are both `ValueError` subclasses, so they must
        # stay above this one to keep their more specific messages.
        #
        # Deep schema validation of a well-formed-but-wrong-shape `c001.json`
        # stays out of scope here, matching the explicit scoping in the plot
        # scripts; this only fixes how such a file is *reported*.
        print(f"{current_input} is not a valid cost-table input: {exc}", file=sys.stderr)
        return 2

    prices = aws_us_east_1_snapshot()

    # Populate the side-channel before rendering. Side-channel is intentional:
    # CostPerQuery doesn't carry instance_type because the cost model itself
    # only needs the price, but the table reader wants the human-readable
    # column. A future refactor pushes this into the dataclass.
    _tier_to_instance.clear()
    for t in SCALE_TIERS:
        _tier_to_instance[t] = tiers[t].instance_type

    # `cost_per_query`'s own validation lives one layer down and fires *here*,
    # not in the load block above: a `throughput_qps` of `0`, `NaN` or
    # `Infinity` passes `float()` cleanly at load time and is only rejected when
    # the amortization runs. That check is correct (an infinite qps would
    # fabricate a `$0.00/query` row) but it raised `ValueError` past the end of
    # the guarded block and out as a raw traceback at exit 1 — the same
    # raising-into-nothing gap as the load-block guards (#115). Same arm, so the
    # cost model's refusal reaches the operator as a clean exit 2. The message
    # points at the throughput sources because that is the only operator input
    # feeding this call.
    try:
        rows = build_rows(tiers, qps_by_tier_engine, prices)
    except UnknownInstanceTypeError as exc:
        # Not a throughput problem: the instance type comes from `--tf-main`, and
        # the library's "Pass a PriceTable" cannot be followed from this script,
        # whose prices are `aws_us_east_1_snapshot()` (#176).
        print(
            f"no price for an instance type in {args.tf_main}: {exc.args[0].split(' Pass a')[0]} "
            f"This script prices with vector_bench.prices.aws_us_east_1_snapshot(); "
            f"add the instance there, or use an instance type it lists.",
            file=sys.stderr,
        )
        return 2
    except (TypeError, ValueError, KeyError) as exc:
        sources = ", ".join(sorted({s.replace("`", "") for s in qps_source.values()}))
        print(
            f"cost model rejected a measured throughput (from {sources}): {exc}",
            file=sys.stderr,
        )
        return 2
    md = render_markdown(
        rows,
        prices=prices,
        qps_source=qps_source,
        qps_backend=qps_backend,
        qps_n_vectors=qps_n_vectors,
        results_dir_display=(
            "results/load"
            if results_dir.resolve() == DEFAULT_RESULTS_DIR.resolve()
            else _shown(results_dir)
        ),
    )

    out_path = Path(args.out)
    # The output path is operator input too: an unwritable `--out` (a read-only
    # filesystem, a permission-denied dir, or a path component that is a file)
    # makes `atomic_write_text` raise OSError, which without this guard escaped as
    # a raw traceback at exit 1 — the same write-seam gap the input guards (#96)
    # never covered on this script. Translate to a clean exit 2 per the #83/#84
    # exit-code contract (write-seam sibling of llm-eval-harness#158/#159).
    try:
        atomic_write_text(out_path, md)
    except OSError as exc:
        sys.stderr.write(f"could not write {out_path}: {exc}\n")
        return 2

    print(f"cost-table wrote {out_path}")
    for r in rows:
        print(
            f"  {r.scale_tier:>4s}  {r.engine:<10s}  ${r.monthly_cost.total_usd_month:>8.2f}/mo  "
            f"{format_qps(r.throughput_qps):>8s} qps  {format_usd_per_query(r.usd_per_query)}/q  "
            f"{format_usd_per_query(r.usd_per_million_queries):>8s}/M"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
