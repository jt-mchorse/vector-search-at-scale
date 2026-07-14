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


def _cap_base_for_temp(base: str) -> str:
    if len(base.encode("utf-8")) <= _MAX_TEMP_BASE_BYTES:
        return base
    out = base
    while out and len(out.encode("utf-8")) > _MAX_TEMP_BASE_BYTES:
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
