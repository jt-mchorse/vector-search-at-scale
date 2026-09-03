"""The temp-name byte budget is measured in the bytes the filesystem sees (#137).

`_cap_base_for_temp` exists so a destination basename near NAME_MAX does not
overflow the limit once the temp affixes are prepended. Its comment says the
budget is in bytes because NAME_MAX is a byte limit — true — and the old
implementation counted `str.encode("utf-8")` under the strict error handler,
which is a different set of bytes from the ones the kernel is handed.

The gap is reachable, not pedantic, because POSIX path bytes and `sys.argv`
both decode through `surrogateescape`: a byte that is not valid UTF-8 becomes a
lone surrogate in `U+DC80..U+DCFF`, which strict UTF-8 encoding refuses. This
repo has two operator-controlled basenames that reach the cap —
`scripts/cost_table.py --out`, and `--run-id`, which `run_benchmark` turns into
`Path(results_dir) / f"{run_id}.json"` — and they failed differently:

* `cost_table` catches `OSError` alone, so it produced the raw traceback at
  exit 1 its own comment says it exists to prevent.
* `_do_run` catches `(ValueError, OSError)` — widened in #101 for the
  run-id-collision case — so `UnicodeEncodeError`, a `ValueError`, was caught
  *by accident*, giving the right exit code with a message naming neither the
  path nor the write.

That second one is why the `run` test below asserts on the **message**. A test
that only checked the exit code would pass against the unfixed code.

These tests are written so the *host* never decides the verdict. The
`_cap_base_for_temp` cases are pure-function and hold everywhere. The seam
cases assert properties true on both a byte-transparent filesystem (ext4, where
the write succeeds) and a UTF-8-validating one (APFS, which returns `EILSEQ`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vector_bench import io_utils as io_mod
from vector_bench.io_utils import _MAX_TEMP_BASE_BYTES, _cap_base_for_temp, atomic_write_text

_REPO_ROOT = Path(__file__).resolve().parents[1]

# A lone low surrogate is what `surrogateescape` produces for the raw byte
# 0xFF. Built from its codepoint rather than written literally so the character
# cannot be mangled by an editor or a copy-paste round trip.
SURROGATE = chr(0xDCFF)


def _fs_len(text: str) -> int:
    """The byte length the kernel sees. Never raises; that is the whole point."""
    return len(os.fsencode(text))


def _decode(stream: bytes) -> str:
    """Decode a child process stream that may carry a raw non-UTF-8 byte.

    The CLI tests below capture bytes rather than passing `text=True`. On a
    filesystem that accepts the name (ext4, i.e. CI) the write succeeds, the
    child prints the path, and `sys.stdout`'s `surrogateescape` handler puts
    the original raw byte on the stream. `text=True` decodes that strictly *in
    the parent* and raises `UnicodeDecodeError` inside `subprocess` — a failure
    of the harness, not of the code under test.
    """
    return stream.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The variant table. Axes: length (fits / overflows) x encoding class
# (pure ASCII / multibyte UTF-8 / surrogate-bearing / mixed).
# ---------------------------------------------------------------------------

NAME_VARIANTS = [
    ("ascii-short", "run-001.json"),
    ("ascii-at-budget", "a" * _MAX_TEMP_BASE_BYTES),
    ("ascii-long", "a" * 250),
    # "é" is 2 bytes in UTF-8, so 150 of them is 300 bytes: over budget in
    # bytes while well under it in characters.
    ("multibyte-short", "rún-001.json"),
    ("multibyte-long", "é" * 150),
    # Each surrogate is exactly one byte under `os.fsencode` — the byte the
    # name actually came from.
    ("surrogate-short", "run" + SURROGATE + ".json"),
    ("surrogate-long", SURROGATE * 250),
    ("mixed-long", "é" * 50 + SURROGATE * 150),
    ("surrogate-only", SURROGATE),
    ("mixed-at-boundary", "a" * (_MAX_TEMP_BASE_BYTES - 1) + SURROGATE),
]


@pytest.mark.parametrize(("label", "base"), NAME_VARIANTS, ids=[v[0] for v in NAME_VARIANTS])
def test_cap_base_for_temp_never_raises_and_stays_within_budget(label: str, base: str) -> None:
    """Every name a `Path` can hold gets a capped answer, not an exception.

    Strict-UTF-8 measurement raised `UnicodeEncodeError` for the surrogate-
    bearing rows before it could answer the length question at all.
    """
    capped = _cap_base_for_temp(base)

    assert _fs_len(capped) <= _MAX_TEMP_BASE_BYTES, f"{label}: over budget"
    assert capped == base[: len(capped)], (
        f"{label}: the capped name must be a character-boundary prefix of the "
        "original — trimming happens by character so no codepoint is split"
    )
    if _fs_len(base) <= _MAX_TEMP_BASE_BYTES:
        assert capped == base, f"{label}: a name within budget must be returned unchanged"
    else:
        # Maximality: one more character would have gone over. Without this the
        # test would also pass for a cap that returns "" for everything.
        assert len(capped) < len(base)
        assert _fs_len(base[: len(capped) + 1]) > _MAX_TEMP_BASE_BYTES, (
            f"{label}: the cap trimmed further than the budget required"
        )


def test_cap_base_for_temp_agrees_with_the_old_measurement_on_encodable_names() -> None:
    """Switching the measurement must not move the budget for names that worked.

    `os.fsencode` and `str.encode("utf-8")` return the same bytes for every
    string that is valid UTF-8, so every previously-passing name is unaffected;
    the change is confined to the names the old call refused outright.
    """
    for _label, base in NAME_VARIANTS:
        try:
            strict = len(base.encode("utf-8"))
        except UnicodeEncodeError:
            continue  # the population the old measurement could not count at all
        assert _fs_len(base) == strict


def test_name_bytes_never_raises_on_a_surrogate() -> None:
    """The measurement helper itself is total over `str`.

    `os.fsencode` uses `surrogateescape` on POSIX and `surrogatepass` on
    Windows, so it round-trips every string a `Path` can carry.
    """
    assert io_mod._name_bytes("run" + SURROGATE + ".json") == len(b"run\xff.json")


# ---------------------------------------------------------------------------
# The seams. The exception *class* is the contract every caller is written
# against, so that is what gets asserted.
# ---------------------------------------------------------------------------


def test_atomic_write_text_unencodable_target_name_fails_as_oserror_if_at_all(
    tmp_path: Path,
) -> None:
    """A destination name the filesystem cannot represent is an OS-level
    problem, and must surface as one.

    Deliberately not asserted as "succeeds" or as "raises": ext4 accepts any
    non-NUL byte in a name and the write goes through, while APFS validates
    UTF-8 and returns `EILSEQ`. Both are correct, and both are `OSError` or
    nothing — which is what a plain `Path.write_text` of the same target does,
    and the class every write seam here is written against.
    """
    target = tmp_path / ("run" + SURROGATE + ".json")

    try:
        atomic_write_text(target, "{}\n")
    except UnicodeEncodeError as e:  # pragma: no cover - the bug this closes
        pytest.fail(
            "atomic_write_text raised UnicodeEncodeError for an unencodable "
            f"destination *name*: {e!r}. The content was pure ASCII."
        )
    except OSError:
        # The filesystem refused the name. Nothing was left behind.
        assert list(tmp_path.iterdir()) == []
        return

    assert target.read_text(encoding="utf-8") == "{}\n"
    assert [p.name for p in tmp_path.iterdir()] == [target.name]


def test_atomic_write_text_long_unencodable_target_name_is_capped_not_refused(
    tmp_path: Path,
) -> None:
    """The long-name and the unencodable-name axes compose.

    This is the row that needs both halves of the fix: the fast-path check has
    to survive the surrogate to discover the name is over budget, and the trim
    loop has to survive it on every iteration.
    """
    target = tmp_path / (SURROGATE * 250)

    try:
        atomic_write_text(target, "x")
    except UnicodeEncodeError as e:  # pragma: no cover - the bug this closes
        pytest.fail(f"cap raised on a long unencodable name: {e!r}")
    except OSError:
        assert list(tmp_path.iterdir()) == []


def test_cost_table_unencodable_out_has_no_traceback(tmp_path: Path) -> None:
    """`scripts/cost_table.py --out` must honour the exit-code contract.

    Its guard catches `OSError` alone, and `UnicodeEncodeError` is a
    `ValueError`, so an unencodable `--out` reproduced verbatim the failure the
    guard's own comment describes: "a raw traceback at exit 1" — after the
    whole cost model had already run.

    Asserted as "no traceback, and if nothing was written the code is 2" rather
    than a fixed code, because ext4 accepts the name and writes the file while
    APFS refuses it; both are correct.
    """
    out = tmp_path / ("cost" + SURROGATE + ".md")

    proc = subprocess.run(
        [sys.executable, "scripts/cost_table.py", "--out", str(out)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        check=False,
    )
    stderr = _decode(proc.stderr)

    assert "Traceback" not in stderr, stderr
    assert "UnicodeEncodeError" not in stderr, stderr
    if not out.exists():
        assert proc.returncode == 2
        assert "could not write" in stderr


def test_run_unencodable_run_id_reports_the_path_not_a_bare_codec_error(tmp_path: Path) -> None:
    """`--run-id` *is* the basename: `Path(results_dir) / f"{run_id}.json"`.

    This one already exited 2 before the fix, and by accident: `_do_run`
    catches `(ValueError, OSError)` because #101 widened it for the
    run-id-collision case, and `UnicodeEncodeError` is a `ValueError`. So an
    exit-code assertion alone passes against the unfixed code and proves
    nothing.

    What separates the two is the message. Before: `error: 'utf-8' codec can't
    encode character '\\udcff' in position 3: surrogates not allowed` — naming
    neither the path, nor the write, nor the `--run-id` the operator typed.
    After: an `OSError` carrying the path. Asserting the path appears in stderr
    is the assertion the bug actually fails.
    """
    results_dir = tmp_path / "results"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "vector_bench.cli",
            "run",
            "--backend",
            "stub",
            "--n",
            "50",
            "--dim",
            "8",
            "--queries",
            "5",
            "--run-id",
            "run" + SURROGATE,
            "--results-dir",
            str(results_dir),
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        check=False,
    )
    stderr = _decode(proc.stderr)

    assert "Traceback" not in stderr, stderr
    written = results_dir / ("run" + SURROGATE + ".json")
    if written.exists():
        assert proc.returncode == 0
        return

    assert proc.returncode == 2
    assert "codec can't encode" not in stderr, (
        "the error must describe the failed write, not the helper's own "
        f"inability to measure the name:\n{stderr}"
    )
    assert "run" in stderr, f"stderr must name the --run-id the write failed on:\n{stderr}"
    assert ".json" in stderr, (
        f"stderr must name the destination file the write failed on:\n{stderr}"
    )
