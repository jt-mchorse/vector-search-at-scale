"""`throughput_qps` is read as the file holds it, not coerced with `float()` (#243).

#241 removed the coercion from `plot_latency._load_matrix`; the cost table read
the same field of the same `c001.json` through `float(qps)`, which accepts a
numeric string -- padding and `_` separators included. Measured on main: a
`c001.json` with `"throughput_qps": " 1_623.5\\n"` produced all nine rows at
`1623.5 qps`, exit 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import load_throughput_qps, main  # noqa: E402

STRINGS = {
    "padded-with-separator": " 1_623.5\n",
    "separator": "1_623",
    "plain-decimal": "1623.5",
    "exponent": "1.6e3",
}


def _c001(tmp_path: Path, qps: object) -> Path:
    run_dir = tmp_path / "load"
    run_dir.mkdir()
    (run_dir / "c001.json").write_text(json.dumps({"throughput_qps": qps}), encoding="utf-8")
    return run_dir


@pytest.mark.parametrize("qps", list(STRINGS.values()), ids=list(STRINGS))
def test_a_numeric_string_is_refused_by_the_loader(tmp_path: Path, qps: str) -> None:
    with pytest.raises(ValueError, match=r"^throughput_qps must be a number; got str "):
        load_throughput_qps(_c001(tmp_path, qps))


@pytest.mark.parametrize("qps", list(STRINGS.values()), ids=list(STRINGS))
def test_main_exits_2_and_writes_nothing(tmp_path: Path, capsys, qps: str) -> None:  # type: ignore[no-untyped-def]
    run_dir = _c001(tmp_path, qps)
    out = tmp_path / "out.md"
    rc = main(["--dry", "--load-results", f"1m={run_dir}", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "throughput_qps must be a number; got str" in err
    assert not out.exists()


@pytest.mark.parametrize("qps", [1623, 1623.5], ids=["int", "float"])
def test_a_number_is_still_read(tmp_path: Path, qps: float) -> None:
    assert load_throughput_qps(_c001(tmp_path, qps)) == float(qps)
