"""Every documented `vector-bench load` command must run at the tiers' dimension (#297).

The cost table reads only the `c001.json` that `vector-bench load` writes, and
since #295 it marks a run whose `workload.dim` is not `TIER_DIM` as "measured at
dim=<d>, not 384". `load` defaults to `--dim 64`, so a documented command that
leaves `--dim` off produces a run that can never be a `(real)` row. #293 fixed
the README's `vector-bench run` examples, whose lock parses only that block;
the `load` commands that actually feed the cost table were not covered.

The population is every `vector-bench load` invocation that carries flags, in
the README, `docs/*.md` and `scripts/*.sh`, plus the hint `cost_table.py` prints
when `c001.json` is missing. A bare mention ("go through `vector-bench load`")
is prose, not a command, and is not in the population.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import TIER_DIM, main  # noqa: E402
from vector_bench.cli import main as cli_main  # noqa: E402

# `vector-bench load` followed by at least one flag, up to the end of the code
# span or line. A `\` line continuation is folded first, so a multi-line shell
# example is one command. A `printf '...\n'` line in a script ends at its `\n'`
# (#300), so the escape and the closing quote are not part of the command.
_LOAD_CMD = re.compile(r"vector-bench load((?: +--[^`\n'\\]*))")


def _documented_files() -> list[Path]:
    return [
        _REPO_ROOT / "README.md",
        *sorted((_REPO_ROOT / "docs").glob("*.md")),
        *sorted((_REPO_ROOT / "scripts").glob("*.sh")),
    ]


def _commands_in(text: str) -> list[str]:
    folded = re.sub(r" *\\\n\s*", " ", text)
    return ["vector-bench load" + m.group(1).rstrip() for m in _LOAD_CMD.finditer(folded)]


def _documented_commands() -> list[tuple[str, str]]:
    out = []
    for path in _documented_files():
        for cmd in _commands_in(path.read_text(encoding="utf-8")):
            out.append((str(path.relative_to(_REPO_ROOT)), cmd))
    return out


def _hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    rc = main(["--run-id", "no-such-run-297", "--out", str(tmp_path / "out.md")])
    assert rc == 2
    (cmd,) = _commands_in(capsys.readouterr().err)
    return cmd


def _dim_of(cmd: str) -> int | None:
    argv = shlex.split(cmd)
    if "--dim" not in argv:
        return None
    return int(argv[argv.index("--dim") + 1])


def test_population_includes_the_readme_latency_under_load_command() -> None:
    # Anti-vacuity: a regex that matched nothing would make every lock below green.
    found = _documented_commands()
    assert any(src == "README.md" and "--concurrency 1,10,100" in c for src, c in found), found


def test_commands_in_folds_shell_continuations() -> None:
    text = (
        "```bash\nvector-bench load --backend pgvector --n 1000000 \\\n  --dim 384 --run-id x\n```"
    )
    assert _commands_in(text) == [
        "vector-bench load --backend pgvector --n 1000000 --dim 384 --run-id x"
    ]


def test_commands_in_skips_bare_mentions() -> None:
    assert _commands_in("Concurrency studies go through `vector-bench load`, which") == []


_DOCUMENTED = _documented_commands()


@pytest.mark.parametrize(
    ("source", "cmd"), _DOCUMENTED, ids=[f"{src}-{i}" for i, (src, _) in enumerate(_DOCUMENTED)]
)
def test_documented_load_command_passes_the_tier_dim(source: str, cmd: str) -> None:
    assert _dim_of(cmd) == TIER_DIM, f"{source}: `{cmd}` does not pass --dim {TIER_DIM}"


def test_cost_table_missing_c001_hint_passes_the_tier_dim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cmd = _hint(tmp_path, capsys)
    assert _dim_of(cmd) == TIER_DIM, cmd


def _fill(cmd: str, results_dir: Path) -> list[str]:
    """Substitute the placeholders of a documented command and point it at `results_dir`."""
    subs = {"<backend>": "stub", "<b>": "stub", "<N>": "300", "<run_id>": "r297", "<id>": "r297"}
    argv = []
    for tok in shlex.split(cmd)[2:]:  # drop `vector-bench load`
        argv.append(subs.get(tok, tok))
    if "--results-dir" in argv:
        argv[argv.index("--results-dir") + 1] = str(results_dir)
    else:
        argv += ["--results-dir", str(results_dir)]
    return ["load", *argv, "--queries", "100"]


def _row_note_after_running(cmd: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Run `cmd` against the stub, stand it in for a 1m pgvector run, return the row's source cell."""
    results = tmp_path / "load"
    assert cli_main(_fill(cmd, results)) == 0
    c001 = results / "r297" / "c001.json"
    body = json.loads(c001.read_text(encoding="utf-8"))
    # Only what a real 1m-tier run of the same command would change: the
    # engine and the vector count. The dim is whatever the command produced.
    body["backend"] = "pgvector"
    body["workload"]["n_vectors"] = 1_000_000
    c001.write_text(json.dumps(body), encoding="utf-8")
    out = tmp_path / "ct.md"
    capsys.readouterr()
    assert main(["--load-results", f"1m={results / 'r297'}", "--out", str(out)]) == 0
    (row,) = [
        ln
        for ln in out.read_text(encoding="utf-8").splitlines()
        if ln.startswith("| 1m | pgvector |")
    ]
    return row.rsplit("|", 2)[1]


def test_running_the_hint_yields_a_real_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cmd = _hint(tmp_path, capsys)
    cell = _row_note_after_running(cmd, tmp_path, capsys)
    assert cell.strip().endswith("(real)"), cell


def test_running_the_readme_command_yields_a_real_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (cmd,) = [c for src, c in _documented_commands() if src == "README.md" and "--concurrency" in c]
    cell = _row_note_after_running(cmd, tmp_path, capsys)
    assert cell.strip().endswith("(real)"), cell


def test_the_same_command_without_dim_is_marked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Control: the pipeline above can tell the two apart. Dropping `--dim` falls
    # back to the CLI's default (64, unchanged by #297), which the marker names.
    cmd = re.sub(r" --dim \d+", "", _hint(tmp_path, capsys))
    cell = _row_note_after_running(cmd, tmp_path, capsys)
    assert f"measured at dim=64, not {TIER_DIM}" in cell, cell
