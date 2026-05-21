"""Smoke test for `scripts/capture_demo.sh` (issue #12).

The capture script is the deterministic driver for the 60-second README
demo. JT records the GIF/video while it runs; CI runs it with
`CAPTURE_PACE_SECONDS=0` so the demo can't bitrot the same way
`tests/test_cost_table.py` already protects the cost-table rendering in
isolation.

Contract this test pins:

1. The script exits 0 on a fresh clone with no AWS account, no Docker.
2. Each of the two surfaces actually runs (the surface header + the
   surface's distinctive output both appear).
3. The vector-bench step prints the JSON shape the real-backend studies
   rely on (`mean_recall_at_k`, `query_latency` keys, `top_k=10`,
   `run_id`).
4. The cost_table step emits the same markdown header signature that
   `docs/cost_per_query.md` carries — `test_cost_table.py` locks the
   format on disk; this test locks it via the capture path too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "capture_demo.sh"


@pytest.fixture(scope="module")
def capture_output() -> str:
    """Run the capture script once and reuse its stdout across assertions.

    `CAPTURE_PACE_SECONDS=0` removes the recording pauses so the test
    isn't gated on sleep durations.
    """
    if not SCRIPT.exists():
        pytest.fail(f"missing {SCRIPT}")
    if shutil.which("bash") is None:
        pytest.skip("bash not available")

    env = dict(os.environ)
    env["CAPTURE_PACE_SECONDS"] = "0"
    # Ensure `python` and `vector-bench` resolve via the venv pytest is
    # running under — capture_demo.sh shells out to both.
    venv_bin = Path(sys.executable).parent
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"capture_demo.sh exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return result.stdout


def test_surface_1_vector_bench_run_emits_expected_json_keys(capture_output: str) -> None:
    assert "1/2 · vector-bench run" in capture_output
    # vector-bench run emits a JSON document on stdout; lock the
    # load-bearing keys that the real-backend studies will consume.
    for key in (
        '"mean_recall_at_k"',
        '"query_latency"',
        '"p50_ms"',
        '"p95_ms"',
        '"p99_ms"',
        '"run_id": "demo-capture"',
        '"top_k": 10',
    ):
        assert key in capture_output, f"missing {key!r} in vector-bench JSON output"


def test_surface_2_cost_table_renders_committed_markdown_header(capture_output: str) -> None:
    """The cost table's header is the load-bearing artifact for the viewer.

    Lock the header row that `docs/cost_per_query.md` ships (and that
    `tests/test_cost_table.py` already locks elsewhere) — if the
    cost_table.py format ever drifts, this test catches it via the
    capture path too, not just via the on-disk file.
    """
    assert "2/2 · cost_table.py --dry" in capture_output
    expected_header = (
        "| Scale | Engine | Instance | EBS | Monthly $ | qps "
        "| $/query | $/M queries | Throughput source |"
    )
    assert expected_header in capture_output, (
        "cost_table markdown header drifted; test_cost_table.py and this test must agree"
    )
    # Every tier × engine row appears in the rendered table — proves
    # the table isn't a truncated header alone.
    for tier in ("1m", "10m", "100m"):
        for engine in ("pgvector", "qdrant", "weaviate"):
            assert f"| {tier} | {engine} |" in capture_output, (
                f"missing {tier!r}/{engine!r} row in rendered cost table"
            )


def test_capture_demo_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} should be executable"
