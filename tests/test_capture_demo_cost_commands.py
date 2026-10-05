"""Every `cost_table.py` command the demo prints is one an operator can run (#171).

`capture_demo.sh` closed with `python scripts/cost_table.py --results-dir
results/load  # real qps`. `--run-id` defaults to the stub run, so that read no
real measurement (D-016 moved real throughput to `--load-results`), and with no
`--out` it overwrote the committed `docs/cost_per_query.md`.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

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
