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
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import TextIO

# Cap the target basename's contribution to the temp filename. The temp name is
# `.<base>.<random>.tmp`; the affixes add ~13 bytes, so prepending a full
# basename that is itself near NAME_MAX (255 on ext4/APFS) overflows the limit
# and the write fails with `OSError: [Errno 63] File name too long` — even though
# a plain `Path.write_text` of that same target succeeds. Reachable here from a
# long operator `--run-id` (results land at `results/<run_id>.json`). Sibling of
# rag-production-kit#128, mcp-server-cookbook#96, and the 2026-07-14 cross-repo
# sweep (eval_harness#175, prompt_regression#127, async_pipelines#86,
# emb_shootout#103, chunking_lab#128, cost_optimizer#154). The base in the temp
# name is cosmetic (`ls`-ability); uniqueness comes from the random component
# `_open_temp` appends (and its `O_EXCL` retry), so truncating it is safe. Budget is in BYTES (NAME_MAX is a
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


# How many random names `_open_temp` tries before giving up. Mirrors
# `tempfile.TMP_MAX`; a collision on 32 random bits is already vanishingly rare.
_TEMP_ATTEMPTS = 10000


def _open_temp(target: Path, encoding: str) -> tuple[TextIO, Path]:
    """Create `.<base>.<random>.tmp` beside *target*; return ``(file, path)``.

    The file is created with mode ``0o666`` so the KERNEL applies the process
    umask, exactly as `Path.write_text` / `open(..., "w")` do (#164). This
    replaced `tempfile.NamedTemporaryFile`, which always creates 0600
    whatever the umask is, so every artifact came out owner-only. The umask is
    deliberately never read through `os.umask(0); os.umask(old)`: that sets a
    process-wide umask of 0 for every other thread until it is restored.

    The temp file stays in the target's directory so `os.replace` is a
    same-filesystem rename. The descriptor goes through `open(..., opener=)`
    so the file object owns it from birth: if the text layer fails (an
    unknown *encoding*), `open` closes it and this function unlinks the file
    it created, so no `.tmp` is left behind.
    """
    prefix = f".{_cap_base_for_temp(target.name)}."
    for _ in range(_TEMP_ATTEMPTS):
        candidate = target.parent / f"{prefix}{secrets.token_hex(4)}.tmp"
        created = False

        def _create_0o666(name: str, flags: int) -> int:
            nonlocal created
            fd = os.open(name, flags | os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            created = True
            return fd

        try:
            # The caller owns the handle (it writes, fsyncs and closes it).
            fh = open(candidate, "x", encoding=encoding, opener=_create_0o666)  # noqa: SIM115
        except FileExistsError:
            if created:
                raise
            continue
        except BaseException:
            if created:
                with contextlib.suppress(FileNotFoundError):
                    candidate.unlink()
            raise
        return fh, candidate
    raise FileExistsError(errno.EEXIST, "no usable temporary file name found", str(target.parent))


def _preserve_target_mode(tmp_path: Path, target: Path) -> None:
    """Give *tmp_path* the permission bits *target* has now, if it exists.

    `os.replace` carries the TEMP file's mode onto the target, so without this
    an overwrite silently re-moded the destination: under the old
    `NamedTemporaryFile` temp, an existing 0644 artifact came back 0600 (#164).
    `Path.write_text` truncates in place and keeps the inode's mode; this is
    the rename-based equivalent. A missing target is not an error: a new file
    keeps the umask-derived mode `_open_temp` gave it.
    """
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        return
    os.chmod(tmp_path, mode)


def _resolve_symlinked_target(target: Path) -> Path:
    """The file a write to *target* lands in: through a symlink (#201).

    `os.replace` renames onto the LINK, not the file it points at, so a
    symlinked destination used to become a regular file while the linked file
    kept its old contents -- where `Path.write_text`, the call this helper
    replaced, writes through the link. `_preserve_target_mode` already followed
    the link (`os.stat`), so the helper copied the linked file's mode onto a
    file that then replaced the link instead. Sibling of
    python-async-llm-pipelines#157.

    Resolving here, before `_open_temp`, puts the temp file beside the
    RESOLVED file, so the rename stays on one filesystem when the link points
    to another one, and the NAME_MAX cap above is applied to the name actually
    being replaced. A dangling link resolves to the path it names, which the
    write then creates, as `Path.write_text` would. A link loop is left as is
    by `realpath` and raises `OSError` (ELOOP) from `_preserve_target_mode`,
    again as `Path.write_text` does. A plain path is returned unchanged, so no
    existing caller sees a different path.
    """
    if not target.is_symlink():
        return target
    return Path(os.path.realpath(target))


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically.

    On success the destination contains exactly *text*. On any failure
    path (signal, disk-full, OOM during flush), the destination is
    either unchanged (overwrite case) or absent (new-file case) —
    never partial. Parent directories are auto-created.

    File mode matches `Path.write_text` (#164): a new file gets
    ``0o666 & ~umask``; an overwrite keeps the existing file's mode.

    A symlinked destination is written THROUGH, as `Path.write_text` does:
    the link stays a link and the file it names gets the new contents (#201).
    """
    target = _resolve_symlinked_target(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        tmp, tmp_path = _open_temp(target, encoding)
        with tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        _preserve_target_mode(tmp_path, target)
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
