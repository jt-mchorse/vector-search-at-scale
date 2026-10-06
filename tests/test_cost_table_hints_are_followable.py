"""cost_table's hints work when followed (#176).

* A missing `c001.json` said "Re-run the load harness (`python -m
  vector_bench.load --run-id ...`)"; that module has no `__main__`, exits 0 and
  writes nothing. And a load run without concurrency 1 writes no c001.json.
* An unknown Terraform instance type was reported as "cost model rejected a
  measured throughput (from .../c001.json)" with the library's "Pass a
  PriceTable" advice, which this script cannot take.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import DEFAULT_TFVARS_PATH, main  # noqa: E402
from vector_bench.cli import main as bench_main  # noqa: E402


def test_the_missing_c001_hint_names_a_command_that_produces_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--load-results", f"1m={empty}", "--out", str(tmp_path / "o.md")]) == 2
    hint = capsys.readouterr().err
    assert "python -m vector_bench.load" not in hint
    m = re.search(r"`vector-bench (load [^`]*)`", hint)
    assert m, hint
    assert "concurrency 1" in hint
    # Follow it, filling the placeholders with the stub backend.
    argv = (
        m.group(1)
        .replace("<backend>", "stub")
        .replace("<N>", "2000")
        .replace("<run_id>", "followed")
        .replace("<dir>", str(tmp_path / "res"))
        .split()
    )
    assert bench_main(argv) == 0
    produced = tmp_path / "res" / "followed"
    assert (produced / "c001.json").is_file()
    assert main(["--load-results", f"1m={produced}", "--out", str(tmp_path / "o.md")]) == 0


def test_an_unknown_instance_type_names_the_terraform_file_not_the_throughput(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tf = tmp_path / "main.tf"
    text = DEFAULT_TFVARS_PATH.read_text(encoding="utf-8")
    edited = re.sub(r'instance_type\s*=\s*"m6i\.large"', 'instance_type = "m6i.weird"', text)
    assert edited != text
    tf.write_text(edited, encoding="utf-8")
    assert main(["--tf-main", str(tf), "--out", str(tmp_path / "o.md")]) == 2
    err = capsys.readouterr().err
    assert str(tf) in err
    assert "m6i.weird" in err
    assert "rejected a measured throughput" not in err
    assert "Pass a PriceTable" not in err
    assert "aws_us_east_1_snapshot()" in err
