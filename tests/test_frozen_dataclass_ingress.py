"""A frozen dataclass must copy a mutable field in, not alias the caller's (#135).

`frozen=True` stops the *field* being rebound and does nothing about the object
behind it. Two classes here took a `dict` from the caller and kept it:

    mine = {"x": InstancePrice("x", "r", 1.0, 1, 1.0)}
    t = PriceTable(snapshot_date="d", instances=mine, ...)
    mine["INJECTED"] = ...;  mine["x"] = InstancePrice("x", "r", 999.0, 1, 1.0)
    -> "INJECTED" in t.instances     True
    -> t.instances["x"].usd_per_hour 999.0

`PriceTable` is the serious one. It carries `snapshot_date` and `source_url`
precisely so a published cost is attributable, and `prices.py` states the rule
for changing a price: bump the date, update the source, re-run
`scripts/cost_table.py`. An aliased dict routes around all three — the table
still truthfully reports `snapshot_date` while holding prices that are not the
snapshot's.

`BenchmarkResult.to_dict` already copied on the way *out*, with a comment
naming the goal — "so callers can't mutate the frozen dataclass through the
dict". That is one of the two directions.

The population is discovered, not listed: a fifth frozen dataclass with a
mutable field is covered by this file the day it is written.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil

import pytest

import vector_bench
from vector_bench.cost import EbsGp3Price, InstancePrice, PriceTable
from vector_bench.harness import LatencyStats, Workload
from vector_bench.prices import _DEFAULT_EBS, aws_us_east_1_snapshot


@pytest.fixture
def benchmark_result_kwargs() -> dict:
    """A valid `BenchmarkResult` payload, mirroring `test_harness._make_result`."""
    return {
        "run_id": "r",
        "backend": "stub",
        "workload": Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1),
        "ingest_seconds": 0.5,
        "ingest_rows_per_sec": 40.0,
        "query_latency": LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0),
        "mean_recall_at_k": 0.9,
        "started_at": "2026-06-26T00:00:00Z",
        "git_sha": None,
        "cost_per_query_usd": None,
    }


#: Field types that cannot be mutated behind a reference, so they need no copy.
IMMUTABLE_CONTAINERS = ("tuple", "frozenset")


def _dataclasses_in_package():
    """Every dataclass defined under `vector_bench`, discovered."""
    found = {}

    def walk(pkg):
        for mod in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            module = importlib.import_module(mod.name)
            if mod.ispkg:
                walk(module)
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and dataclasses.is_dataclass(obj)
                    and obj.__module__ == module.__name__
                ):
                    found[f"{module.__name__}.{name}"] = obj

    walk(vector_bench)
    return found


def _mutable_fields(cls) -> list[str]:
    out = []
    for f in dataclasses.fields(cls):
        t = str(f.type)
        if any(k in t for k in IMMUTABLE_CONTAINERS):
            continue
        if any(k in t for k in ("dict", "list", "set[", "Mapping", "MutableMapping")):
            out.append(f.name)
    return out


def _frozen_with_mutable_fields() -> dict[str, list[str]]:
    return {
        name: muts
        for name, cls in _dataclasses_in_package().items()
        if cls.__dataclass_params__.frozen and (muts := _mutable_fields(cls))
    }


def test_the_discovery_finds_the_two_known_classes() -> None:
    """Anti-vacuous: every assertion below loops over this, so `{}` would pass
    all of them. A hand-listed guard is what let these two sit unnoticed."""
    found = _frozen_with_mutable_fields()
    assert "vector_bench.cost.PriceTable" in found
    assert "vector_bench.harness.BenchmarkResult" in found
    assert found["vector_bench.cost.PriceTable"] == ["instances"]
    assert found["vector_bench.harness.BenchmarkResult"] == ["extra"]


def test_every_frozen_dataclass_with_a_mutable_field_copies_it_in() -> None:
    """The rule, over the discovered population.

    Source-based because there is no uniform way to construct every dataclass
    here; the behavioural proof for the two real ones is below.
    """
    offenders = []
    for name, muts in _frozen_with_mutable_fields().items():
        cls = _dataclasses_in_package()[name]
        post_init = getattr(cls, "__post_init__", None)
        src = inspect.getsource(post_init) if post_init else ""
        for field_name in muts:
            if f'object.__setattr__(self, "{field_name}"' not in src:
                offenders.append(f"{name}.{field_name}")
    assert offenders == [], (
        f"frozen dataclasses aliasing a caller's mutable field: {offenders}. "
        "Copy it in `__post_init__` via `object.__setattr__`."
    )


def test_price_table_does_not_alias_the_callers_dict() -> None:
    mine = {"x": InstancePrice("x", "r", 1.0, 1, 1.0)}
    table = PriceTable(snapshot_date="2026-05-17", instances=mine, ebs=_DEFAULT_EBS, source_url="u")
    mine["INJECTED"] = InstancePrice("INJECTED", "r", 0.0, 1, 1.0)
    mine["x"] = InstancePrice("x", "r", 999.0, 1, 1.0)

    assert "INJECTED" not in table.instances
    assert table.instances["x"].usd_per_hour == 1.0


def test_price_table_shallow_is_sufficient_because_its_values_are_frozen_scalars() -> None:
    """Pins the reasoning behind the *depth* choice, not just the copy.

    `PriceTable.instances` takes a shallow copy because `InstancePrice` is
    frozen and holds only scalars, so its values cannot be mutated behind the
    copy. That is a property of `InstancePrice`, so it is asserted here — if it
    ever grows a mutable field, this fails loudly instead of the shallow copy
    silently becoming the wrong choice.
    """
    assert InstancePrice.__dataclass_params__.frozen
    for f in dataclasses.fields(InstancePrice):
        assert not _mutable_fields(InstancePrice), f"InstancePrice.{f.name} is mutable now"
    # Same argument for the field that deliberately takes no copy at all.
    assert EbsGp3Price.__dataclass_params__.frozen
    assert _mutable_fields(EbsGp3Price) == []


def test_benchmark_result_does_not_alias_the_callers_dict(benchmark_result_kwargs) -> None:
    from vector_bench.harness import BenchmarkResult

    mine: dict = {"note": "original", "nested": {"k": "original"}}
    result = BenchmarkResult(**{**benchmark_result_kwargs, "extra": mine})

    mine["INJECTED"] = "after construction"
    mine["note"] = "MUTATED"
    mine["nested"]["k"] = "MUTATED"

    assert "INJECTED" not in result.extra
    assert result.extra["note"] == "original"
    # Deep, unlike PriceTable: `extra` is `dict[str, Any]`, so a nested
    # container is exactly what a caller puts there.
    assert result.extra["nested"]["k"] == "original"


def test_benchmark_result_still_copies_on_the_way_out(benchmark_result_kwargs) -> None:
    """The egress half that already worked. A fix to ingress must not lose it."""
    from vector_bench.harness import BenchmarkResult

    result = BenchmarkResult(**{**benchmark_result_kwargs, "extra": {"note": "original"}})
    dumped = result.to_dict()
    dumped["extra"]["note"] = "MUTATED"
    assert result.extra["note"] == "original"


def test_the_committed_snapshot_still_returns_independent_tables() -> None:
    """`aws_us_east_1_snapshot`'s docstring promises callers can mutate their
    copy without side effects. That was already true; pin it so the ingress
    copy cannot break it."""
    a = aws_us_east_1_snapshot()
    a.instances["m6i.large"] = InstancePrice("m6i.large", "eu-west-1", 9.99, 2, 8.0)
    a.instances.pop("r6i.xlarge", None)

    b = aws_us_east_1_snapshot()
    assert b.instances["m6i.large"].usd_per_hour == pytest.approx(0.0960)
    assert "r6i.xlarge" in b.instances
