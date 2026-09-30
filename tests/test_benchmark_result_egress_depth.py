"""`BenchmarkResult.to_dict` copies `extra` deep, as `__post_init__` does (#154).

#135 made the ingress copy deep and argued it from the field's type: `extra` is
`dict[str, Any]`, free-form, "so a nested container is exactly what a caller
puts there". The same comment quoted the egress copy -- `dict(self.extra)` --
as the half that already worked. It was one level deep, and both egress arms
used a flat dict, so nothing could tell. Measured at `0177c04`: editing
`to_dict()["extra"]["hnsw"]["ef_search"]` rewrote the frozen record and every
later `to_json()`.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from vector_bench.harness import BenchmarkResult, LatencyStats, Workload, dump_benchmark_json

_SRC = Path(__file__).resolve().parent.parent / "src" / "vector_bench"

_EXTRA: dict[str, Any] = {"hnsw": {"ef_search": 64, "m": 16}, "notes": ["a"], "flat": 1}


def _result(extra: dict[str, Any]) -> BenchmarkResult:
    return BenchmarkResult(
        run_id="r",
        backend="stub",
        workload=Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1),
        ingest_seconds=0.5,
        ingest_rows_per_sec=40.0,
        query_latency=LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0),
        mean_recall_at_k=0.9,
        started_at="2026-06-26T00:00:00Z",
        git_sha=None,
        cost_per_query_usd=None,
        extra=extra,
    )


def _edit_nested(payload: dict[str, Any]) -> None:
    payload["extra"]["hnsw"]["ef_search"] = 9999
    payload["extra"]["notes"].append("INJECTED")


@pytest.mark.parametrize("method", ["to_dict", "to_json"])
def test_a_nested_edit_to_the_payload_does_not_reach_the_record(method: str) -> None:
    result = _result(copy.deepcopy(_EXTRA))
    _edit_nested(getattr(result, method)())
    assert result.extra == _EXTRA
    assert result.to_json()["extra"] == _EXTRA


def test_a_nested_edit_does_not_reach_the_file_written_afterwards(tmp_path: Path) -> None:
    """Through the writer: the bytes `dump_benchmark_json` puts on disk after a
    consumer edited an earlier payload."""
    result = _result(copy.deepcopy(_EXTRA))
    _edit_nested(result.to_dict())
    path = dump_benchmark_json(tmp_path / "r.json", result=result)
    assert json.loads(path.read_text(encoding="utf-8"))["extra"] == _EXTRA


def test_two_payloads_do_not_share_nested_state() -> None:
    result = _result(copy.deepcopy(_EXTRA))
    first, second = result.to_dict(), result.to_dict()
    _edit_nested(first)
    assert second["extra"] == _EXTRA


# ----------------------------------------------------------------------
# The population: every serialiser, derived
# ----------------------------------------------------------------------

_MUTABLE = ("dict", "list", "set", "Mapping", "MutableMapping")


def _serialisers() -> list[tuple[str, str, dict[str, str], ast.FunctionDef]]:
    """`(module, class, {field: annotation}, to_dict)` for every dataclass
    under `vector_bench` that has a `to_dict`, by `rglob`."""
    out = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            fields = {
                s.target.id: ast.unparse(s.annotation)
                for s in cls.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
            method = next(
                (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "to_dict"),
                None,
            )
            if method is not None:
                out.append((path.name, cls.name, fields, method))
    return out


def _mutable_fields(fields: dict[str, str]) -> set[str]:
    return {f for f, a in fields.items() if a.split("[", 1)[0].split(".")[-1] in _MUTABLE}


def _unsafe_reads(method: ast.FunctionDef, mutable: set[str]) -> list[str]:
    """`self.<mutable field>` reads that are not the direct argument of a
    `copy.deepcopy(...)` call."""
    guarded = {
        id(call.args[0])
        for call in ast.walk(method)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == "copy.deepcopy" and call.args
    }
    return [
        ast.unparse(node)
        for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr in mutable
        and id(node) not in guarded
    ]


def test_every_serialiser_deep_copies_its_mutable_container_fields() -> None:
    offenders = [
        f"{module}:{cls}.to_dict reads {read}"
        for module, cls, fields, method in _serialisers()
        for read in _unsafe_reads(method, _mutable_fields(fields))
    ]
    assert not offenders, (
        f"{offenders}: a free-form container handed out by reference (or one level "
        f"deep) lets a payload edit rewrite the frozen record (#154)."
    )


def test_the_population_is_not_empty_and_extra_is_in_it() -> None:
    """A pass over no serialisers is not a pass."""
    found = {f"{cls}.{f}" for _, cls, fields, _ in _serialisers() for f in _mutable_fields(fields)}
    assert found == {"BenchmarkResult.extra"}, found
    assert len(_serialisers()) >= 5


def test_the_detector_sees_a_shallow_copy() -> None:
    """Control for the detector: `dict(self.extra)` -- the shape #154 fixed --
    is an unsafe read, and `copy.deepcopy(self.extra)` is not."""
    shallow = ast.parse('def to_dict(self):\n    return {"extra": dict(self.extra)}').body[0]
    deep = ast.parse('def to_dict(self):\n    return {"extra": copy.deepcopy(self.extra)}').body[0]
    assert _unsafe_reads(shallow, {"extra"}) == ["self.extra"]  # type: ignore[arg-type]
    assert _unsafe_reads(deep, {"extra"}) == []  # type: ignore[arg-type]
