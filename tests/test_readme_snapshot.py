"""README snapshot: lock the surface bullet list and demo invariants.

Sister to the portfolio-wide drift-lock pattern landed 2026-05-18+.
Issues #1..#5 are all closed; the README must not carry "pending until
#N ships" or "this PR, issue #1" framing.

Tests:
- Every shipped-issue ref (#1..#5) appears in the body.
- No "pending until ... ships" framing.
- No "(this PR, issue #N)" framing for closed issues.
- Demo section names a follow-up, references a runnable command, and is
  not the bare pre-2026-05-19 single line.
- Every relative file path the README references resolves on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_what_this_is_section_cites_all_five_shipped_issues() -> None:
    body = _readme()
    expected = ["#1", "#2", "#3", "#4", "#5"]
    missing = [ref for ref in expected if ref not in body]
    assert not missing, (
        f"README is missing references to shipped issues: {missing}. "
        "Every closed issue with shipped surface must be cited."
    )


def test_readme_does_not_carry_pending_until_ships_framing() -> None:
    body = _readme()
    matches = re.findall(r"pending until[^.]*ships", body, re.IGNORECASE)
    assert not matches, (
        f"README contains stale 'pending until ... ships' framing: {matches!r}. "
        "Every gating issue named in such a clause is closed; rewrite past-tense or "
        "name a follow-up issue for the captured artifact."
    )


def test_readme_does_not_carry_this_pr_issue_n_framing() -> None:
    body = _readme()
    # "this PR, issue #N" or "(this PR, issue #N)" was correct only in
    # the original landing PR for #1; everything shipped now.
    matches = re.findall(r"this PR,?\s*issue\s*#\d+", body)
    assert not matches, (
        f"README contains stale 'this PR, issue #N' framing: {matches!r}. "
        "Rewrite past-tense — every closed issue should just be cited as `(#N)`."
    )


def test_demo_section_names_followup_and_describes_today() -> None:
    body = _readme()
    start = body.index("## Demo")
    end = body.index("##", start + 1)
    demo = body[start:end]
    assert re.search(r"#\d+", demo), (
        "Demo section must name at least one follow-up issue (the captured-asset owner)."
    )
    assert "vector-bench" in demo or "scripts/" in demo, (
        "Demo section must reference at least one runnable command from this repo "
        "(either `vector-bench` or `scripts/...`)."
    )
    stripped = [line for line in demo.strip().splitlines() if line and not line.startswith("#")]
    assert len(stripped) > 2, (
        "Demo section is too thin — must describe today's runnable surface, not a single "
        "'pending until X ships' line."
    )


def test_referenced_files_exist() -> None:
    body = _readme()
    pattern = re.compile(r"\(([^)\s]+\.(?:md|jsonl|py|html|json|yml|yaml|png|svg|tf))\)")
    refs = {r for r in pattern.findall(body) if not r.startswith(("http://", "https://"))}
    missing = sorted(r for r in refs if not (REPO_ROOT / r).exists())
    assert not missing, (
        f"README references files that don't exist: {missing}. "
        "Either fix the link or commit the file."
    )
