"""A line break in a results path does not split a row of the cost table (#288).

#281 refused control characters in `run_id` only. `--results-dir` (and so a
`--load-results` directory) is unchecked, and the source cell escaped `|` and
undecodable bytes but not line breaks, so a newline in the directory split a
row of the published `docs/cost_per_query.md`:

    | 1m | pgvector | m6i.large | ... | `/tmp/vsas-nl/team
    runs/r1/c001.json` (simulated) |

A path is real, so it is shown rather than refused: `_shown`, the one
path-to-report-text renderer, writes control characters and line/paragraph
separators as visible escapes.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import DEFAULT_RESULTS_DIR, _shown, main  # noqa: E402

_RUN_ID = "stub-10k"

BREAKS = {
    "LF": ("\n", "\\n"),
    "CR": ("\r", "\\r"),
    "CRLF": ("\r\n", "\\r\\n"),
    "NEL": ("\x85", "\\x85"),
    "LS": (" ", "\\u2028"),
    "PS": (" ", "\\u2029"),
    "TAB": ("\t", "\\t"),
    "ESC": ("\x1b", "\\x1b"),
}


def _table_rows(md: str) -> list[str]:
    return [
        line for line in md.splitlines() if line.startswith("| 1m ") or line.startswith("| 10m ")
    ]


@pytest.mark.parametrize("name", list(BREAKS))
def test_a_control_character_in_the_results_dir_keeps_one_row_per_cell(
    tmp_path: Path, name: str
) -> None:
    raw, shown = BREAKS[name]
    results = tmp_path / f"team{raw}runs"
    shutil.copytree(DEFAULT_RESULTS_DIR / _RUN_ID, results / _RUN_ID)
    out = tmp_path / "cost.md"
    assert main(["--dry", "--results-dir", str(results), "--out", str(out)]) == 0
    md = out.read_text()
    rows = _table_rows(md)
    assert rows, md
    for row in rows:
        assert row.count("|") >= 10, row
        assert f"`{tmp_path}/team{shown}runs/{_RUN_ID}/c001.json`" in row
    if raw != "\n":  # the document's own line breaks are LF
        assert raw not in md
    for line in md.splitlines():
        assert not line.startswith(f"runs/{_RUN_ID}")


@pytest.mark.parametrize("path", ["/tmp/plain dir/x", "/tmp/café/中文", "/tmp/a|b"])
def test_an_ordinary_path_is_shown_unchanged(path: str) -> None:
    assert _shown(Path(path)) == path


def test_an_undecodable_byte_is_still_shown_as_before() -> None:
    assert _shown(Path("/tmp/a\udcffb")) == "/tmp/a\\xffb"
