"""`_check_run_id` refuses control characters and line separators, not only NUL (#281).

Measured on main 75fa9e6: `load --run-id "$(printf 'base\\nline')"` exited 0
into a results directory whose name holds the newline, `run --run-id
"$(printf 'r\\rx')"` exited 0, and `plot_latency` over the matrix exited 0,
wrote `base\\nline_stub_n200.png`, and split its markdown header row over four
physical lines. #278 had refused NUL alone; this is the rest of its class.

The class is taken from the Unicode catalog (Cc, Zl, Zp), and one test searches
the whole code-point space for line breaks `str.splitlines()` recognises so a
member a hand list would miss (U+001C-U+001E, U+0085) is held to the rule too.
`_check_run_id` is the one rule `run`, `load` and `plot_latency`'s `run_id` and
`backend` all go through (#278), so each of those surfaces is exercised here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.cli import main as cli_main
from vector_bench.harness import Workload, _check_run_id, run_benchmark
from vector_bench.load import run_under_load

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "results" / "load" / "stub-10k" / "matrix.json"
_SPEC = importlib.util.spec_from_file_location(
    "plot_latency_control_chars", REPO_ROOT / "scripts" / "plot_latency.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
plot_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plot_latency)

SMALL = ["--backend", "stub", "--n", "40", "--queries", "4", "--dim", "8", "--top-k", "3"]

#: The ones a shell can put in argv and that broke the table on main, plus the
#: two Unicode line separators and NEL (a C1 control that `splitlines` breaks on).
SAMPLE = ["\n", "\r", "\r\n", "\t", "\x0b", "\x0c", "\x1c", "\x1f", "\x7f", "\x85", " ", " "]


def _catalog() -> list[str]:
    return [
        chr(cp)
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) in ("Cc", "Zl", "Zp")
    ]


def test_every_catalog_member_is_refused() -> None:
    members = _catalog()
    # 65 Cc (C0 + DEL + C1) + U+2028 + U+2029. If a Unicode version moves this,
    # the count changes and the rule still follows the catalog.
    assert len(members) == 67
    accepted = []
    for ch in members:
        try:
            _check_run_id(f"a{ch}b")
        except ValueError:
            continue
        accepted.append(f"U+{ord(ch):04X}")
    assert accepted == []


def test_every_splitlines_break_is_refused() -> None:
    """Search the space rather than trust the catalog to cover line breaks."""
    breaks = [chr(cp) for cp in range(sys.maxunicode + 1) if len(f"a{chr(cp)}b".splitlines()) > 1]
    assert breaks, "the search found no line breaks at all"
    for ch in breaks:
        with pytest.raises(ValueError, match="single path component"):
            _check_run_id(f"base{ch}line")


@pytest.mark.parametrize("run_id", ["base-line", "café", "基准", "with space", "a|b", "stub-10k"])
def test_printable_run_ids_still_pass(run_id: str) -> None:
    # Controls: non-ASCII letters, a space, and a pipe (#125 escapes it in the
    # table rather than refusing it) are not in the class.
    _check_run_id(run_id)


def _files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("ch", ["\n", "\r", " "], ids=["LF", "CR", "LS"])
@pytest.mark.parametrize("command", ["run", "load"])
def test_cli_refuses_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    ch: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = [command, *SMALL, "--run-id", f"base{ch}line", "--results-dir", str(tmp_path / "r")]
    if command == "load":
        argv += ["--concurrency", "1"]
    rc = cli_main(argv)
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: run_id must be a single path component"), err
    assert _files_under(tmp_path) == []


def test_library_writers_raise(tmp_path: Path) -> None:
    workload = Workload(n_vectors=40, dim=8, n_queries=4, top_k=3, seed=1)
    with pytest.raises(ValueError, match="control character"):
        run_benchmark(StubBackend(), workload, run_id="a\nb", results_dir=tmp_path / "r")
    with pytest.raises(ValueError, match="control character"):
        run_under_load(
            StubBackend(),
            workload,
            run_id="a\rb",
            concurrency_levels=(1,),
            results_dir=tmp_path / "l",
        )
    assert _files_under(tmp_path) == []


def _edited(tmp_path: Path, mutate) -> Path:
    data = json.loads(COMMITTED.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "in" / "matrix.json"
    path.parent.mkdir()
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _top(field: str, value: str):
    return lambda d: d.__setitem__(field, value)


def _cell(field: str, value: str):
    return lambda d: d["cells"][0].__setitem__(field, value)


READER = [
    ("run_id-LF", _top("run_id", "base\nline"), "run_id"),
    ("run_id-CR", _top("run_id", "base\rline"), "run_id"),
    ("run_id-LS", _top("run_id", "base line"), "run_id"),
    ("run_id-NEL", _top("run_id", "base\x85line"), "run_id"),
    ("backend-LF", _top("backend", "st\nub"), "backend"),
    ("cell-run_id-LF", _cell("run_id", "base\nline"), "cell 0 run_id"),
    ("cell-backend-PS", _cell("backend", "st ub"), "cell 0 backend"),
]


@pytest.mark.parametrize(("mutate", "needle"), [r[1:] for r in READER], ids=[r[0] for r in READER])
def test_plot_latency_refuses_before_printing_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate, needle: str
) -> None:
    pytest.importorskip("matplotlib")
    matrix = _edited(tmp_path, mutate)
    rc = plot_latency.main([str(matrix), "--out-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not a valid load matrix" in captured.err
    assert needle in captured.err
    assert "control character" in captured.err
    assert captured.out == ""
    assert list(tmp_path.rglob("*.png")) == []
