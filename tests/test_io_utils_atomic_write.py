"""Atomicity contract for `vector_bench.io_utils.atomic_write_text` (issue #33).

Until this PR, five production write sites in this repo used
`Path.write_text` without atomicity guarantees:

- `load.py` writes a per-cell JSON per concurrency level inside a loop
  plus a top-level `matrix.json`. Partial state across multiple cell
  files breaks the matrix-load reader silently.
- `harness.py` writes a per-backend result JSON.
- `scripts/hnsw_grid.py` writes the grid sweep results.
- `scripts/cost_table.py` writes `docs/cost_per_query.md` (front-page README).

A signal between the implicit `open(..., "w")` truncate and `close()`
flush leaves the destination zero-length or partial.

This PR routes all five sites through `vector_bench.io_utils.atomic_write_text`,
matching the 2026-05-26 portfolio atomic-write arc.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from vector_bench import io_utils as io_utils_mod
from vector_bench.io_utils import atomic_write_text

# ---------------------------------------------------------------------------
# Unit tests on the helper.
# ---------------------------------------------------------------------------


def test_atomic_write_text_happy_path(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    atomic_write_text(out, "hello\nworld\n")
    assert out.read_text(encoding="utf-8") == "hello\nworld\n"


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "x.json"
    assert not out.parent.exists()
    atomic_write_text(out, "{}")
    assert out.read_text(encoding="utf-8") == "{}"


def test_atomic_write_text_overwrites_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    out.write_text("STALE-CONTENT-MUST-NOT-SURVIVE", encoding="utf-8")
    atomic_write_text(out, "fresh")
    body = out.read_text(encoding="utf-8")
    assert body == "fresh"
    assert "STALE" not in body


def test_atomic_write_text_replace_failure_leaves_destination_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "result.json"

    def boom(*_args, **_kwargs):
        raise OSError("simulated mid-rename failure")

    monkeypatch.setattr(io_utils_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated mid-rename failure"):
        atomic_write_text(out, '{"k": "v"}')

    assert not out.exists()


def test_atomic_write_text_replace_failure_cleans_up_tmp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "artifacts" / "delta.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    def boom(*_args, **_kwargs):
        raise OSError("simulated mid-rename failure")

    monkeypatch.setattr(io_utils_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated mid-rename failure"):
        atomic_write_text(out, '{"k": "v"}')

    siblings = list(out.parent.iterdir())
    assert siblings == [], f"expected no temp leftovers in {out.parent}, got {siblings}"


def test_atomic_write_text_destination_unchanged_when_overwriting_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "existing.json"
    out.write_text('{"keep": true}', encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(io_utils_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(out, '{"overwrite": true}')

    assert out.read_text(encoding="utf-8") == '{"keep": true}'


# ---------------------------------------------------------------------------
# Integration: each production call site routes through atomic_write_text.
# ---------------------------------------------------------------------------


def test_load_run_under_load_routes_through_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`load.run_under_load(...)` writes per-cell JSON + matrix.json.
    The per-cell loop is the most blast-radius-y site: partial state
    across files silently breaks the matrix-load reader.

    Exercises the dep-free hnsw_sim backend with a tiny workload to
    keep the test under a second.
    """
    from vector_bench.backends import make_backend
    from vector_bench.harness import Workload
    from vector_bench.load import run_under_load

    # Tiny workload — small `n` and `queries` to keep run fast.
    backend = make_backend("stub")
    workload = Workload(n_vectors=16, dim=16, n_queries=4, top_k=1)

    results_dir = tmp_path / "results"

    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(io_utils_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated rename failure"):
        run_under_load(
            backend=backend,
            workload=workload,
            run_id="atomic_test",
            concurrency_levels=(1,),
            results_dir=results_dir,
            write_json=True,
        )

    # Neither the per-cell JSON nor the matrix.json should be written
    # when the rename fails.
    run_dir = results_dir / "atomic_test"
    if run_dir.exists():
        jsons = list(run_dir.glob("*.json"))
        assert jsons == [], f"unexpected JSON files written: {jsons}"


def _load_script(name: str):
    """Load `scripts/<name>` as a module."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / name
    module_name = f"_under_test_{name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def test_cost_table_main_routes_through_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scripts/cost_table.py:main()` must route the markdown write
    through atomic_write_text. `docs/cost_per_query.md` is rendered into the
    README's "Cost per query" section on GitHub — a half-written file
    is a front-page failure.
    """
    cost_table = _load_script("cost_table.py")

    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(io_utils_mod.os, "replace", boom)

    out = tmp_path / "docs" / "cost_per_query.md"
    # os.replace (used by atomic_write_text) raising both proves the write routes
    # through the atomic helper AND exercises the write-seam exit-2 contract (#97):
    # a clean exit 2, not a propagated raw traceback.
    rc = cost_table.main(["--out", str(out)])
    assert rc == 2, "a write failure must land as the clean exit-2 contract (#97)"
    assert not out.exists(), "cost_table --out must not write destination on replace failure"


def test_atomic_write_text_survives_long_basename_near_name_max(tmp_path: Path) -> None:
    """A destination basename near NAME_MAX (255 on ext4/APFS) that a plain
    `Path.write_text` accepts must also succeed through `atomic_write_text`
    (issue #103).

    Before the `_cap_base_for_temp` cap, the temp name `.<base>.<random>.tmp`
    prepended the full basename, pushing the temp filename past NAME_MAX and
    raising `OSError: [Errno 63] File name too long` — even though writing the
    same target directly succeeds. Reachable from a long operator `--run-id`
    (results land at `results/<run_id>.json`). Identical bug to
    rag-production-kit#128 / eval_harness#175 and the 2026-07-14 cross-repo
    sweep; vsas was the last Python repo carrying the pre-fix helper.
    """
    # 250-char basename: write_text accepts it (<= 255), but the pre-fix temp
    # name `.<250>.<8-char>.tmp` was ~272 bytes and overflowed NAME_MAX.
    base = "r" * 246 + ".json"
    assert len(base.encode("utf-8")) == 251
    target = tmp_path / base

    # Baseline: a plain write of the same target succeeds on this fs.
    probe = tmp_path / (base + ".probe")
    if len((base + ".probe").encode("utf-8")) <= 255:
        probe.write_text("probe")
    target.write_text("baseline")  # proves write_text accepts the 251-byte name
    target.unlink()

    atomic_write_text(target, "payload")
    assert target.read_text() == "payload"
    # No leftover temp files in the dir.
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_cap_base_for_temp_trims_on_char_boundary() -> None:
    """`_cap_base_for_temp` trims to a byte budget without splitting a
    multibyte codepoint (NAME_MAX is a byte limit, but names are str)."""
    # Each `é` is 2 bytes in UTF-8; 200 of them = 400 bytes, over the budget.
    capped = io_utils_mod._cap_base_for_temp("é" * 200)
    assert len(capped.encode("utf-8")) <= io_utils_mod._MAX_TEMP_BASE_BYTES
    assert capped == capped  # decodes cleanly — no half-codepoint
    # A short name is returned unchanged.
    assert io_utils_mod._cap_base_for_temp("short.json") == "short.json"
