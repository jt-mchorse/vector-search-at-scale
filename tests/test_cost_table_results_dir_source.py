"""The source cell names the file a row was read from, under any `--results-dir` (#174).

`_display` asked whether the directory was `results_dir / run_id` -- true for
every default-run directory whatever `--results-dir` was -- and printed a
hard-coded `results/load/` prefix. Measured on main (9c3914c): a copy of the
committed run with its throughput set to 50.0 qps, read via `--results-dir`,
was published as::

    | 1m | pgvector | ... | 50.0 | ... | `results/load/stub-10k/c001.json` (simulated) |

crediting the committed file (1623.5 qps) for a number it does not contain.
The method bullet hard-coded the same prefix.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import DEFAULT_RESULTS_DIR, main  # noqa: E402

_RUN_ID = "stub-10k"


def _source_cells(md: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\| `([^`]*c001\.json)` \(", md)}


def _bullet(md: str) -> str:
    (line,) = [line for line in md.splitlines() if line.startswith("- **Throughput**")]
    return line


def test_a_results_dir_copy_is_credited_to_the_copy(tmp_path: Path) -> None:
    copy = tmp_path / "load"
    shutil.copytree(DEFAULT_RESULTS_DIR / _RUN_ID, copy / _RUN_ID)
    record = copy / _RUN_ID / "c001.json"
    payload = json.loads(record.read_text())
    assert payload["throughput_qps"] != 50.0
    payload["throughput_qps"] = 50.0
    record.write_text(json.dumps(payload))

    out = tmp_path / "cost.md"
    assert main(["--dry", "--results-dir", str(copy), "--out", str(out)]) == 0
    md = out.read_text()
    assert "| 50.0 |" in md  # the copy's number really is what was published
    assert _source_cells(md) == {f"{copy}/{_RUN_ID}/c001.json"}
    assert f"`{copy}/<run_id>/c001.json`" in _bullet(md)
    assert "results/load" not in _bullet(md)


@pytest.mark.parametrize(
    "results_dir",
    # `results/load` prints the same text resolved or not; a spelling with `..`
    # is what tells a resolved comparison from a textual one.
    [None, str(DEFAULT_RESULTS_DIR), "results/load", "results/../results/load"],
    ids=["flag-absent", "absolute-default", "relative-default", "dotdot-default"],
)
def test_the_committed_directory_still_renders_repo_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, results_dir: str | None
) -> None:
    monkeypatch.chdir(_REPO_ROOT)
    out = tmp_path / "cost.md"
    argv = ["--dry", "--out", str(out)]
    if results_dir is not None:
        argv += ["--results-dir", results_dir]
    assert main(argv) == 0
    md = out.read_text()
    assert _source_cells(md) == {f"results/load/{_RUN_ID}/c001.json"}
    assert "`results/load/<run_id>/c001.json`" in _bullet(md)
