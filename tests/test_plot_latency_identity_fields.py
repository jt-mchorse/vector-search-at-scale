"""`plot_latency` refuses a `run_id` / `backend` the writer cannot produce (#275).

Both values come from the input `matrix.json` and go into the chart filename
`{run_id}_{backend}_n{n}.png`. Measured on main 69329e7: `run_id: "../escaped"`
wrote the chart into the parent of `--out-dir` at exit 0, `run_id: 7` charted
at exit 0, and `backend: 5` / `null` reached `_escape_cell`'s `.replace` as an
`AttributeError` traceback at exit 1. The writer refuses all of these
(`_check_run_id`, #199) and records the backend's `str` name, so the reader
now applies the same rules and exits 2 (#107, #217).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "results" / "load" / "stub-10k" / "matrix.json"
_SPEC = importlib.util.spec_from_file_location(
    "plot_latency_identity_fields", REPO_ROOT / "scripts" / "plot_latency.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_latency)


def _set_top(field, value):
    return lambda d: d.__setitem__(field, value)


def _set_cell(field, value):
    return lambda d: d["cells"][0].__setitem__(field, value)


BAD = [
    ("run_id-dotdot-prefix", _set_top("run_id", "../escaped"), "run_id"),
    ("run_id-dotdot", _set_top("run_id", ".."), "run_id"),
    ("run_id-sep", _set_top("run_id", "team/baseline"), "run_id"),
    ("run_id-empty", _set_top("run_id", ""), "run_id"),
    ("run_id-int", _set_top("run_id", 7), "run_id"),
    ("run_id-null", _set_top("run_id", None), "run_id"),
    ("backend-int", _set_top("backend", 5), "backend"),
    ("backend-null", _set_top("backend", None), "backend"),
    ("backend-sep", _set_top("backend", "../../escaped"), "backend"),
    ("cell-run_id-int", _set_cell("run_id", 5), "cell 0 run_id"),
    ("cell-run_id-dotdot", _set_cell("run_id", "../x"), "cell 0 run_id"),
    ("cell-backend-null", _set_cell("backend", None), "cell 0 backend"),
]


def _edited(tmp_path: Path, mutate) -> Path:
    data = json.loads(COMMITTED.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "in" / "matrix.json"
    path.parent.mkdir()
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(("mutate", "needle"), [b[1:] for b in BAD], ids=[b[0] for b in BAD])
def test_bad_identity_field_exits_2_and_writes_nothing(
    tmp_path: Path, capsys, mutate, needle: str
) -> None:
    pytest.importorskip("matplotlib")
    matrix = _edited(tmp_path, mutate)
    out_dir = tmp_path / "work" / "out"
    rc = plot_latency.main([str(matrix), "--out-dir", str(out_dir)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not a valid load matrix" in captured.err
    assert needle in captured.err
    assert "Traceback" not in captured.err
    # Nothing printed as a table, and no PNG anywhere under tmp_path -- the
    # `../escaped` row used to land in `work/`, beside `--out-dir`.
    assert captured.out == ""
    assert list(tmp_path.rglob("*.png")) == []


def test_committed_matrix_still_plots(tmp_path: Path, capsys) -> None:
    pytest.importorskip("matplotlib")
    matrix = tmp_path / "matrix.json"
    shutil.copyfile(COMMITTED, matrix)
    out_dir = tmp_path / "out"
    rc = plot_latency.main([str(matrix), "--out-dir", str(out_dir)])
    assert rc == 0
    assert [p.name for p in out_dir.iterdir()] == ["stub-10k_stub_n10000.png"]
    assert "| concurrency |" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["stub", "pgvector", "qdrant", "weaviate", "hnsw-sim", "a|b"])
def test_every_writer_backend_name_loads(tmp_path: Path, backend: str) -> None:
    # Control: the names the writer records (and a pipe, which #125 escapes in
    # the table rather than refusing) are single path components and load.
    def mutate(d):
        d["backend"] = backend
        for c in d["cells"]:
            c["backend"] = backend

    m = plot_latency._load_matrix(_edited(tmp_path, mutate))
    assert m.backend == backend
