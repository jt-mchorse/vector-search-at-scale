"""Both capture stages run this checkout's code, never a `vector-bench` from PATH (#178).

Stage 1 ran the bare console script `vector-bench`, which is bound to the
install that created it, not to the checkout the capture runs from. On the
machine this was found on, PATH's `vector-bench` was another checkout's
editable install on Python 3.11, so a fresh clone's stage 1 recorded code the
clone did not contain (a marker added to the clone's package printed only in
stage 2). The smoke test puts the venv's bin first on PATH, which hides this.
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
MARKER = "STALE-VECTOR-BENCH-FROM-PATH"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_a_vector_bench_first_on_path_is_never_run(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "vector-bench"
    fake.write_text(f"#!/bin/sh\necho {MARKER}\nexit 0\n")
    fake.chmod(0o755)
    env = dict(os.environ)
    env["CAPTURE_PACE_SECONDS"] = "0"
    # The fake first; the interpreter running this test next, so `python`
    # still resolves when the repo has no .venv (CI).
    env["PATH"] = os.pathsep.join([str(fake_bin), str(Path(sys.executable).parent), env["PATH"]])
    r = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    assert MARKER not in r.stdout + r.stderr, "stage 1 ran the vector-bench on PATH"
    stage1 = r.stdout.split("═══ 1/2", 1)[1].split("═══ 2/2", 1)[0]
    assert "recall" in stage1, "stage 1 printed no bench output"
