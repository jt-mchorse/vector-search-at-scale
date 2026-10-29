"""A measurement is a tier's only when it was taken at the dimension the tiers are sized for (#295).

#291 made `scripts/cost_table.py --load-results TIER=PATH` compare the run's
`workload.n_vectors` to the tier's count, and left `dim` out while the docs
disagreed on it. #292 settled it: the tiers are sized for 384-dim vectors. A run
at the right N and the wrong dimension was still the tier's `(real)` number.
Measured on origin/main 73fa067 with a `c001.json` recording ``backend:
pgvector``, ``n_vectors: 1000000``, ``dim: 768``, passed as ``1m=``::

    | 1m | pgvector | m6i.large | ... | 1623.5 | $0.0000000174 | $0.02 | `.../c001.json` (real) |

at exit 0, and a ``dim`` of ``"768"`` (a string) was accepted silently. The
mismatch now goes into the same marker #291 uses:
`(real, measured at dim=768, not 384)`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_table import ENGINES, main  # noqa: E402

_COMMITTED_DOC = _REPO_ROOT / "docs" / "cost_per_query.md"

# Stated here rather than imported, so the expectation is not read from the code
# under test. `test_infra_doc_sizing_claims` locks the constant to docs/infra.md.
_SIZED_DIM = 384
_N_1M = 1_000_000


def _seed(dest: Path, *, backend: str = "pgvector", workload: object) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload = {"backend": backend, "throughput_qps": 4497.1, "workload": workload}
    (dest / "c001.json").write_text(json.dumps(payload), encoding="utf-8")
    return dest


def _markers(md: str, tier: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in md.splitlines():
        if line.startswith(f"| {tier} |"):
            engine = line.split("|")[2].strip()
            m = re.search(r"\(([^()]*)\) \|$", line)
            assert m, line
            out[engine] = m.group(1)
    assert set(out) == set(ENGINES), md
    return out


def _run(tmp_path: Path, *overrides: str) -> str:
    out = tmp_path / "out.md"
    argv = ["--dry", "--out", str(out)]
    for o in overrides:
        argv += ["--load-results", o]
    assert main(argv) == 0
    return out.read_text(encoding="utf-8")


def test_a_768_dim_run_is_not_published_as_the_1m_tiers_real_number(tmp_path: Path):
    """The row the issue was filed for."""
    run = _seed(tmp_path / "pg768", workload={"n_vectors": _N_1M, "dim": 768})
    markers = _markers(_run(tmp_path, f"1m={run}"), "1m")
    assert markers["pgvector"] == "real, measured at dim=768, not 384"
    assert markers["qdrant"] == "measured on pgvector, not qdrant; measured at dim=768, not 384"
    assert markers["weaviate"] == "measured on pgvector, not weaviate; measured at dim=768, not 384"


@pytest.mark.parametrize("dim", [383, 385, 64, 1536])
def test_any_other_dimension_is_labelled(tmp_path: Path, dim: int):
    run = _seed(tmp_path / "run", workload={"n_vectors": _N_1M, "dim": dim})
    assert _markers(_run(tmp_path, f"1m={run}"), "1m")["pgvector"] == (
        f"real, measured at dim={dim}, not {_SIZED_DIM}"
    )


def test_the_sized_dimension_at_the_tiers_count_is_still_real(tmp_path: Path):
    """Control: the right N at the right dim keeps the bare `(real)`."""
    run = _seed(tmp_path / "run", workload={"n_vectors": _N_1M, "dim": _SIZED_DIM})
    assert _markers(_run(tmp_path, f"1m={run}"), "1m")["pgvector"] == "real"


def test_wrong_count_and_wrong_dim_carry_both_notes(tmp_path: Path):
    run = _seed(tmp_path / "run", workload={"n_vectors": 1000, "dim": 768})
    markers = _markers(_run(tmp_path, f"100m={run}"), "100m")
    assert (
        markers["pgvector"] == "real, measured at n=1,000, not 100m; measured at dim=768, not 384"
    )
    assert markers["qdrant"] == (
        "measured on pgvector, not qdrant; measured at n=1,000, not 100m; "
        "measured at dim=768, not 384"
    )


def test_a_count_without_a_dimension_says_so(tmp_path: Path):
    """As a file with no count reads `scale unrecorded` (#291)."""
    run = _seed(tmp_path / "run", workload={"n_vectors": _N_1M})
    markers = _markers(_run(tmp_path, f"1m={run}"), "1m")
    assert markers["pgvector"] == "real, dim unrecorded"
    assert markers["qdrant"] == "measured on pgvector, not qdrant; dim unrecorded"


def test_a_file_with_no_count_keeps_the_291_marker_whatever_its_dim(tmp_path: Path):
    """Control: `scale unrecorded` already says the row is not the tier's."""
    run = _seed(tmp_path / "run", workload={"dim": 768})
    assert _markers(_run(tmp_path, f"1m={run}"), "1m")["pgvector"] == "real, scale unrecorded"


def test_a_simulated_row_is_unchanged_whatever_its_dim(tmp_path: Path):
    """Control for the scope: `(simulated)` already claims no engine's measurement."""
    stub = _seed(tmp_path / "stub", backend="stub", workload={"n_vectors": _N_1M, "dim": 768})
    assert set(_markers(_run(tmp_path, f"1m={stub}"), "1m").values()) == {"simulated"}


def test_the_documented_command_still_reproduces_the_committed_doc(tmp_path: Path):
    """The committed stub-10k run is dim=64 and backend `stub`: `--dry` is byte-identical."""
    out = tmp_path / "cost.md"
    assert main(["--dry", "--out", str(out)]) == 0
    assert out.read_bytes() == _COMMITTED_DOC.read_bytes()


@pytest.mark.parametrize(
    "dim",
    ["768", True, 384.0, [384], {"value": 384}],
    ids=["string", "bool", "float", "list", "object"],
)
def test_a_wrong_type_dim_is_a_clean_exit_2(tmp_path: Path, capsys, dim: object):
    """Refused like a wrong-type `n_vectors` (#291), naming the file and the field."""
    run = _seed(tmp_path / "run", workload={"n_vectors": _N_1M, "dim": dim})
    out = tmp_path / "out.md"
    assert main(["--dry", "--load-results", f"1m={run}", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert f"{run}/c001.json" in err
    assert "workload.dim" in err
    assert not out.exists()
