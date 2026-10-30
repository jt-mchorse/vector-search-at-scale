"""A read-only existing destination is refused, by the writer and its pre-flight (#299).

`atomic_write_text` renames a temp file over the target. A rename swaps the
directory entry, so the target's own write bit was never consulted and a
``chmod 444`` file was silently rewritten, mode still 0444 -- while
`Path.write_text`, the call the helper replaced, raises ``PermissionError``.
The write seams document a read-only path as a clean exit 2. Helper cases run
both writers on the same layout, so the lock is parity with `Path.write_text`.
`check_writable` must refuse the same file, or `run` / `load` / `hnsw_grid`
approve a path and then pay the whole workload before the write refuses it.

Root bypasses permission bits (`Path.write_text` succeeds too), so these skip
as root. GitHub's ubuntu runners run as the non-root ``runner`` user, so CI
exercises them. Port of prompt-regression-suite#318.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from vector_bench.backends.stub import StubBackend
from vector_bench.harness import Workload, run_benchmark
from vector_bench.io_utils import atomic_write_text, check_writable
from vector_bench.load import run_under_load

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from scripts.cost_table import main as cost_table_main  # noqa: E402

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits"),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses file permission bits, so a 0444 file stays writable",
    ),
]


def _write(writer: str, path: Path, text: str) -> None:
    if writer == "atomic":
        atomic_write_text(path, text)
    elif writer == "check_writable":
        check_writable(path)
    else:
        path.write_text(text)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _freeze(path: Path, text: str = "old\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o444)
    return path


@pytest.fixture
def frozen(tmp_path: Path) -> Iterator[Path]:
    """A 0444 file holding ``old``; made writable again for tmp_path cleanup."""
    path = _freeze(tmp_path / "r.json")
    yield path
    path.chmod(0o644)


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_readonly_file_is_refused(frozen: Path, writer: str) -> None:
    with pytest.raises(PermissionError) as excinfo:
        _write(writer, frozen, "new\n")
    assert excinfo.value.errno == errno.EACCES
    assert frozen.read_text() == "old\n"
    assert _mode(frozen) == 0o444


def test_refusal_leaves_no_temp_file(frozen: Path) -> None:
    with pytest.raises(PermissionError):
        atomic_write_text(frozen, "new\n")
    assert sorted(p.name for p in frozen.parent.iterdir()) == ["r.json"]


def test_check_writable_refuses_what_the_writer_refuses(frozen: Path) -> None:
    with pytest.raises(PermissionError) as excinfo:
        check_writable(frozen)
    assert excinfo.value.errno == errno.EACCES
    assert frozen.read_text() == "old\n"
    assert sorted(p.name for p in frozen.parent.iterdir()) == ["r.json"]


@pytest.mark.parametrize("writer", ["atomic", "write_text", "check_writable"])
def test_readonly_file_behind_a_symlink_is_refused(tmp_path: Path, writer: str) -> None:
    # The check runs on the RESOLVED file (#201): a writable link name over a
    # read-only file must not get through.
    real = _freeze(tmp_path / "real.md")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    try:
        with pytest.raises(PermissionError):
            _write(writer, link, "new\n")
        assert real.read_text() == "old\n"
        assert link.is_symlink()
    finally:
        real.chmod(0o644)


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_writable_and_missing_targets_still_write(tmp_path: Path, writer: str) -> None:
    # Controls: the refusal is scoped to an existing unwritable file.
    owner_only = tmp_path / "owner.md"
    owner_only.write_text("old\n")
    owner_only.chmod(0o600)
    check_writable(owner_only)
    _write(writer, owner_only, "new\n")
    assert owner_only.read_text() == "new\n"
    assert _mode(owner_only) == 0o600

    missing = tmp_path / "new.md"
    check_writable(missing)
    _write(writer, missing, "fresh\n")
    assert missing.read_text() == "fresh\n"


def test_cost_table_out_on_a_readonly_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _freeze(tmp_path / "cost.md")
    try:
        rc = cost_table_main(["--dry", "--out", str(out)])
        err = capsys.readouterr().err
        assert rc == 2
        assert "could not write" in err
        assert "Traceback" not in err
        assert out.read_text() == "old\n"
    finally:
        out.chmod(0o644)


CALLS: dict[str, int] = {}


class _Counting(StubBackend):
    def ingest(self, vectors, ids):  # type: ignore[no-untyped-def]
        CALLS["ingest"] = CALLS.get("ingest", 0) + 1
        return super().ingest(vectors, ids)


W = Workload(n_vectors=500, dim=16, n_queries=20, top_k=5, seed=1, concurrency=1)


def test_run_force_onto_a_readonly_result_refuses_before_ingest(tmp_path: Path) -> None:
    CALLS.clear()
    result = _freeze(tmp_path / "r1.json")
    try:
        with pytest.raises(PermissionError):
            run_benchmark(_Counting(), W, run_id="r1", results_dir=tmp_path, force=True)
        assert CALLS == {}
        assert result.read_text() == "old\n"
    finally:
        result.chmod(0o644)


def test_load_force_onto_a_readonly_cell_refuses_before_ingest(tmp_path: Path) -> None:
    # `matrix.json` is writable; only a higher cell is frozen. A pre-flight of
    # `matrix.json` alone passed, the sweep ran, `c001.json` was rewritten and
    # then `c004.json` refused -- new c001 beside the old c004 and matrix.
    CALLS.clear()
    out_dir = tmp_path / "r1"
    (out_dir).mkdir()
    (out_dir / "matrix.json").write_text("{}\n")
    (out_dir / "c001.json").write_text("old c001\n")
    cell = _freeze(out_dir / "c004.json")
    try:
        with pytest.raises(PermissionError):
            run_under_load(
                _Counting(),
                W,
                run_id="r1",
                concurrency_levels=(1, 4),
                results_dir=tmp_path,
                force=True,
            )
        assert CALLS == {}
        assert (out_dir / "c001.json").read_text() == "old c001\n"
        assert cell.read_text() == "old\n"
    finally:
        cell.chmod(0o644)


def test_hnsw_grid_readonly_grid_json_exits_2_before_any_cell_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "grid"
    grid = _freeze(out / "grid.json", json.dumps({"cells": []}) + "\n")
    try:
        rc = hnsw_grid.main(
            [
                *["--n-vectors", "60", "--n-queries", "5", "--dim", "16", "--top-k", "5"],
                *["--M", "8", "--ef-construction", "50", "--ef-search", "16"],
                *["--out-dir", str(out)],
            ]
        )
        assert rc == 2
        assert "Traceback" not in capsys.readouterr().err
        assert sorted(p.name for p in out.iterdir()) == ["grid.json"]
        assert grid.read_text() == json.dumps({"cells": []}) + "\n"
    finally:
        grid.chmod(0o644)
