"""Atomic on-disk write helper.

Five production write sites in this repo emit benchmark artifacts:
- `load.py` writes a per-cell JSON per concurrency level plus a top-
  level `matrix.json`; the LoadMatrix consumer reads the cell files
  back to render the latency-under-load matrix.
- `harness.py` writes a per-backend benchmark result JSON.
- `scripts/hnsw_grid.py` writes the HNSW grid sweep results.
- `scripts/cost_table.py` writes `docs/cost_per_query.md` — the README's
  "Cost per query" section renders from it on GitHub.

`Path.write_text` is not atomic: a signal between the implicit
`open(..., "w")` truncate and `close()` flush leaves the destination
zero-length or partial. Particularly nasty for the per-cell loop in
`load.py`: a half-written cell file or a partial state across multiple
cells breaks the matrix-load reader silently.

Pattern mirrors the portfolio siblings (rag_kit, eval_harness D-015,
emb_shootout D-009, async_pipelines D-011, chunking_lab D-012).
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

# Cap the target basename's contribution to the temp filename. The temp name is
# `.<base>.<random>.tmp`; the affixes add ~13 bytes, so prepending a full
# basename that is itself near NAME_MAX (255 on ext4/APFS) overflows the limit
# and the write fails with `OSError: [Errno 63] File name too long` — even though
# a plain `Path.write_text` of that same target succeeds. Reachable here from a
# long operator `--run-id` (results land at `results/<run_id>.json`). Sibling of
# rag-production-kit#128, mcp-server-cookbook#96, and the 2026-07-14 cross-repo
# sweep (eval_harness#175, prompt_regression#127, async_pipelines#86,
# emb_shootout#103, chunking_lab#128, cost_optimizer#154). The base in the temp
# name is cosmetic (`ls`-ability); uniqueness comes from `NamedTemporaryFile`'s
# random component, so truncating it is safe. Budget is in BYTES (NAME_MAX is a
# byte limit) and we trim on a char boundary so multibyte names are never split
# mid-codepoint.
_MAX_TEMP_BASE_BYTES = 200


def _name_bytes(base: str) -> int:
    """Length of *base* in the bytes the filesystem actually sees.

    `os.fsencode`, not `base.encode("utf-8")` (#137). Both halves of the
    comment above are true and the old implementation still counted the wrong
    bytes: NAME_MAX limits the bytes handed to the kernel, which is
    `os.fsencode` — `sys.getfilesystemencoding()` together with
    `sys.getfilesystemencodeerrors()`, i.e. `surrogateescape` on POSIX.

    That handler is why the distinction bites rather than being pedantry. A
    path byte that is not valid UTF-8 arrives in Python as a lone surrogate in
    `U+DC80..U+DCFF`, and strict `str.encode("utf-8")` refuses to encode it —
    so `_cap_base_for_temp` used to raise `UnicodeEncodeError` on a destination
    the OS can name, *before* reaching the length question. `sys.argv` decodes
    with the same handler, and this repo has two operator-controlled basenames
    that reach here: `scripts/cost_table.py --out`, and `--run-id`, which
    `run_benchmark` turns into `Path(results_dir) / f"{run_id}.json"`.

    They failed differently, and both were wrong. `cost_table`'s guard catches
    `OSError` alone, so it produced the raw traceback at exit 1 that its own
    comment says it exists to prevent. `_do_run` catches `(ValueError,
    OSError)` — widened in #101 for the run-id-collision case — so it returned
    the right code by accident, with a message naming neither the path nor the
    write: `error: 'utf-8' codec can't encode character ...`. Its widening
    comment states the assumption that stopped holding: "The computation is
    pure, so `OSError` here only ever comes from the output write" — true of
    `OSError`, and this failure is not one.

    `os.fsencode` never raises: `surrogateescape` on POSIX, `surrogatepass` on
    Windows, so every `str` a `Path` can hold round-trips. For a name that is
    valid UTF-8 it returns exactly the old number, so the budget is unchanged
    for every name that worked before.
    """
    return len(os.fsencode(base))


def _cap_base_for_temp(base: str) -> str:
    if _name_bytes(base) <= _MAX_TEMP_BASE_BYTES:
        return base
    out = base
    while out and _name_bytes(out) > _MAX_TEMP_BASE_BYTES:
        out = out[:-1]
    return out


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically.

    On success the destination contains exactly *text*. On any failure
    path (signal, disk-full, OOM during flush), the destination is
    either unchanged (overwrite case) or absent (new-file case) —
    never partial. Parent directories are auto-created.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{_cap_base_for_temp(target.name)}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
