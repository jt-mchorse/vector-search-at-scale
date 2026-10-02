"""File-mode contract for `vector_bench.io_utils.atomic_write_text` (#164).

The helper used to create its temp file through `tempfile.NamedTemporaryFile`,
which always creates 0600 whatever the umask is, and `os.replace` carried that
mode onto the target. A new artifact came out owner-only, and overwriting an
existing 0644 artifact demoted it to 0600. `Path.write_text`, which the helper
replaced, did neither. Part of portfolio-ops#81.

The contract now matches `Path.write_text`: a new file gets ``0o666 & ~umask``
and an overwrite keeps the destination's existing mode.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from vector_bench import io_utils as io_utils_mod
from vector_bench.harness import BenchmarkResult, LatencyStats, Workload, dump_benchmark_json
from vector_bench.io_utils import atomic_write_text

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and umask")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask() -> Iterator[object]:
    """Set the process umask for one test, then restore the original."""
    saved: list[int] = []

    def _set(value: int) -> None:
        old = os.umask(value)
        if not saved:
            saved.append(old)

    yield _set
    if saved:
        os.umask(saved[0])


@pytest.mark.parametrize(("mask", "expected"), [(0o022, 0o644), (0o077, 0o600), (0o002, 0o664)])
def test_new_file_mode_honours_umask(tmp_path: Path, umask, mask: int, expected: int) -> None:
    umask(mask)
    out = tmp_path / "new.json"
    atomic_write_text(out, "{}")
    assert out.read_text(encoding="utf-8") == "{}"
    assert _mode(out) == expected, (
        f"umask {mask:#o}: new file is {_mode(out):#o}, expected {expected:#o} "
        "(0o666 & ~umask, as Path.write_text gives)"
    )


@pytest.mark.parametrize("existing", [0o644, 0o600, 0o640, 0o664])
def test_overwrite_preserves_existing_mode(tmp_path: Path, umask, existing: int) -> None:
    # umask 022 would give 0644 to a fresh file; the existing mode must win
    # whether it is wider, narrower or just different.
    umask(0o022)
    out = tmp_path / "artifact.json"
    out.write_text("old", encoding="utf-8")
    os.chmod(out, existing)
    atomic_write_text(out, "new")
    assert out.read_text(encoding="utf-8") == "new"
    assert _mode(out) == existing, (
        f"overwrite changed {existing:#o} to {_mode(out):#o}; Path.write_text keeps the mode"
    )


def test_umask_is_not_changed_by_the_write(tmp_path: Path, umask) -> None:
    """The helper must let the kernel apply the umask, not read it through
    `os.umask(0)` (process-wide, racy across threads) and leave it changed."""
    umask(0o027)
    atomic_write_text(tmp_path / "a.txt", "x")
    current = os.umask(0o027)
    assert current == 0o027


def test_no_temp_file_left_and_name_shape_kept(
    tmp_path: Path, umask, monkeypatch: pytest.MonkeyPatch
) -> None:
    umask(0o022)
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(io_utils_mod.os, "replace", spy)
    atomic_write_text(tmp_path / "out.json", "{}")
    assert len(seen) == 1
    assert seen[0].startswith(".out.json.")
    assert seen[0].endswith(".tmp")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def test_bad_encoding_leaves_no_temp_file(tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        atomic_write_text(tmp_path / "out.txt", "x", encoding="no-such-codec")
    assert list(tmp_path.iterdir()) == []


def _result() -> BenchmarkResult:
    return BenchmarkResult(
        run_id="mode-test",
        backend="stub",
        workload=Workload(n_vectors=20, dim=4, n_queries=5, top_k=3, seed=1),
        ingest_seconds=0.5,
        ingest_rows_per_sec=40.0,
        query_latency=LatencyStats(p50_ms=1.0, p95_ms=2.0, p99_ms=3.0, max_ms=4.0),
        mean_recall_at_k=0.9,
        started_at="2026-10-01T00:00:00Z",
        git_sha=None,
        cost_per_query_usd=None,
    )


def test_dump_benchmark_json_real_caller_new_and_forced_overwrite(tmp_path: Path, umask) -> None:
    """A real caller: `dump_benchmark_json` writes results/<run_id>.json."""
    umask(0o022)
    out = tmp_path / "results" / "mode-test.json"
    dump_benchmark_json(out, result=_result())
    assert json.loads(out.read_text(encoding="utf-8"))["run_id"] == "mode-test"
    assert _mode(out) == 0o644

    os.chmod(out, 0o640)
    dump_benchmark_json(out, result=_result(), force=True)
    assert _mode(out) == 0o640
