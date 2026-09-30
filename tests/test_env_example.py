"""`.env.example` lists exactly the environment variables the code reads (#158).

Handoff §10 says each repo gets a `.env.example`; this one had none. The
README's run examples named the three backend variables, but only as `...`
placeholders, and nothing tied the list to the code. The set is derived from
source, so a new backend's variable fails here until it is listed, and a
listed variable nobody reads fails too.

Scope: every tracked `.py` except the hermetic unit tests (`tests/test_*.py`),
which set variables through `monkeypatch` and read none an operator supplies.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Every backend passes `arg or os.environ.get(...)` explicitly, so no variable
# is read only inside a client library.
SDK_IMPLICIT: frozenset[str] = frozenset()

_READ_PATTERNS = (
    re.compile(r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""),
    re.compile(r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""["']([A-Z][A-Z0-9_]*)["']\s+in\s+os\.environ"""),
)


def _in_scope(rel: str) -> bool:
    parts = Path(rel).parts
    return not (len(parts) == 2 and parts[0] == "tests" and parts[1].startswith("test_"))


def names_read(text: str) -> set[str]:
    return {m for pattern in _READ_PATTERNS for m in pattern.findall(text)}


def _names_read_by_repo() -> set[str]:
    files = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    names: set[str] = set()
    for rel in files:
        if _in_scope(rel):
            names |= names_read((REPO_ROOT / rel).read_text(encoding="utf-8"))
    return names | SDK_IMPLICIT


def _names_listed() -> dict[str, str]:
    listed: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            listed[m.group(1)] = m.group(2)
    return listed


def test_the_reader_sees_every_spelling() -> None:
    text = (
        'os.environ.get("A_1")\nos.environ["B"]\nos.getenv( "C" )\n'
        "if 'D' in os.environ: pass\nos.environ.get(name)\n"
    )
    assert names_read(text) == {"A_1", "B", "C", "D"}


def test_the_scan_found_the_backend_reads() -> None:
    # A floor on the population, so a scope bug cannot make the arms below
    # compare two empty sets.
    assert {"PGVECTOR_DSN", "QDRANT_URL", "WEAVIATE_HOST"} <= _names_read_by_repo()


def test_every_variable_read_is_listed() -> None:
    assert ENV_EXAMPLE.is_file(), ".env.example is missing (handoff §10)"
    missing = sorted(_names_read_by_repo() - _names_listed().keys())
    assert not missing, f".env.example does not list {missing}, which the code reads"


def test_every_variable_listed_is_read() -> None:
    extra = sorted(_names_listed().keys() - _names_read_by_repo())
    assert not extra, f".env.example lists {extra}, which nothing reads"


def test_the_weaviate_host_is_a_bare_host() -> None:
    # `WeaviateBackend` passes it as `http_host` and `grpc_host`, with the
    # ports as separate arguments, so a URL here would fail to connect.
    host = _names_listed()["WEAVIATE_HOST"]
    assert ":" not in host, f"WEAVIATE_HOST={host!r} carries a scheme or port"
