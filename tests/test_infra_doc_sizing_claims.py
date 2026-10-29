"""docs/infra.md's sizing claims agree with its own formula over the Terraform tiers (#292).

The "Sizing assumptions" prose was hand-written in #1 and, unlike the cost cells
(#280), never re-derived. The doc's formula is raw = N x dim x 4 bytes and an HNSW
index of 1.5-2.5x raw. Measured on 12e210b over the `tiers` map in
`terraform/envs/benchmark/main.tf`:

    tier   raw        index (1.5-2.5x)   RAM    EBS / index
    1m     1.43 GiB   2.1-3.6 GiB        8      14.0-23.3x
    10m    14.31 GiB  21.5-35.8 GiB      32     5.6-9.3x
    100m   143.05 GiB 214.6-357.6 GiB    128    4.2-7.0x

That contradicted three things the doc said. "The 1m / 10m tiers fit comfortably
in RAM" was wrong because the 10m index spills past 32 GiB at the high end.
"EBS sizing leaves 2-3x headroom" was wrong because the real headroom is 4-23x.
The raw-bytes column said ~1.5 / ~15 / ~150 GiB, which are decimal GB. The README's
real-backend example also ran `--dim 768` on the 1m tier, which the doc says is sized
for 384-dim.

These tests recompute every sizing cell and classification from main.tf, the price
snapshot's `memory_gib`, and the doc's own constants. The dimension is
`scripts/cost_table.TIER_DIM`, which the cost table also compares a run's
`workload.dim` to (#295); the doc must state that same number. A tier resize, a price-table
RAM change, or a doc edit that disagrees fails here.
"""

from __future__ import annotations

import math
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
    TIER_DIM,
    parse_terraform_tiers,
)
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402

_INFRA_MD = _REPO_ROOT / "docs" / "infra.md"
_README = _REPO_ROOT / "README.md"

_GIB = 2**30
_KIB = 2**10
_FLOAT32_BYTES = 4
_TIER_VECTORS = {"1m": 10**6, "10m": 10**7, "100m": 10**8}


def _doc() -> str:
    return _INFRA_MD.read_text(encoding="utf-8")


def _doc_dim() -> int:
    """The embedding dimension the doc says the tiers are sized for."""
    (dim,) = re.findall(r"assuming \*\*(\d+)-dimensional float32", _doc())
    return int(dim)


def test_doc_states_the_dimension_the_cost_table_checks() -> None:
    """One number for "the tiers are sized for 384-dim" (#295): the sizing model
    below and `cost_table`'s `workload.dim` check both use `TIER_DIM`, and the
    doc's prose must agree with it."""
    assert _doc_dim() == TIER_DIM
    assert f"With `dim = {TIER_DIM}`" in _doc()


def _doc_index_factor() -> tuple[float, float]:
    """The doc's HNSW-index-over-raw range, read from its own prose."""
    m = re.search(r"HNSW indexes typically run \*\*(\d+(?:\.\d+)?)–(\d+(?:\.\d+)?)×\*\*", _doc())
    assert m, "infra.md no longer states the HNSW index range"
    return float(m.group(1)), float(m.group(2))


def _model() -> dict[str, dict[str, float]]:
    tiers = parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))
    prices = aws_us_east_1_snapshot()
    dim = TIER_DIM
    lo_f, hi_f = _doc_index_factor()
    out: dict[str, dict[str, float]] = {}
    for name in SCALE_TIERS:
        t = tiers[name]
        inst = prices.get_instance(t.instance_type)
        raw = _TIER_VECTORS[name] * dim * _FLOAT32_BYTES / _GIB
        lo, hi = lo_f * raw, hi_f * raw
        out[name] = {
            "raw": raw,
            "index_lo": lo,
            "index_hi": hi,
            "ram": inst.memory_gib,
            "vcpus": inst.vcpus,
            "ebs": t.data_volume_gb,
            "ebs_ratio_lo": t.data_volume_gb / hi,
            "ebs_ratio_hi": t.data_volume_gb / lo,
        }
    return out


def _table_rows(md: str, header_prefix: str) -> dict[str, list[str]]:
    lines = md.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(header_prefix))
    rows: dict[str, list[str]] = {}
    for ln in lines[start + 2 :]:
        if not ln.startswith("|"):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells
    return rows


def _numbers(cell: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cell)]


def _decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def _matches_as_written(written: str, value: float) -> bool:
    """`written` is `value` rounded to the precision the doc wrote it at."""
    places = _decimals(written)
    return abs(float(written) - value) <= 0.5 * 10**-places + 1e-9


def _sizing_rows() -> dict[str, list[str]]:
    return _table_rows(_doc(), "| Tier  | HNSW index")


def _classification(m: dict[str, float]) -> str:
    if m["index_hi"] <= m["ram"]:
        return "yes"
    if m["index_lo"] <= m["ram"]:
        return "borderline"
    return "no"


def test_sizing_claims_table_lists_exactly_the_terraform_tiers() -> None:
    assert sorted(_sizing_rows()) == sorted(SCALE_TIERS)


def test_per_vector_raw_payload_matches_formula() -> None:
    m = re.search(r"that's `(\d+(?:\.\d+)?)\s+KiB/vector`", _doc())
    assert m
    assert float(m.group(1)) == TIER_DIM * _FLOAT32_BYTES / _KIB


def test_per_million_raw_payload_matches_formula() -> None:
    m = re.search(r"~(\d+(?:\.\d+)?) GiB per million vectors", _doc())
    assert m
    assert _matches_as_written(m.group(1), 10**6 * TIER_DIM * _FLOAT32_BYTES / _GIB)


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_raw_bytes_column_is_gib_of_the_formula(tier: str) -> None:
    """Not decimal GB under a GiB label: 1.536e9 bytes is 1.43 GiB, not ~1.5."""
    cell = _table_rows(_doc(), "| Tier  | Vectors")[tier][2]
    m = re.fullmatch(r"~(\d+(?:\.\d+)?) GiB", cell)
    assert m, cell
    assert _matches_as_written(m.group(1), _model()[tier]["raw"]), (
        f"infra.md {tier} raw cell {cell!r} != {_model()[tier]['raw']:.3f} GiB"
    )


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_vcpu_and_ram_columns_match_instance_type(tier: str) -> None:
    row = _table_rows(_doc(), "| Tier  | Vectors")[tier]
    m = _model()[tier]
    assert int(row[4]) == m["vcpus"]
    assert row[5] == f"{m['ram']:g} GiB"


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_index_range_cell_matches_formula(tier: str) -> None:
    cell = _sizing_rows()[tier][1]
    lo, hi = re.findall(r"\d+(?:\.\d+)?", cell)
    m = _model()[tier]
    assert _matches_as_written(lo, m["index_lo"]), (cell, m["index_lo"])
    assert _matches_as_written(hi, m["index_hi"]), (cell, m["index_hi"])


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_ram_cell_matches_instance_type(tier: str) -> None:
    (ram,) = _numbers(_sizing_rows()[tier][2])
    assert ram == _model()[tier]["ram"]


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_fits_in_ram_cell_matches_formula(tier: str) -> None:
    """The issue's core claim: 10m's index is 21.5-35.8 GiB on 32 GiB, so it is
    borderline, not "fits comfortably"."""
    cell = _sizing_rows()[tier][3]
    assert cell == _classification(_model()[tier])


@pytest.mark.parametrize("tier", SCALE_TIERS)
def test_ebs_ratio_cell_matches_formula(tier: str) -> None:
    cell = _sizing_rows()[tier][4]
    lo, hi = re.findall(r"\d+(?:\.\d+)?", cell)
    m = _model()[tier]
    assert _matches_as_written(lo, m["ebs_ratio_lo"]), (cell, m["ebs_ratio_lo"])
    assert _matches_as_written(hi, m["ebs_ratio_hi"]), (cell, m["ebs_ratio_hi"])


def test_ebs_headroom_prose_is_the_smallest_ratio_floored() -> None:
    """ "at least N× headroom" must be true on every tier, and must not be so
    low that it hides the real margin (the stale "2–3×" was true-ish as a floor
    but off by 2x)."""
    m = re.search(r"EBS sizing leaves at least (\d+)× headroom", _doc())
    assert m, "infra.md no longer states an EBS headroom floor"
    smallest = min(t["ebs_ratio_lo"] for t in _model().values())
    assert int(m.group(1)) == math.floor(smallest)


def test_ram_prose_matches_each_tiers_classification() -> None:
    """Each tier's prose verdict is the one the table derives. The stale
    "The 1m / 10m tiers fit comfortably in RAM" fails this for 10m."""
    flat = " ".join(_doc().split())
    phrase = {
        "yes": "The {t} tier fits in RAM across the whole range",
        "borderline": "The {t} tier is **borderline**",
        "no": "{t} intentionally does *not* fit",
    }
    for tier in SCALE_TIERS:
        cls = _classification(_model()[tier])
        want = phrase[cls].format(t=tier)
        assert want in flat, f"{tier} is {cls!r}; infra.md lacks {want!r}"
    assert "comfortably" not in flat


def test_borderline_crossover_matches_formula() -> None:
    """ "fits up to ~2.2× raw": the factor at which 10m's index reaches its RAM."""
    m = re.search(r"fits up to ~(\d+(?:\.\d+)?)× raw", _doc())
    assert m
    (tier,) = [t for t, v in _model().items() if _classification(v) == "borderline"]
    v = _model()[tier]
    assert _matches_as_written(m.group(1), v["ram"] / v["raw"])


def test_readme_real_backend_example_uses_the_sized_dimension() -> None:
    """The README's real-backend commands run against `make up SCALE=<tier>`;
    they must use the dimension infra.md sizes the tiers for, at no more vectors
    than the tier holds."""
    readme = _README.read_text(encoding="utf-8")
    (tier,) = re.findall(r"Real-backend flow \(assumes `make up SCALE=(\w+)`", readme)
    block = readme.split("Real-backend flow", 1)[1].split("```bash", 1)[1].split("```", 1)[0]
    runs = re.findall(r"--backend (\w+)\s+--n (\d+) --dim (\d+)", block)
    assert {b for b, _, _ in runs} == {"pgvector", "qdrant", "weaviate"}
    for backend, n, dim in runs:
        assert int(dim) == TIER_DIM, f"{backend} example runs --dim {dim} on the {tier} tier"
        assert int(n) <= _TIER_VECTORS[tier]


def test_readme_768_dim_note_matches_formula() -> None:
    readme = _README.read_text(encoding="utf-8")
    m = re.search(
        r"(\d+)-dim doubles\s+the (\w+) index to (\d+(?:\.\d+)?)–(\d+(?:\.\d+)?) GiB on the tier's (\d+) GiB",
        readme,
    )
    assert m
    dim, tier = int(m.group(1)), m.group(2)
    lo_f, hi_f = _doc_index_factor()
    raw = _TIER_VECTORS[tier] * dim * _FLOAT32_BYTES / _GIB
    assert _matches_as_written(m.group(3), lo_f * raw)
    assert _matches_as_written(m.group(4), hi_f * raw)
    assert float(m.group(5)) == _model()[tier]["ram"]
