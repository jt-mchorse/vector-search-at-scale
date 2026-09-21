"""A throughput measurement belongs to the engine that produced it (#144).

`scripts/cost_table.py` reads **one** `c001.json` per *tier* and applies its
`throughput_qps` to all three engines::

    qps = qps_by_tier[tier_name]        # no engine dimension, anywhere
    for engine in ENGINES:
        rows.append(cost_per_query(infra, prices, qps))

`c001.json` records which backend produced it, and that field was never read.
The `(real)` marker was driven by whether `--load-results` had been passed.

Measured on the pre-change tree — the committed `c001.json` with `backend` set
to `pgvector` and `throughput_qps` to `842.0`, passed for the 1m tier::

    | 1m | pgvector | ... | 842.0 | $0.0000000335 | ... (real) |
    | 1m | qdrant   | ... | 842.0 | $0.0000000335 | ... (real) |
    | 1m | weaviate | ... | 842.0 | $0.0000000335 | ... (real) |

pgvector's measurement published as qdrant's and weaviate's, labelled `(real)`,
for two engines that never ran — a benchmark number attributed to something that
did not produce it (handoff §10).

**The same defect one level out**, found by the two existing tests that broke:
the marker was a function of the *invocation* rather than of the *measurement*.
`--no-dry` dropped `(simulated)` entirely while still reading the same
stub-backed file, so choosing a flag decided whether a simulated number looked
real. Both of those tests asserted the old behaviour and are updated in
`test_cost_table.py`, with the reason written into them.

What this module does **not** cover: per-engine throughput *input*. The
shared-throughput model is an honest approximation as long as it is labelled as
one, and widening the CLI to accept per-engine results is a feature with its own
design questions. Filed separately.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import (  # noqa: E402
    ENGINES,
    _provenance_marker,
    load_throughput_backend,
    main,
)

_COMMITTED_C001 = _REPO_ROOT / "results" / "load" / "stub-10k" / "c001.json"


def _seed(dest: Path, *, qps: float, backend: str | None) -> Path:
    """A `c001.json` shaped like the harness's, with *backend* controllable."""
    dest.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"throughput_qps": qps, "p95_latency_ms": 1.0}
    if backend is not None:
        payload["backend"] = backend
    (dest / "c001.json").write_text(json.dumps(payload), encoding="utf-8")
    return dest


def _tier_rows(md: str, tier: str) -> list[str]:
    return [ln for ln in md.splitlines() if ln.startswith(f"| {tier} |")]


def _cell(row: str, index: int) -> str:
    return [c.strip() for c in row.strip().strip("|").split("|")][index]


# ----------------------------------------------------------------------
# The harm
# ----------------------------------------------------------------------


def test_one_engines_measurement_is_not_published_as_the_others(tmp_path: Path) -> None:
    """The row the issue was filed for."""
    override = _seed(tmp_path / "real_pgvector", qps=842.0, backend="pgvector")
    out = tmp_path / "out.md"
    assert main(["--dry", "--load-results", f"1m={override}", "--out", str(out)]) == 0

    rows = {_cell(r, 1): r for r in _tier_rows(out.read_text(encoding="utf-8"), "1m")}
    assert set(rows) == set(ENGINES)

    assert "(real)" in rows["pgvector"]
    for other in ("qdrant", "weaviate"):
        assert "(real)" not in rows[other], f"{other} never ran: {rows[other]}"
        assert f"(measured on pgvector, not {other})" in rows[other]


def test_the_borrowed_rows_still_carry_the_number_they_were_given(tmp_path: Path) -> None:
    """The fix is to the label, not to the arithmetic.

    The shared-throughput model is unchanged: all three rows still amortize the
    same qps. What changed is that two of them now say whose it is. Pinned so a
    later change cannot quietly drop the borrowed rows instead of labelling
    them — that would be a different decision, and a louder one.
    """
    override = _seed(tmp_path / "real_pgvector", qps=842.0, backend="pgvector")
    out = tmp_path / "out.md"
    assert main(["--dry", "--load-results", f"1m={override}", "--out", str(out)]) == 0

    qps_cells = {_cell(r, 5) for r in _tier_rows(out.read_text(encoding="utf-8"), "1m")}
    assert qps_cells == {"842.0"}


# ----------------------------------------------------------------------
# The marker is a function of the measurement, not of the invocation
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "engine", "expected"),
    [
        ("pgvector", "pgvector", "(real)"),
        ("qdrant", "qdrant", "(real)"),
        ("pgvector", "qdrant", "(measured on pgvector, not qdrant)"),
        ("weaviate", "pgvector", "(measured on weaviate, not pgvector)"),
        # A backend that is not one of the three engines is a simulator.
        ("stub", "pgvector", "(simulated)"),
        ("hnsw-sim", "qdrant", "(simulated)"),
        # No `backend` key at all: say so rather than guess.
        (None, "pgvector", "(provenance unrecorded)"),
    ],
)
def test_the_marker_is_decided_by_the_backend_field(
    backend: str | None, engine: str, expected: str
) -> None:
    assert _provenance_marker(backend, engine) == expected


def test_dry_and_no_dry_describe_the_same_file_the_same_way(tmp_path: Path) -> None:
    """The second defect, and the one the existing tests had pinned.

    Both invocations read `results/load/stub-10k/c001.json`, which records
    `backend: "stub"`. `--no-dry` used to drop the `(simulated)` marker, so the
    identical measurement published with and without provenance depending on a
    flag the operator chose.
    """
    dry, wet = tmp_path / "dry.md", tmp_path / "wet.md"
    assert main(["--dry", "--out", str(dry)]) == 0
    assert main(["--no-dry", "--out", str(wet)]) == 0

    for tier in ("1m", "10m", "100m"):
        dry_rows = _tier_rows(dry.read_text(encoding="utf-8"), tier)
        wet_rows = _tier_rows(wet.read_text(encoding="utf-8"), tier)
        assert dry_rows == wet_rows, f"{tier} rows differ between --dry and --no-dry"
        for row in dry_rows:
            assert "(simulated)" in row


# ----------------------------------------------------------------------
# The prose must not claim a difference the table cannot show
# ----------------------------------------------------------------------


def test_the_doc_does_not_promise_per_engine_differences(tmp_path: Path) -> None:
    """The sentence and the data are generated into one file.

    It read: "The cost-per-query differences between engines therefore come
    from throughput differences" — while `qps` has no engine dimension, so those
    differences are identically zero by construction. Asserted against the
    rendered output rather than the source string, because that is what a reader
    sees.
    """
    out = tmp_path / "out.md"
    assert main(["--dry", "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")

    assert (
        "The cost-per-query differences between engines therefore come from "
        "throughput differences" not in md
    )
    assert "this table shows none" in md
    assert "identical by construction" in md

    # ...and the claim is true of the rendered rows: within a tier, every
    # engine's cost cells match. If that ever stops being true the sentence has
    # to change with it.
    for tier in ("1m", "10m", "100m"):
        rows = _tier_rows(md, tier)
        assert len(rows) == len(ENGINES)
        assert len({_cell(r, 6) for r in rows}) == 1, f"{tier} $/query differs"
        assert len({_cell(r, 5) for r in rows}) == 1, f"{tier} qps differs"


# ----------------------------------------------------------------------
# The shipped artifact
# ----------------------------------------------------------------------


def test_the_committed_run_records_a_backend_and_regenerates_unchanged(
    tmp_path: Path,
) -> None:
    """The whole change must be invisible on the published table.

    `results/load/stub-10k/c001.json` records `backend: "stub"`, which maps to
    the `(simulated)` marker every row of the committed doc already carried — so
    only the corrected sentence moves.
    """
    assert load_throughput_backend(_COMMITTED_C001.parent) == "stub"

    out = tmp_path / "regen.md"
    assert main(["--dry", "--out", str(out)]) == 0
    regenerated = out.read_text(encoding="utf-8")
    committed = (_REPO_ROOT / "docs" / "cost_per_query.md").read_text(encoding="utf-8")
    assert regenerated == committed


def test_load_throughput_backend_tolerates_a_non_string_backend(tmp_path: Path) -> None:
    """An externally-produced c001.json is not trusted to hold a string.

    Returning the raw value would put `None`/`3`/`{}` into a rendered cell; the
    reader coerces to `None`, which renders as "provenance unrecorded" — the
    honest answer for a field it cannot interpret.
    """
    for bad in (None, 3, [], {}, True):
        d = _seed(tmp_path / f"b{type(bad).__name__}{bad!r}", qps=1.0, backend=None)
        payload = json.loads((d / "c001.json").read_text(encoding="utf-8"))
        payload["backend"] = bad
        (d / "c001.json").write_text(json.dumps(payload), encoding="utf-8")
        assert load_throughput_backend(d) is None
