"""Every CLI flag is read, or is a documented compatibility no-op (#156, D-016).

#144 made `cost_table.py`'s row marker a property of the data and left
`--dry/--no-dry` parsed and read nowhere -- deliberately, its own arm asserts the
two produce identical rows -- while `--help`, the module docstring and
`docs/architecture.md` went on saying the flag controlled the marker and
"still selects *which* inputs are used". An operator passing `--no-dry` for
unmarked "real" rows got byte-identical output and no signal.

The lock is structural: a flag that a fix orphans turns this module red, and
the one flag kept for compatibility is read only to say it changed nothing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _dests(path: Path) -> list[tuple[str, int, ast.Call]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        names = [a.value for a in node.args if isinstance(a, ast.Constant)]
        dest = next(
            (
                k.value.value
                for k in node.keywords
                if k.arg == "dest" and isinstance(k.value, ast.Constant)
            ),
            None,
        )
        if dest is None:
            longs = [n for n in names if isinstance(n, str) and n.startswith("--")]
            if longs:
                dest = longs[0][2:].replace("-", "_")
            elif names and isinstance(names[0], str) and not names[0].startswith("-"):
                dest = names[0]
        if dest:
            out.append((dest, node.lineno, node))
    return out


def _sources() -> list[Path]:
    return sorted([*(_ROOT / "scripts").glob("*.py"), *(_ROOT / "src").rglob("*.py")])


def _is_read(dest: str, text: str) -> bool:
    return bool(
        re.search(rf"\b\w+\.{re.escape(dest)}\b", text.replace(f"dest={dest!r}", ""))
        or re.search(rf"getattr\(\s*\w+\s*,\s*[\"']{re.escape(dest)}[\"']", text)
    )


def test_every_flag_is_read_or_declared_a_no_op() -> None:
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(_ROOT))
        for dest, lineno, _ in _dests(path):
            # The flag's own spelling (`"--dry"`) is not a read of it.
            if not _is_read(dest, re.sub(r"[\"']--?[\w-]+[\"']", "", text)):
                offenders.append(f"{rel}:{lineno} --{dest.replace('_', '-')}")
    assert not offenders, f"flags parsed and never read: {offenders}"


def test_no_dry_says_it_changed_nothing_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry` is read now only to tell an operator that `--no-dry` did nothing.
    The rows stay identical (#144's intent) and the help text says so."""
    import scripts.cost_table as cost_table

    dry, wet = tmp_path / "dry.md", tmp_path / "wet.md"
    assert cost_table.main(["--dry", "--out", str(dry)]) == 0
    assert "has no effect" not in capsys.readouterr().err
    assert cost_table.main(["--no-dry", "--out", str(wet)]) == 0
    err = capsys.readouterr().err
    assert "--no-dry has no effect" in err
    assert "--load-results" in err
    assert dry.read_bytes() == wet.read_bytes()
    (call,) = [c for d, _, c in _dests(_ROOT / "scripts/cost_table.py") if d == "dry"]
    help_text = ast.unparse(next(k for k in call.keywords if k.arg == "help").value)
    assert "No effect" in help_text


def test_the_sweep_is_not_vacuous() -> None:
    """A known-read flag is found and judged read; a synthetic unread one is not."""
    found = {(str(p.relative_to(_ROOT)), d) for p in _sources() for d, _, _ in _dests(p)}
    assert ("scripts/cost_table.py", "load_results") in found
    assert len(found) >= 20
    assert _is_read("load_results", "x = args.load_results")
    assert not _is_read("dry", 'p.add_argument(help="--dry does things")')
