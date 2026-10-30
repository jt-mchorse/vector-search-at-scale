"""Every `cost_table.py` command the demo prints is one an operator can run (#171).

`capture_demo.sh` closed with `python scripts/cost_table.py --results-dir
results/load  # real qps`. `--run-id` defaults to the stub run, so that read no
real measurement (D-016 moved real throughput to `--load-results`), and with no
`--out` it overwrote the committed `docs/cost_per_query.md`.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import TIER_DIM, TIER_N_VECTORS  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "scripts" / "capture_demo.sh"


def _printed_cost_commands() -> list[tuple[list[str], str]]:
    """(argv, trailing comment) for each cost_table.py command the script prints."""
    out = []
    for line in DEMO.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*printf '\s*(python scripts/cost_table\.py[^']*?)(?:\\n)?'", line)
        if m:
            command, _, comment = m.group(1).partition("#")
            out.append((shlex.split(command), comment.strip()))
    return out


def test_the_printed_commands_are_found() -> None:
    assert len(_printed_cost_commands()) >= 2


def test_every_printed_cost_command_writes_outside_docs() -> None:
    for argv, _ in _printed_cost_commands():
        assert "--out" in argv, f"no --out (defaults to docs/cost_per_query.md): {argv}"
        assert not argv[argv.index("--out") + 1].startswith("docs/"), argv


def test_a_command_promising_real_numbers_supplies_them() -> None:
    real = [argv for argv, comment in _printed_cost_commands() if "real" in comment]
    assert real, "the demo's real-backend hint is gone"
    for argv in real:
        assert "--load-results" in argv, f"'real' without --load-results reads the stub run: {argv}"


# `vector-bench run|load` and `python scripts/cost_table.py` lines the demo
# prints, in order, as argv lists (#300).
_PRINTED = re.compile(
    r"\s*printf '\s*((?:vector-bench (?:run|load)|python scripts/cost_table\.py)[^']*?)(?:\\n)?'"
)


def _printed_commands() -> list[list[str]]:
    out = []
    for line in DEMO.read_text(encoding="utf-8").splitlines():
        m = _PRINTED.match(line)
        if m:
            out.append(shlex.split(m.group(1).partition("#")[0]))
    return out


def _flag(argv: list[str], name: str, default: str | None = None) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else default


def test_the_producer_before_a_load_results_line_writes_what_it_reads() -> None:
    # The next stop printed `vector-bench run --backend pgvector --n 1000000 ...`
    # before `--load-results 1m=results/load/<run-dir>`. `run` writes
    # results/<id>.json, never the c001.json that line reads, so following the
    # demo literally ended at "expected .../c001.json to exist", rc 2 (#300).
    commands = _printed_commands()
    pairs = 0
    for i, argv in enumerate(commands):
        if argv[:2] != ["python", "scripts/cost_table.py"] or "--load-results" not in argv:
            continue
        tier, _, path = argv[argv.index("--load-results") + 1].partition("=")
        producers = [c for c in commands[:i] if c[0] == "vector-bench"]
        assert producers, f"no producer printed before {argv}"
        producer = producers[-1]
        assert producer[1] == "load", f"{producer} writes no c001.json for {argv}"
        assert _flag(producer, "--n") == str(TIER_N_VECTORS[tier]), producer
        assert _flag(producer, "--dim") == str(TIER_DIM), producer
        results_dir = _flag(producer, "--results-dir", "results/load")
        assert f"{results_dir}/{_flag(producer, '--run-id')}" == path, (producer, argv)
        pairs += 1
    assert pairs >= 1, "the demo's --load-results line is gone"
