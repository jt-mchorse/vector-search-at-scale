"""Lock: every `importorskip` module is declared, and CI installs it (#113).

Three tests guarded on `pytest.importorskip("matplotlib")` while matplotlib
was declared *nowhere* in pyproject.toml — not in `dependencies`, not in any
extra. There was no documented install of this repo under which they could
run. They skipped in CI, they skipped for anyone following the README, and
the `python` job reported green through all of it.

That mattered because plotting is a shipped surface: `scripts/plot_latency.py`
(README:145) and `scripts/plot_hnsw_frontier.py` (README:175, :201), whose
committed output the README calls "real". Two of the three tests exist
specifically to cover the write seam — their own comments say so — the same
seam class as #107, a bug that was real in that file.

Two assertions, in order of sharpness:

1. **Declared.** A guard naming a module no extra provides can never be
   satisfied by any install. That is a dead test, not a conditional one.
2. **Installed by CI.** A guard naming a declared-but-uninstalled module is
   a test that exists and never runs.

Derived from the suite rather than hardcoding `matplotlib`, so the next
`importorskip` cannot repeat this quietly — which is exactly how this one
survived.

Deliberately reads the source rather than shelling out to pytest: the
assertion has to hold in a contributor's minimal venv, where the module is
genuinely absent and the guard is genuinely firing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_TESTS_DIR = _REPO_ROOT / "tests"

_IMPORTORSKIP_RE = re.compile(r"""importorskip\(\s*["']([A-Za-z0-9_.]+)["']""")
_INSTALL_EXTRAS_RE = re.compile(r"pip install[^\n]*?-e\s+'?\.\[([a-z0-9_,\- ]+)\]'?")

# Modules a guard may name that are legitimately never installed — e.g. a
# real backend client the hermetic suite must not require. Empty today;
# adding an entry is a deliberate "this test is expected to stay dormant in
# CI" statement, which is the point.
_EXPECTED_DORMANT: frozenset[str] = frozenset()


def _guarded_modules() -> set[str]:
    found: set[str] = set()
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        found.update(_IMPORTORSKIP_RE.findall(path.read_text(encoding="utf-8")))
    return {m.split(".")[0] for m in found} - _EXPECTED_DORMANT


def _distributions_by_extra() -> dict[str, set[str]]:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    out = {"": {_dist_name(d) for d in project.get("dependencies", [])}}
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        out[extra] = {_dist_name(s) for s in specs}
    return out


def _dist_name(spec: str) -> str:
    # "psycopg[binary]>=3.2" -> "psycopg"; "matplotlib>=3.8" -> "matplotlib"
    return re.split(r"[\[<>=!~ ;]", spec, maxsplit=1)[0].strip().lower()


def _extras_installed_by_ci() -> set[str]:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    installed: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            for match in _INSTALL_EXTRAS_RE.finditer(step.get("run") or ""):
                installed.update(p.strip() for p in match.group(1).split(","))
    return installed


def test_suite_actually_uses_importorskip() -> None:
    # Anti-vacuous: if the guards disappear or are rewritten, the locks below
    # start passing while checking nothing. Fail here instead, loudly.
    assert _guarded_modules(), (
        "No test calls pytest.importorskip(...). Either the guards were "
        "rewritten — update _IMPORTORSKIP_RE — or removed, in which case "
        "delete this lock rather than leave it passing vacuously."
    )


@pytest.mark.parametrize("module", sorted(_guarded_modules()))
def test_guarded_module_is_declared_somewhere(module: str) -> None:
    by_extra = _distributions_by_extra()
    providers = [name for name, dists in by_extra.items() if module in dists]
    assert providers, (
        f"tests guard on `importorskip({module!r})`, but no dependency or "
        f"extra in pyproject.toml provides it (declared: "
        f"{sorted({d for dists in by_extra.values() for d in dists})}). "
        "There is no install under which those tests can run — not CI, not a "
        "reader following the README. Declare it in an extra, or drop the "
        "tests."
    )


@pytest.mark.parametrize("module", sorted(_guarded_modules()))
def test_ci_installs_an_extra_providing_the_guarded_module(module: str) -> None:
    by_extra = _distributions_by_extra()
    providers = {name for name, dists in by_extra.items() if module in dists}
    if "" in providers:
        return  # a base dependency is always installed
    installed = _extras_installed_by_ci()
    assert providers & installed, (
        f"`importorskip({module!r})` is provided by extra(s) "
        f"{sorted(providers)}, but CI installs only {sorted(installed)}. "
        "Those tests will skip on every run and the job will still report "
        "green. Add the extra to the install line, or record the module in "
        "_EXPECTED_DORMANT with a reason if it is meant to stay dormant."
    )
