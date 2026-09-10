"""No write-side numeric field accepts a ``bool`` (or a non-number) — #139.

``bool`` subclasses ``int``, so ``math.isfinite(True)`` is ``True`` and
``True < 0`` is ``False``. A finiteness-plus-sign guard — the shape every
result dataclass in this package used — lets a boolean straight through, and
``json.dumps`` writes it as the token ``true``.

Three **reader**-side guards were hardened against exactly that, each with a
docstring saying why. ``scripts/plot_hnsw_frontier.py`` puts it best: a JSON
``true`` at ``mean_recall_at_k``/``p95_ms`` "silently coerces to ``1.0`` and
fabricates a perfect benchmark row (and can hijack the recommended-defaults
knee)". The **writers** of those same fields were never enumerated. Measured
before the fix, one bool per numeric field::

    BenchmarkResult.{ingest_seconds,ingest_rows_per_sec,        ACCEPTS bool
                     mean_recall_at_k,cost_per_query_usd}
    LoadCell.{ingest_seconds,mean_recall_at_k,                  ACCEPTS bool
              throughput_qps,concurrency}
    Workload.seed                                               ACCEPTS bool
    InstancePrice.{usd_per_hour,memory_gib}                     ACCEPTS bool
    EbsGp3Price.{usd_per_gb_month,usd_per_iops_month_over_      ACCEPTS bool
                 baseline,usd_per_mibps_month_over_baseline}

    LatencyStats.{p50,p95,p99,max}_ms                           rejects  (#108 sibling)
    Workload.{n_vectors,dim,n_queries,top_k,concurrency}        rejects  (#29)
    InstancePrice.vcpus                                         rejects  (#127)
    EbsGp3Price.{included_iops,included_throughput_mibps}       rejects  (#127)

14 accepted, 12 rejected — and every rejecting field belongs to a sweep that
had a *reason* to think about booleans. The float half of each class sat two
lines away and was skipped.

The population here is **discovered** from each dataclass's own annotations
rather than listed, so a fifteenth numeric field is in scope the moment it is
added. A hand list is what let ``Workload.seed`` and ``LoadCell.concurrency``
sit outside every enumeration in the first place.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from vector_bench.cost import EbsGp3Price, InstancePrice
from vector_bench.harness import BenchmarkResult, LatencyStats, Workload
from vector_bench.load import LoadCell, LoadMatrix, dump_load_matrix_json
from vector_bench.types import is_valid_number

# Annotations are strings under `from __future__ import annotations`, so the
# numeric test is textual against the exact spellings this package uses. An
# unrecognised annotation is reported by `test_every_annotation_is_classified`
# rather than silently dropping the field out of the population.
NUMERIC_ANNOTATIONS = frozenset({"int", "float", "int | None", "float | None"})
NON_NUMERIC_ANNOTATIONS = frozenset(
    {
        "str",
        "str | None",
        "Workload",
        "LatencyStats",
        "dict[str, Any]",
        "tuple[str, ...]",
        "tuple[LoadCell, ...]",
    }
)


def _workload() -> Workload:
    return Workload(n_vectors=10, dim=4, n_queries=5, top_k=3)


def _latency() -> LatencyStats:
    return LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0)


def _benchmark_kwargs() -> dict[str, Any]:
    return dict(
        run_id="r1",
        backend="stub",
        workload=_workload(),
        ingest_seconds=1.0,
        ingest_rows_per_sec=10.0,
        query_latency=_latency(),
        mean_recall_at_k=0.5,
        started_at="2026-09-09T00:00:00Z",
        git_sha=None,
        cost_per_query_usd=0.25,
    )


def _loadcell_kwargs() -> dict[str, Any]:
    return dict(
        run_id="r1",
        backend="stub",
        workload=_workload(),
        concurrency=1,
        ingest_seconds=1.0,
        query_latency=_latency(),
        mean_recall_at_k=0.5,
        throughput_qps=100.0,
        started_at="2026-09-09T00:00:00Z",
        git_sha=None,
    )


def _workload_kwargs() -> dict[str, Any]:
    return dict(n_vectors=10, dim=4, n_queries=5, top_k=3, seed=1, concurrency=1)


def _latency_kwargs() -> dict[str, Any]:
    return dict(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0)


def _instance_kwargs() -> dict[str, Any]:
    return dict(
        instance_type="m6i.large", region="us-east-1", usd_per_hour=0.1, vcpus=2, memory_gib=8.0
    )


def _ebs_kwargs() -> dict[str, Any]:
    return dict(
        region="us-east-1",
        usd_per_gb_month=0.08,
        usd_per_iops_month_over_baseline=0.005,
        usd_per_mibps_month_over_baseline=0.04,
        included_iops=3000,
        included_throughput_mibps=125,
    )


# Every write-side dataclass whose fields reach a `to_dict()` and then
# `json.dumps`, with a factory for a fully valid instance. Adding a seventh
# here is how a new result type joins the sweep.
GUARDED_CLASSES: tuple[tuple[type, Callable[[], dict[str, Any]]], ...] = (
    (Workload, _workload_kwargs),
    (LatencyStats, _latency_kwargs),
    (BenchmarkResult, _benchmark_kwargs),
    (LoadCell, _loadcell_kwargs),
    (InstancePrice, _instance_kwargs),
    (EbsGp3Price, _ebs_kwargs),
)


def _numeric_fields(cls: type) -> list[str]:
    """Every numeric field of *cls*, discovered from its annotations."""
    return [f.name for f in dataclasses.fields(cls) if f.type in NUMERIC_ANNOTATIONS]


NUMERIC_FIELD_ROWS: tuple[tuple[type, Callable[[], dict[str, Any]], str], ...] = tuple(
    (cls, factory, name) for cls, factory in GUARDED_CLASSES for name in _numeric_fields(cls)
)


def _construct_with(
    cls: type, factory: Callable[[], dict[str, Any]], name: str, value: Any
) -> None:
    kwargs = factory()
    kwargs[name] = value
    cls(**kwargs)


# ---------------------------------------------------------------------------
# The population itself
# ---------------------------------------------------------------------------


def test_every_annotation_is_classified() -> None:
    """Discovery is only discovery if nothing falls out of it silently.

    A field whose annotation is in neither set is neither swept nor knowingly
    exempt — it is invisible, which is the exact failure mode this file exists
    to close. Fail loudly and make someone classify it.
    """
    unclassified = {
        f"{cls.__name__}.{f.name}: {f.type}"
        for cls, _ in GUARDED_CLASSES
        for f in dataclasses.fields(cls)
        if f.type not in NUMERIC_ANNOTATIONS and f.type not in NON_NUMERIC_ANNOTATIONS
    }
    assert not unclassified, (
        "these dataclass fields have an annotation this file does not classify "
        f"as numeric or non-numeric: {sorted(unclassified)}"
    )


def test_the_discovered_population_is_not_empty_or_tiny() -> None:
    """Anti-vacuous: a discovery that matched nothing would make every
    parametrized assertion below vacuously true (zero cases collected).
    """
    assert len(NUMERIC_FIELD_ROWS) >= 26, (
        f"expected at least 26 numeric fields across the six guarded classes, "
        f"discovered {len(NUMERIC_FIELD_ROWS)}: {[f'{c.__name__}.{n}' for c, _, n in NUMERIC_FIELD_ROWS]}"
    )
    discovered = {f"{cls.__name__}.{name}" for cls, _, name in NUMERIC_FIELD_ROWS}
    # The fourteen that accepted a bool before #139, named so their loss is loud.
    for previously_open in (
        "BenchmarkResult.ingest_seconds",
        "BenchmarkResult.ingest_rows_per_sec",
        "BenchmarkResult.mean_recall_at_k",
        "BenchmarkResult.cost_per_query_usd",
        "LoadCell.ingest_seconds",
        "LoadCell.mean_recall_at_k",
        "LoadCell.throughput_qps",
        "LoadCell.concurrency",
        "Workload.seed",
        "InstancePrice.usd_per_hour",
        "InstancePrice.memory_gib",
        "EbsGp3Price.usd_per_gb_month",
        "EbsGp3Price.usd_per_iops_month_over_baseline",
        "EbsGp3Price.usd_per_mibps_month_over_baseline",
    ):
        assert previously_open in discovered, f"{previously_open} dropped out of the population"


def test_every_baseline_constructs() -> None:
    """The factories must build a valid instance, or every rejection below
    would pass for the wrong reason — a `ValueError` from some *other* field.
    """
    for cls, factory in GUARDED_CLASSES:
        cls(**factory())


# ---------------------------------------------------------------------------
# The rule, per discovered field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "factory", "name"),
    NUMERIC_FIELD_ROWS,
    ids=[f"{c.__name__}.{n}" for c, _, n in NUMERIC_FIELD_ROWS],
)
@pytest.mark.parametrize("value", [True, False], ids=["True", "False"])
def test_no_numeric_field_accepts_a_bool(cls, factory, name, value) -> None:
    # `match` on the field name, not merely on `ValueError`: without it a
    # rejection raised by some *other* field of the same baseline would pass
    # this row for the wrong reason.
    with pytest.raises(ValueError, match=re.escape(name)):
        _construct_with(cls, factory, name, value)


@pytest.mark.parametrize(
    ("cls", "factory", "name"),
    NUMERIC_FIELD_ROWS,
    ids=[f"{c.__name__}.{n}" for c, _, n in NUMERIC_FIELD_ROWS],
)
@pytest.mark.parametrize("value", ["1.0", None, b"1", [1.0]], ids=["str", "None", "bytes", "list"])
def test_no_numeric_field_lets_a_non_number_escape_as_TypeError(cls, factory, name, value) -> None:
    """`ValueError`, not the `TypeError` a bare `math.isfinite("1.0")` raises.

    `cost.py::_require_whole_number` already made this call for the int half
    (#127) and `test_cost_int_field_domain.py` locks it; the float half of the
    same module still raised `TypeError`. `Optional` fields legitimately accept
    `None`, so that one row is skipped rather than asserted.
    """
    if value is None and dataclasses.fields(cls) and _is_optional(cls, name):
        pytest.skip(f"{cls.__name__}.{name} is Optional; None is in-domain")
    with pytest.raises(ValueError, match=re.escape(name)):
        _construct_with(cls, factory, name, value)


def _is_optional(cls: type, name: str) -> bool:
    return any(f.name == name and "None" in str(f.type) for f in dataclasses.fields(cls))


@pytest.mark.parametrize(
    ("cls", "factory", "name"),
    NUMERIC_FIELD_ROWS,
    ids=[f"{c.__name__}.{n}" for c, _, n in NUMERIC_FIELD_ROWS],
)
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
def test_no_numeric_field_accepts_a_non_finite(cls, factory, name, value) -> None:
    """The arm the old guards already had, kept as a regression floor: the
    shared predicate must not have widened anything on its way in.
    """
    with pytest.raises(ValueError, match=re.escape(name)):
        _construct_with(cls, factory, name, value)


# ---------------------------------------------------------------------------
# The round trip: writer and reader must agree
# ---------------------------------------------------------------------------


def test_the_writer_cannot_produce_a_matrix_the_reader_refuses(tmp_path: Path) -> None:
    """The sharpest form of the defect: this repo's writer built a file this
    repo's reader exits 2 on.

    `scripts/plot_latency.py::_load_matrix` wraps every numeric it reads in
    `_reject_bool_numeric` (#108). `LoadCell` wrapped none, so::

        LoadCell(ingest_seconds=True, mean_recall_at_k=True, throughput_qps=True)
        -> accepted; matrix.json: {"ingest_seconds": true, ...}
        -> plot_latency.py <that> --out-dir ...
           "not a valid load matrix: ingest_seconds must be a number, not a
            bool; got True"   (exit 2)

    Now the boolean cannot be constructed, so the unreadable file cannot exist.
    The valid round trip is asserted alongside it, because a guard that also
    broke the honest path would pass the first assertion for the wrong reason.
    """
    workload = _workload()
    for field in ("ingest_seconds", "mean_recall_at_k", "throughput_qps", "concurrency"):
        kwargs = _loadcell_kwargs()
        kwargs[field] = True
        with pytest.raises(ValueError, match=re.escape(field)):
            LoadCell(**kwargs)

    cell = LoadCell(**_loadcell_kwargs())
    matrix = LoadMatrix(run_id="r1", backend="stub", workload=workload, cells=(cell,))
    dump_load_matrix_json(tmp_path, matrix=matrix, force=True)
    payload = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    written = payload["cells"][0]
    for field in ("ingest_seconds", "mean_recall_at_k", "throughput_qps", "concurrency"):
        assert not isinstance(written[field], bool), f"{field} serialized as a JSON boolean"
        assert isinstance(written[field], (int, float))


def test_a_boolean_serializes_as_the_json_token_true() -> None:
    """Why a boolean is not merely a type nit, as data.

    `json.dumps` writes `true`, not `1`. A consumer that reads
    `mean_recall_at_k` as a number gets a *perfect* recall from a run that
    measured nothing — the handoff-section-10 fabricated-benchmark class three
    reader guards in this repo already refuse.
    """
    assert json.dumps({"mean_recall_at_k": True}) == '{"mean_recall_at_k": true}'
    # And why the old guard could not see it: both of its arms say yes.
    assert math.isfinite(True)
    assert not True < 0


# ---------------------------------------------------------------------------
# The shared predicate's own contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, True),
        (1.0, True),
        (0, True),
        (7, True),
        (1e308, True),
        (True, False),
        (False, False),
        (-0.1, False),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
        ("1.0", False),
        (None, False),
        (b"1", False),
        ([1.0], False),
    ],
    ids=repr,
)
def test_is_valid_number_default_domain(value: object, expected: bool) -> None:
    assert is_valid_number(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, True), (0.5, True), (1.0, True), (1.0000001, False), (True, False), (-0.0, True)],
    ids=repr,
)
def test_is_valid_number_upper_bound_is_inclusive(value: object, expected: bool) -> None:
    """`mean_recall_at_k`'s `[0, 1]` is the one field with a `maximum`, and both
    endpoints are in-domain: `1.0` is exact recall, which is a real result.
    """
    assert is_valid_number(value, maximum=1.0) is expected


def test_is_valid_number_rejects_bool_before_the_range_can_admit_it() -> None:
    """Order matters, and this is the arm that carries the whole finding.

    `True` is `1`, so every range this package uses admits it: `>= 0` yes,
    `[0, 1]` yes. Only the `isinstance` arm can refuse it, which is why it runs
    first and why a guard written as `isfinite(x) or x < 0` cannot be patched
    by tightening its bounds.
    """
    assert is_valid_number(1.0, maximum=1.0) is True
    assert is_valid_number(True, maximum=1.0) is False
    assert is_valid_number(0.0) is True
    assert is_valid_number(False) is False
