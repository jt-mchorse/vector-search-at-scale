"""Architecture-doc lock: catch drift between `docs/architecture.md` and
the actual shipped surface of the repo.

Sister to the architecture-doc lock that landed in
`embedding-model-shootout` PR #20 (same session), and parallel to the
JS variants in `mcp-server-cookbook`, `nextjs-streaming-ai-patterns`,
and `ai-app-integration-tests`. Five invariants pinned:

1. **Path-token reachability.** Every backtick-quoted path token that
   starts with one of the `RESOLVABLE_PREFIXES` resolves on disk.
   Catches typos and renames.

2. **Closed-feature-issue coverage.** Every issue number in
   `KNOWN_SHIPPED_ISSUES` is referenced at least once in the doc, so
   a future fourth study can't ship without the doc updating, and a
   revert toward the pre-#21 "#1 only" state fires the assertion
   with the missing issues named.

3. **Active-decision coverage.** Every non-superseded `D-NNN` in
   `MEMORY/core_decisions_ai.md` whose numeric id is
   `>= MIN_ACTIVE_DECISION_ID` is referenced at least once. The next
   `D-NNN` landing without a doc update fails this test loud.

4. **Banned-phrase absence.** Phrases that characterized the pre-#21
   drift are absent (case-insensitive).

5. **Symbol-reference resolution** (portfolio-ops #55). Every symbol the
   doc *names* — a `<submodule>.<symbol>` attribute ref or a multi-word
   CamelCase public type — resolves to a real attribute of the
   `vector_bench` package (under `src/`), one of its submodules, the
   `backends` subpackage, or the Python `builtins`. Catches the drift
   class #55 catalogued portfolio-wide (a doc naming a nonexistent type
   such as llm-cost-optimizer's `BatchAPIBackend` stays CI-green).
   Propagates the embedding-model-shootout #71 / python-async #70 /
   llm-eval-harness #140 / chunking-strategies-lab #104 /
   prompt-regression-suite #103 precedents.

Hard-pin tests lock `BANNED_PHRASES`, `KNOWN_SHIPPED_ISSUES`,
`RESOLVABLE_PREFIXES`, `MIN_ACTIVE_DECISION_ID`, `SYMBOL_SKIP_EXTENSIONS`,
and `_SUBPACKAGES` to their exact values so a future loose edit can't
silently weaken the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "architecture.md"
DECISIONS = REPO_ROOT / "MEMORY" / "core_decisions_ai.md"

# D-001 is the scope baseline (handoff §2) and isn't tied to a shipped
# code surface; it doesn't need to be cited in architecture.md. Every
# active D-NNN with id >= MIN_ACTIVE_DECISION_ID does.
MIN_ACTIVE_DECISION_ID = 2

# Closed feature issues whose work the architecture doc should
# enumerate. Each represents a shipped surface with a code/artifact
# home in the repo.
#
# Intentionally excluded from the coverage check:
#   - #11  README pending-framing pivot — README-only, not architecture
#          (locked separately by tests/test_readme_snapshot.py)
#   - #12  GIF/video walkthrough — operator-supplied artifact only
#   - #19  Silent-lying concurrency fix — runtime gate documented
#          inline at Layer 2's `vector-bench run` bullet rather than
#          as a top-level architecture layer
KNOWN_SHIPPED_ISSUES = (1, 2, 3, 4, 5, 14, 16, 39)

# Drift shapes specific to issue #21's pre-fix state. Lowercase
# substring match. Pinned in a tuple so a future loose edit of the
# test can't silently drop one.
BANNED_PHRASES = (
    "this pr",
    "· pending",
    "· future",
    "(unfiled)",
    "to-be-filed",
)

# Path-token prefixes that must resolve on disk if quoted in the doc.
# Backtick-quoted tokens only.
RESOLVABLE_PREFIXES = (
    "src/vector_bench/",
    "scripts/",
    "terraform/",
    "results/",
    "docs/",
    "tests/",
    "Makefile",
)


# Symbol-resolution lock (portfolio-ops #55). The package lives under `src/`
# and has a `backends` subpackage; `_SUBPACKAGES` names the subpackages whose
# attributes also count as resolvable doc symbols. Pinned by
# `test_symbol_subpackages_hard_pin_set` so a new subpackage is a deliberate
# widening, not an accidental one.
_PKG = "vector_bench"
_PKG_DIR = REPO_ROOT / "src" / _PKG
_SUBPACKAGES = ("backends",)

# File-suffix tokens that look like a `<name>.<attr>` symbol reference but are
# really filenames (`cli.py`, `prices.py`). Excluded from the dotted-symbol
# resolution check so a filename isn't mistaken for a submodule attribute.
# Hard-pinned by `test_symbol_skip_extensions_hard_pin_set`.
SYMBOL_SKIP_EXTENSIONS = ("py", "sqlite", "json", "md", "txt", "yaml", "yml", "sh", "toml")


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def active_decisions() -> tuple[int, ...]:
    """Parse `MEMORY/core_decisions_ai.md` for non-superseded `D-NNN`
    entries whose numeric id is `>= MIN_ACTIVE_DECISION_ID`.
    """
    text = DECISIONS.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=- id:)", text)
    active: list[int] = []
    for block in blocks:
        id_match = re.search(r"- id:\s*D-(\d+)", block)
        if not id_match:
            continue
        sup_match = re.search(r"superseded_by:\s*(\S+)", block)
        is_active = (sup_match is None) or (sup_match.group(1).strip().lower() == "null")
        if is_active:
            n = int(id_match.group(1))
            if n >= MIN_ACTIVE_DECISION_ID:
                active.append(n)
    return tuple(sorted(active))


def _extract_backtick_paths(text: str) -> set[str]:
    """Collect every backtick-quoted token that starts with one of the
    RESOLVABLE_PREFIXES. Mermaid diagram strings (inside `[...]:`) and
    multi-line code fences are out of scope — backtick spans only.

    Placeholder tokens that contain `<...>` (angle-bracket variable) or
    `{...}` (brace-expansion across multiple files) are not literal
    paths a reader would copy-paste; they document a *shape*. Those
    are excluded from the resolvability check so the doc can still use
    `<run_id>` / `{png,svg}` patterns without false-positives.
    """
    found: set[str] = set()
    for match in re.finditer(r"`([^`\n]+)`", text):
        token = match.group(1).strip()
        for prefix in RESOLVABLE_PREFIXES:
            if token.startswith(prefix):
                # Drop trailing punctuation that wouldn't be part of a
                # copy-pasted path.
                while token and token[-1] in ".,;:":
                    token = token[:-1]
                # Drop a trailing `()` from function-style refs.
                token = re.sub(r"\(\)$", "", token)
                # Skip placeholder shapes (`<run_id>` / `{png,svg}`)
                # — they're explanatory templates, not literal paths.
                if "<" in token or "{" in token:
                    break
                if token:
                    found.add(token)
                break
    return found


def _resolves_on_disk(token: str) -> bool:
    return (REPO_ROOT / token).exists()


def _package_symbol_resolves(name: str) -> bool:
    """True if `name` is an attribute of the `vector_bench` package, any of its
    `*.py` submodules, the `backends` subpackage, or the Python `builtins`.

    Submodule coverage is load-bearing (e.g. `ThreadPoolExecutor`, which the
    doc names for the concurrency layer, resolves as an attribute of
    `vector_bench.load` where it is imported). Builtins are included so the
    doc's `KeyboardInterrupt` reference resolves without a hand-maintained
    allow-list that rots.
    """
    import builtins
    import importlib

    if hasattr(builtins, name):
        return True
    pkg = importlib.import_module(_PKG)
    if hasattr(pkg, name):
        return True
    module_names = [f"{_PKG}.{p.stem}" for p in _PKG_DIR.glob("*.py") if p.stem != "__init__"]
    module_names += [f"{_PKG}.{sub}" for sub in _SUBPACKAGES]
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        if hasattr(module, name):
            return True
    return False


def _extract_symbol_refs(text: str) -> tuple[set[str], set[str]]:
    """Split backtick-quoted tokens into the two symbol-citation styles the doc
    uses, so the resolver only checks genuine symbol claims. Returns
    ``(dotted, camel)``.

    - ``dotted``: ``<submodule>.<symbol>`` where ``<submodule>`` is a real
      ``src/vector_bench/*.py`` module stem and the token is not a filename
      (dropped via ``SYMBOL_SKIP_EXTENSIONS``). Stdlib refs (``os.replace``,
      ``json.dumps``, ``dataclasses.asdict``) are skipped: their prefix is not
      a submodule stem.
    - ``camel``: a *multi-word* CamelCase identifier (an internal
      lowercase->uppercase boundary, e.g. ``BenchmarkResult``, ``LatencyStats``,
      ``ThreadPoolExecutor``). Single-word capitalized tokens (``Backend``,
      ``Workload``) and no-boundary tokens (``Makefile``) are excluded: they
      collide with prose. Bare snake_case is not locked.
    """
    submodules = {p.stem for p in _PKG_DIR.glob("*.py") if p.stem != "__init__"}
    dotted: set[str] = set()
    camel: set[str] = set()
    for match in re.finditer(r"`([^`\n]+)`", text):
        token = match.group(1).strip()
        token = re.sub(r"\(\)$", "", token)
        while token and token[-1] in ".,;:":
            token = token[:-1]
        dotted_match = re.fullmatch(r"([a-z_]+)\.([A-Za-z_][A-Za-z0-9_]*)", token)
        if dotted_match:
            module, attr = dotted_match.group(1), dotted_match.group(2)
            if module in submodules and attr not in SYMBOL_SKIP_EXTENSIONS:
                dotted.add(token)
            continue
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*", token) and re.search(
            r"[a-z][A-Z]", token
        ):
            camel.add(token)
    return dotted, camel


def test_doc_exists() -> None:
    assert DOC.exists(), f"missing {DOC}"


def test_decisions_file_exists() -> None:
    assert DECISIONS.exists(), f"missing {DECISIONS}"


def test_backtick_paths_resolve_on_disk(doc_text: str) -> None:
    tokens = _extract_backtick_paths(doc_text)
    unresolved = sorted(t for t in tokens if not _resolves_on_disk(t))
    assert not unresolved, (
        "docs/architecture.md quotes paths that don't exist on disk:\n"
        + "\n".join(f"  - `{t}`" for t in unresolved)
        + "\n(regenerate the doc to match the current layout, or fix the typo)"
    )


def test_doc_symbol_refs_resolve(doc_text: str) -> None:
    """Every symbol the doc names resolves to a real attribute (portfolio-ops #55).

    ``test_backtick_paths_resolve_on_disk`` validates slash-path tokens only; a
    *symbol* reference — a ``<submodule>.<symbol>`` attribute or a multi-word
    CamelCase public type — was unguarded. That is exactly the drift class #55
    catalogued (a doc naming a nonexistent ``BatchAPIBackend`` /
    ``compute_frontier`` stays CI-green). Inverse-verified by
    ``test_symbol_resolver_flags_injected_drift``.
    """
    import importlib

    dotted, camel = _extract_symbol_refs(doc_text)
    assert dotted or camel, (
        "expected at least one symbol reference (`<submodule>.<symbol>` or a "
        "multi-word CamelCase type) in docs/architecture.md — the resolver "
        "would otherwise be vacuously green"
    )

    unresolved: list[str] = []
    for token in sorted(dotted):
        module_name, _, symbol = token.rpartition(".")
        try:
            module = importlib.import_module(f"{_PKG}.{module_name}")
        except ModuleNotFoundError:
            unresolved.append(f"{token} (module {_PKG}.{module_name} not importable)")
            continue
        if not hasattr(module, symbol):
            unresolved.append(token)
    for token in sorted(camel):
        if not _package_symbol_resolves(token):
            unresolved.append(f"{token} (not a vector_bench symbol or a builtin)")

    assert not unresolved, (
        "docs/architecture.md names symbols that don't exist in the package:\n"
        + "\n".join(f"  - {u}" for u in unresolved)
        + "\n(fix the doc to match the shipped symbol, or update the rename that "
        "orphaned it)"
    )


def test_symbol_resolver_flags_injected_drift() -> None:
    """Inverse safety net: a nonexistent CamelCase type in doc text is flagged.

    Guards against a vacuously-green resolver — if a refactor ever neutered
    extraction or resolution, this fails. Mirrors the #55 drift shape while a
    real symbol in the same string still resolves.
    """
    fake = "The `NonexistentVectorBackend` yields a `LatencyStats`."
    dotted, camel = _extract_symbol_refs(fake)
    assert "NonexistentVectorBackend" in camel
    assert "LatencyStats" in camel
    unresolved = sorted(t for t in camel if not _package_symbol_resolves(t))
    assert unresolved == ["NonexistentVectorBackend"]


def test_symbol_skip_extensions_hard_pin_set() -> None:
    assert SYMBOL_SKIP_EXTENSIONS == (
        "py",
        "sqlite",
        "json",
        "md",
        "txt",
        "yaml",
        "yml",
        "sh",
        "toml",
    )


def test_symbol_subpackages_hard_pin_set() -> None:
    assert _SUBPACKAGES == ("backends",)


def test_every_shipped_issue_referenced(doc_text: str) -> None:
    referenced = {int(m.group(1)) for m in re.finditer(r"#(\d+)\b", doc_text)}
    missing = sorted(set(KNOWN_SHIPPED_ISSUES) - referenced)
    assert not missing, (
        "docs/architecture.md doesn't reference these closed-feature-issues "
        "even once:\n"
        + "\n".join(f"  - #{n}" for n in missing)
        + "\n(every shipped surface should have its origin issue annotated "
        "in the doc; add a `(#NN)` to the relevant component bullet or diagram node)"
    )


def test_every_active_decision_referenced(doc_text: str, active_decisions: tuple[int, ...]) -> None:
    referenced = {int(m.group(1)) for m in re.finditer(r"\bD-0*(\d+)\b", doc_text)}
    missing = sorted(set(active_decisions) - referenced)
    assert not missing, (
        "docs/architecture.md doesn't reference these active "
        "(non-superseded) core decisions even once:\n"
        + "\n".join(f"  - D-{n:03d}" for n in missing)
        + "\n(every shipped layer / posture in MEMORY/core_decisions_ai.md "
        "should be annotated in the doc where the relevant code lives; "
        "add a `D-NNN` reference to the relevant bullet)"
    )


def test_no_banned_phrases(doc_text: str) -> None:
    lowered = doc_text.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    assert not hits, (
        "docs/architecture.md contains pre-#21 drift phrases:\n"
        + "\n".join(f"  - {p!r}" for p in hits)
        + "\n(these phrases described the pre-shipping state; the doc is "
        "now a steady-state reference, not a PR description)"
    )


def test_banned_phrases_hard_pin_set() -> None:
    assert BANNED_PHRASES == (
        "this pr",
        "· pending",
        "· future",
        "(unfiled)",
        "to-be-filed",
    )


def test_known_shipped_issues_hard_pin_set() -> None:
    assert KNOWN_SHIPPED_ISSUES == (1, 2, 3, 4, 5, 14, 16, 39)


def test_resolvable_prefixes_hard_pin_set() -> None:
    assert RESOLVABLE_PREFIXES == (
        "src/vector_bench/",
        "scripts/",
        "terraform/",
        "results/",
        "docs/",
        "tests/",
        "Makefile",
    )


def test_min_active_decision_id_hard_pin() -> None:
    assert MIN_ACTIVE_DECISION_ID == 2


def test_load_runner_flag_matches_cli(doc_text: str) -> None:
    # architecture.md once documented `vector-bench load --clients 1,10,100`, but
    # the CLI registers `--concurrency` (cli.py), so a copy-paste from the doc
    # failed with "unrecognized arguments: --clients" (#89). The path/symbol/
    # decision/banned-phrase locks don't cover flag names — pin the doc's load
    # flag to the real CLI flag so it can't drift again.
    cli_src = (REPO_ROOT / "src" / "vector_bench" / "cli.py").read_text(encoding="utf-8")
    assert '"--concurrency"' in cli_src, "the CLI no longer registers --concurrency"
    assert '"--clients"' not in cli_src, "CLI now has --clients; update this lock"
    assert "--clients" not in doc_text, (
        "docs/architecture.md uses --clients; the real CLI flag is --concurrency"
    )
    assert "--concurrency" in doc_text
