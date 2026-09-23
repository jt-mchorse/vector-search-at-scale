"""Per-engine throughput reaches the table, and borrowing stays unambiguous (#145, D-013).

#144 fixed the *labelling*: a throughput measured on one engine stopped being
published as the other two's without saying so. The limitation it left is what
this module is about — `qps_by_tier` had no engine dimension, so the three rows
in a tier were identical *by construction* and the per-engine cost comparison
the document exists for could not be produced by the tool at all.

Two design questions the issue raised were answered by reading the repo rather
than by choosing:

- `src/vector_bench/load.py` documents `LoadMatrix` as "all cells for one
  `(backend, workload)` pair", and a `run_id` directory holds exactly that. **A
  run is already per-engine**, so per-engine input needs no change to D-007 —
  the operator points at several run directories.
- The engine binding is read out of each file's own `backend` field rather than
  restated in a `TIER:ENGINE=PATH` flag, because #144 decided that provenance is
  a property of the measurement and not of a CLI flag. Putting it back on the
  flag would re-introduce the defect one level out.

The resolution rule, in one sentence: **use the supplied file that names this
engine; failing that, the tier's single unambiguous fallback — the one supplied
file, when exactly one was supplied; failing that, the `--run-id` default.**

`test_one_supplied_file_still_borrows_for_the_other_engines` is the arm that
stays green on both trees, and it is the one that matters: every invocation
that existed before #145 supplied exactly one file per tier, and must keep
producing byte-identical output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.cost_table import (  # noqa: E402
    DEFAULT_TFVARS_PATH,
    ENGINES,
    SCALE_TIERS,
    _Measurement,
    _parse_load_results_overrides,
    build_rows,
    main,
    parse_terraform_tiers,
    resolve_engine_throughput,
    uniform_qps,
)
from vector_bench.prices import aws_us_east_1_snapshot  # noqa: E402


def _seed(dest: Path, *, qps: float, backend: str | None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"throughput_qps": qps}
    if backend is not None:
        payload["backend"] = backend
    (dest / "c001.json").write_text(json.dumps(payload), encoding="utf-8")
    return dest


def _m(name: str, backend: str | None, qps: float) -> _Measurement:
    return _Measurement(directory=Path(name), qps=qps, backend=backend)


DEFAULT = _m("results/load/stub-10k", "stub", 1623.5)


# ----------------------------------------------------------------------
# The resolution rule
# ----------------------------------------------------------------------


def test_nothing_supplied_means_every_engine_reads_the_default_run() -> None:
    resolved = resolve_engine_throughput([], DEFAULT)
    assert set(resolved) == set(ENGINES)
    assert all(m is DEFAULT for m in resolved.values())


def test_one_supplied_file_still_borrows_for_the_other_engines() -> None:
    """Back-compat, and the reason borrowing is still correct here.

    Every pre-#145 invocation is this one. Borrowing from a single source is
    unambiguous -- there is nothing to choose between -- so the behaviour is
    unchanged and #144's marker does the rest.
    """
    pg = _m("real_pgvector", "pgvector", 842.0)
    resolved = resolve_engine_throughput([pg], DEFAULT)
    assert resolved["pgvector"] is pg
    assert resolved["qdrant"] is pg
    assert resolved["weaviate"] is pg


def test_two_supplied_files_each_bind_to_the_engine_that_produced_them() -> None:
    pg = _m("real_pgvector", "pgvector", 842.0)
    qd = _m("real_qdrant", "qdrant", 1310.5)
    resolved = resolve_engine_throughput([pg, qd], DEFAULT)
    assert resolved["pgvector"] is pg
    assert resolved["qdrant"] is qd


def test_an_engine_named_by_none_of_several_files_falls_back_rather_than_borrowing() -> None:
    """The load-bearing half of the rule.

    With two measurements in hand there is no principled answer to "whose does
    weaviate get", and silently picking the first-typed one is the same class of
    error as publishing one run as all three -- the defect #144 exists to
    prevent. So it reads the default run and is labelled by its own provenance.
    """
    pg = _m("real_pgvector", "pgvector", 842.0)
    qd = _m("real_qdrant", "qdrant", 1310.5)
    resolved = resolve_engine_throughput([pg, qd], DEFAULT)
    assert resolved["weaviate"] is DEFAULT


def test_the_order_the_operator_typed_them_does_not_decide_anything() -> None:
    """A rule that depended on argument order would be arbitrary attribution
    wearing a deterministic hat."""
    pg = _m("real_pgvector", "pgvector", 842.0)
    qd = _m("real_qdrant", "qdrant", 1310.5)
    forward = resolve_engine_throughput([pg, qd], DEFAULT)
    backward = resolve_engine_throughput([qd, pg], DEFAULT)
    assert forward == backward


def test_a_file_naming_no_engine_can_still_be_the_single_fallback() -> None:
    """The committed stub run records `backend: "stub"` -- truthful, and names
    no engine. It cannot be engine-attributed, but one of it is unambiguous."""
    stub = _m("some_stub_run", "stub", 999.0)
    resolved = resolve_engine_throughput([stub], DEFAULT)
    assert all(m is stub for m in resolved.values())
    assert stub.engine is None


def test_two_files_claiming_the_same_engine_is_refused() -> None:
    """No defensible resolution: one of two real measurements would have to be
    discarded. The old `dict[tier] = path` assignment did exactly that,
    silently."""
    a = _m("run_a", "pgvector", 842.0)
    b = _m("run_b", "pgvector", 901.0)
    with pytest.raises(ValueError, match="two results for engine"):
        resolve_engine_throughput([a, b], DEFAULT)


# ----------------------------------------------------------------------
# The parser that feeds it
# ----------------------------------------------------------------------


def test_a_repeated_tier_accumulates_instead_of_silently_overwriting() -> None:
    """Pre-#145 this returned `{tier: Path}` and assigned, so the natural way to
    express "pgvector and qdrant, both at 1m" kept only the last file."""
    parsed = _parse_load_results_overrides(["1m=/runs/pg", "1m=/runs/qd"])
    assert parsed == {"1m": [Path("/runs/pg"), Path("/runs/qd")]}


def test_the_parser_still_rejects_what_it_always_rejected() -> None:
    for bad, match in (
        ("1m", "expected TIER=PATH"),
        ("nope=/runs/pg", "unknown tier"),
        ("1m=", "empty path"),
    ):
        with pytest.raises(ValueError, match=match):
            _parse_load_results_overrides([bad])


# ----------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------


def test_build_rows_takes_throughput_per_tier_and_engine() -> None:
    """AC2 at the pure-function seam: the map `build_rows` reads is keyed by the
    pair, so two engines in one tier can carry different numbers at all."""
    tiers = parse_terraform_tiers(DEFAULT_TFVARS_PATH.read_text(encoding="utf-8"))
    qps = {(t, e): 1000.0 for t in SCALE_TIERS for e in ENGINES}
    qps["1m", "qdrant"] = 2000.0
    rows = {(r.scale_tier, r.engine): r for r in build_rows(tiers, qps, aws_us_east_1_snapshot())}
    assert rows["1m", "pgvector"].throughput_qps == 1000.0
    assert rows["1m", "qdrant"].throughput_qps == 2000.0
    # Twice the throughput over an identical bill is exactly half the unit cost.
    assert rows["1m", "qdrant"].usd_per_query * 2 == pytest.approx(
        rows["1m", "pgvector"].usd_per_query
    )
    assert (
        rows["1m", "qdrant"].monthly_cost.total_usd_month
        == rows["1m", "pgvector"].monthly_cost.total_usd_month
    )


def test_uniform_qps_expands_one_number_across_the_engines() -> None:
    expanded = uniform_qps({"1m": 10.0, "10m": 20.0, "100m": 30.0})
    assert expanded == {
        (t, e): v for t, v in (("1m", 10.0), ("10m", 20.0), ("100m", 30.0)) for e in ENGINES
    }


def test_the_committed_artifact_regenerates_byte_identically(tmp_path: Path) -> None:
    """AC3, and the arm that must be green on both trees.

    The default invocation supplies no `--load-results` at all, so every engine
    reads the same stub run exactly as before. Nothing published moves.
    """
    out = tmp_path / "cost_per_query.md"
    assert main(["--dry", "--out", str(out)]) == 0
    committed = (_REPO_ROOT / "docs" / "cost_per_query.md").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed


def test_two_run_dirs_for_one_tier_reach_the_rendered_table(tmp_path: Path) -> None:
    """The whole path, from two `--load-results` flags to three distinct rows."""
    pg = _seed(tmp_path / "pg", qps=842.0, backend="pgvector")
    qd = _seed(tmp_path / "qd", qps=1310.5, backend="qdrant")
    out = tmp_path / "out.md"
    assert (
        main(
            ["--dry", "--load-results", f"1m={pg}", "--load-results", f"1m={qd}", "--out", str(out)]
        )
        == 0
    )
    rows = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.startswith("| 1m |")]
    qps_cells = [ln.split("|")[6].strip() for ln in rows]
    assert qps_cells == ["842.0", "1310.5", "1623.5"]
    # The 10m and 100m tiers were not overridden and are untouched.
    other = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.startswith("| 10m |")]
    assert {ln.split("|")[6].strip() for ln in other} == {"1623.5"}


def test_duplicate_engine_from_the_cli_is_a_clean_exit_two(tmp_path: Path) -> None:
    """An operator mistake surfaces as the repo's exit-2 bad-input contract
    (#83/#84/#85), not as a traceback and not as a silently dropped file."""
    a = _seed(tmp_path / "a", qps=842.0, backend="pgvector")
    b = _seed(tmp_path / "b", qps=901.0, backend="pgvector")
    out = tmp_path / "out.md"
    rc = main(
        ["--dry", "--load-results", f"1m={a}", "--load-results", f"1m={b}", "--out", str(out)]
    )
    assert rc == 2
    assert not out.exists()
