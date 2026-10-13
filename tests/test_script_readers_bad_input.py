"""Every script reader turns a wrong-shape or undecodable file into exit 2 (#217).

- `plot_hnsw_frontier.py` caught only bad UTF-8 and bad JSON; `render` raised on
  a wrong-shape grid outside every exit-2 arm, so `{}`, `[]`, `{"cells": 5}` and
  a grid with no `workload` ended at exit 1 with a traceback. Its siblings
  refuse the same inputs with exit 2.
- `json.loads` raises `RecursionError` on deep nesting (about 100,000 levels on
  3.11, which CI runs) and a plain `ValueError` past the 4300-digit int limit.
  Every script caught `JSONDecodeError` only.
- `cost_table`'s `matrix.json` read (#182) called `.get` on whatever parsed, so
  `[]` was an `AttributeError` at exit 1.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vector_bench import io_utils

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


frontier = _load("plot_hnsw_frontier")
latency = _load("plot_latency")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import cost_table  # noqa: E402

_DEEP = "[" * 100_000 + "]" * 100_000
_LONG_INT = '{"cells": [{"recall": ' + "1" * 5000 + "}]}"


@pytest.mark.parametrize(
    "text",
    [
        '{"cells": {"a": 1}}',
        '{"cells": "abc"}',
        '{"cells": 5}',
        "{}",
        '{"cells": null}',
        '{"cells": []}',
        "[]",
        '"x"',
        _DEEP,
        _LONG_INT,
    ],
    ids=[
        "cells-dict",
        "cells-str",
        "cells-int",
        "empty",
        "cells-null",
        "cells-empty",
        "list",
        "string",
        "deep",
        "long-int",
    ],
)
def test_frontier_refuses_a_bad_grid_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    grid = tmp_path / "grid.json"
    grid.write_text(text, encoding="utf-8")
    rc = frontier.main([str(grid), "--out-png", str(tmp_path / "a.png")])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "Traceback" not in err
    assert not (tmp_path / "a.png").exists()


def test_frontier_refuses_a_grid_without_a_workload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grid = tmp_path / "grid.json"
    cell = {
        "M": 8,
        "ef_construction": 64,
        "ef_search": 32,
        "recall": 0.9,
        "p95_ms": 1.0,
        "p50_ms": 0.5,
        "ingest_seconds": 1.0,
        "qps": 100.0,
    }
    grid.write_text(json.dumps({"cells": [cell]}), encoding="utf-8")
    rc = frontier.main([str(grid), "--out-png", str(tmp_path / "a.png")])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "workload" in err


@pytest.mark.parametrize("text", [_DEEP, "[]"], ids=["deep", "list"])
def test_plot_latency_refuses_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    matrix = tmp_path / "matrix.json"
    matrix.write_text(text, encoding="utf-8")
    rc = latency.main([str(matrix)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("matrix", "c001"),
    [
        ("[]", '{"throughput_qps": 100.0}'),
        (_DEEP, '{"throughput_qps": 100.0}'),
        (None, _DEEP),
    ],
    ids=["matrix-list", "matrix-deep", "c001-deep"],
)
def test_cost_table_refuses_a_bad_load_results_dir_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], matrix: str | None, c001: str
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "c001.json").write_text(c001, encoding="utf-8")
    if matrix is not None:
        (run / "matrix.json").write_text(matrix, encoding="utf-8")
    rc = cost_table.main(["--out", str(tmp_path / "out.md"), "--load-results", f"1m={run}"])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "Traceback" not in err


def test_loads_json_translates_a_recursion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Forced: 3.14 decodes 100,000 levels, so the real input only bites on CI.
    def deep(_text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(io_utils.json, "loads", deep)
    with pytest.raises(json.JSONDecodeError, match="nested too deeply"):
        io_utils.loads_json("[[[]]]")


def test_no_script_or_module_calls_json_loads_directly() -> None:
    offenders = []
    for path in sorted(
        [*REPO_ROOT.glob("scripts/*.py"), *REPO_ROOT.glob("src/vector_bench/**/*.py")]
    ):
        if path.name == "io_utils.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"loads", "load"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, f"route these through io_utils.loads_json: {offenders}"
