"""Public-surface tests for ``vector_bench/__init__.py``.

``vector_bench`` re-exports 22 names from five submodules
(``backends.stub``, ``cost``, ``harness``, ``prices``, ``types``) and
already publishes ``__version__``. Every other test in this suite
imports submodules directly (``from vector_bench.harness import
run_benchmark``), so silent renames or accidental ``__all__`` drops in
``__init__.py`` don't fail any test — but they break the package
docstring's quoted "Library use" promise and the ``vector-bench``
console-script entry-point.

These four standalone + 2 parametrized tests lock the surface across
six orthogonal axes:

1. ``__version__`` is set to a semver-ish string.
2. Every name in ``__all__`` is bound on the package and non-None.
3. ``__all__`` agrees with the actual top-level **absolute** ``from
   vector_bench.X import …`` names (filter
   ``module.startswith("vector_bench.")`` — same as the original
   eval-harness pattern; this is the first repo in the May 2026
   pattern series that uses absolute imports rather than relative).
4. Package-docstring's quoted "Library use" imports resolve
   (``Backend``, ``Workload``, ``BenchmarkResult``, ``QueryHit``,
   ``run_benchmark``, ``generate_corpus``, ``ground_truth_topk``,
   ``StubBackend`` — 8 names quoted at lines 5-9 of ``__init__.py``).
5. pyproject's quoted dotted path ``vector_bench.cli.main`` resolves
   to a callable (the ``vector-bench`` console-script entry-point).
6. One anchor per re-exported submodule (5 anchors). The ``backends``
   subpackage is anchored by its ``stub`` submodule since that's what
   ``__init__.py`` re-imports from.

Ninth strike of the portfolio-wide public-surface hygiene pattern.
Orthogonal to ``tests/test_readme_snapshot.py`` (README structural
claims) and ``tests/test_hnsw_recommended_defaults_snapshot.py``
(numeric defaults); this test locks the Python public surface.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import vector_bench

_INIT_PATH = Path(vector_bench.__file__)
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")

# Package docstring (lines 5-9 in __init__.py) quotes these eight names
# as importable directly from the top level.
PACKAGE_DOCSTRING_NAMES = (
    "Backend",
    "Workload",
    "BenchmarkResult",
    "QueryHit",
    "run_benchmark",
    "generate_corpus",
    "ground_truth_topk",
    "StubBackend",
)

# pyproject.toml declares:
#   [project.scripts]
#   vector-bench = "vector_bench.cli:main"
README_DOTTED_PATHS = (("vector_bench.cli", "main"),)

# Anchor names that prove each re-exported submodule survived.
# ``backends`` is a subpackage; we anchor it via the specific submodule
# that ``__init__.py`` actually re-imports from (``backends.stub``).
SUBMODULE_ANCHORS = {
    "backends.stub": "StubBackend",
    "cost": "cost_per_query",
    "harness": "run_benchmark",
    "prices": "aws_us_east_1_snapshot",
    "types": "Backend",
}


def _parse_init_absolute_imports() -> set[str]:
    """Return the set of names imported into ``__init__.py`` via
    top-level absolute ``from vector_bench.X import (...)`` blocks."""
    tree = ast.parse(_INIT_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("vector_bench.")
        ):
            for alias in node.names:
                # An aliased import adds the alias to the public surface,
                # not the original name.
                names.add(alias.asname or alias.name)
    return names


def test_version_is_set_to_semver_ish_string() -> None:
    """``__version__`` is published; downstream importers and PyPI
    builds rely on it."""
    assert hasattr(vector_bench, "__version__"), (
        "vector_bench.__version__ is missing — packaging tools and "
        "downstream `vector_bench.__version__` lookups will break."
    )
    version = vector_bench.__version__
    assert isinstance(version, str), (
        f"vector_bench.__version__ should be a string, got {type(version).__name__}: {version!r}."
    )
    assert version, "vector_bench.__version__ is an empty string."
    assert _SEMVER_PATTERN.match(version), (
        f"vector_bench.__version__ = {version!r} doesn't look like "
        f"semver (expected MAJOR.MINOR.PATCH[-prerelease][+build])."
    )


def test_all_names_are_bound_and_non_none() -> None:
    """Every name in ``__all__`` must be importable and non-None."""
    missing: list[str] = []
    none_valued: list[str] = []
    for name in vector_bench.__all__:
        if not hasattr(vector_bench, name):
            missing.append(name)
            continue
        if getattr(vector_bench, name) is None:
            none_valued.append(name)
    assert not missing, (
        f"vector_bench.__all__ advertises names that are not bound on "
        f"the package: {missing}. The most likely cause is a re-import "
        f"line was deleted from __init__.py but __all__ wasn't updated."
    )
    assert not none_valued, (
        f"vector_bench.__all__ entries bound to None: {none_valued}. "
        f"A re-import probably resolved to a missing submodule attribute."
    )


def test_all_matches_actual_top_level_imports() -> None:
    """``__all__`` should equal the set of top-level **absolute** re-exports.

    Catches drift in either direction: an export was added to the
    imports block but not ``__all__``, or vice versa.
    """
    advertised = set(vector_bench.__all__)
    imported = _parse_init_absolute_imports()
    only_imported = imported - advertised
    only_advertised = advertised - imported
    assert not only_imported, (
        f"Names imported into vector_bench/__init__.py but missing from "
        f"__all__: {sorted(only_imported)}. Add them to __all__ or stop "
        f"importing them at the top level."
    )
    assert not only_advertised, (
        f"Names in vector_bench.__all__ but not imported at the top of "
        f"__init__.py: {sorted(only_advertised)}. Add the import or "
        f"remove the __all__ entry."
    )


def test_package_docstring_imports_resolve() -> None:
    """Package docstring's quoted "Library use" must keep working.

    The package docstring literally quotes (lines 5-9 in __init__.py)::

        from vector_bench import (
            Backend, Workload, BenchmarkResult, QueryHit,
            run_benchmark, generate_corpus, ground_truth_topk,
            StubBackend,
        )

    If any of those eight names disappears from the top-level surface,
    every reader who copy-pastes the snippet hits an ImportError.
    """
    missing = [n for n in PACKAGE_DOCSTRING_NAMES if not hasattr(vector_bench, n)]
    assert not missing, (
        f"vector_bench is missing names quoted in its own docstring's "
        f"`Library use` block: {missing}. Either restore the exports "
        f"or update the docstring at the top of __init__.py."
    )


@pytest.mark.parametrize(
    ("module_path", "attr"),
    README_DOTTED_PATHS,
    ids=[f"{m}.{a}" for m, a in README_DOTTED_PATHS],
)
def test_console_script_dotted_path_resolves(module_path: str, attr: str) -> None:
    """pyproject's ``vector-bench`` console-script entry-point must
    keep resolving to a callable.

    pyproject.toml declares::

        [project.scripts]
        vector-bench = "vector_bench.cli:main"

    If ``cli.py`` is renamed or ``main`` is moved, ``vector-bench``
    breaks at first invocation. Locking the lookup here keeps the
    packaging contract honest.
    """
    module = importlib.import_module(module_path)
    assert hasattr(module, attr), (
        f"`{module_path}.{attr}` no longer resolves. pyproject's "
        f"[project.scripts] table points to `{module_path}:{attr}` — "
        f"either restore the export or update pyproject.toml."
    )
    obj = getattr(module, attr)
    assert callable(obj), (
        f"`{module_path}.{attr}` is no longer callable (got "
        f"{type(obj).__name__}). The console-script entry-point calls "
        f"it; the lookup must return a callable."
    )


@pytest.mark.parametrize(
    ("submodule", "anchor"),
    sorted(SUBMODULE_ANCHORS.items()),
    ids=sorted(SUBMODULE_ANCHORS.keys()),
)
def test_submodule_anchor_re_exported(submodule: str, anchor: str) -> None:
    """One anchor per re-exported submodule survives at the top level."""
    assert hasattr(vector_bench, anchor), (
        f"`{anchor}` from `vector_bench.{submodule}` is no longer "
        f"re-exported at the top level. Did `{submodule}` move or get "
        f"renamed? Update `vector_bench/__init__.py` to re-export from "
        f"the new path."
    )
