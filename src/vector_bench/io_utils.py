"""Atomic on-disk write helper.

Five production write sites in this repo emit benchmark artifacts:
- `load.py` writes a per-cell JSON per concurrency level plus a top-
  level `matrix.json`; the LoadMatrix consumer reads the cell files
  back to render the latency-under-load matrix.
- `harness.py` writes a per-backend benchmark result JSON.
- `scripts/hnsw_grid.py` writes the HNSW grid sweep results.
- `scripts/cost_table.py` writes `docs/cost.md` — the README's "Cost
  analysis" section renders from it on GitHub.

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
            prefix=f".{target.name}.",
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
