"""A measurement is a tier's only when it was taken at that tier's scale (#291).

`scripts/cost_table.py --load-results TIER=PATH` read `throughput_qps` and
`backend` from `c001.json` and never `workload.n_vectors`, so a run at any
scale was published as the tier's measurement, marked `(real)`, at exit 0.
Measured on origin/main 12e210b with a real local pgvector run at `--n 1000`::

    | 100m | pgvector | r6i.4xlarge | ... | $915.84 | 4497.1 | $0.0000000775 | $0.08 | `.../pg-tiny/c001.json` (real) |

The same directory passed as `1m=`, `10m=` and `100m=` made all three tiers
`(real)`. Following #144 / D-013 (provenance is a property of the measurement,
and a borrowed number is labelled rather than refused), the mismatch goes
into the marker: `(real, measured at n=1,000, not 100m)`.

The fixtures here are hand-built `c001.json` files shaped like the harness's;
no Postgres is needed to exercise the reader.
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

import scripts.cost_table as cost_table  # noqa: E402
from scripts.cost_table import ENGINES, SCALE_TIERS, main  # noqa: E402

_COMMITTED_DOC = _REPO_ROOT / "docs" / "cost_per_query.md"

# Stated here rather than imported, so the expectation is not read from the code
# under test (and the module imports against a tree without the constant).
_TIER_N = {"1m": 1_000_000, "10m": 10_000_000, "100m": 100_000_000}


def _seed(dest: Path, *, backend: str, n_vectors: object = None, workload: object = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"backend": backend, "throughput_qps": 4497.1}
    if workload is not None:
        payload["workload"] = workload
    elif n_vectors is not None:
        payload["workload"] = {"n_vectors": n_vectors, "dim": 384, "n_queries": 50}
    (dest / "c001.json").write_text(json.dumps(payload), encoding="utf-8")
    return dest


def _markers(md: str, tier: str) -> dict[str, str]:
    """{engine: the parenthesised marker at the end of its row} for *tier*."""
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


def test_a_1000_vector_run_is_not_published_as_the_100m_tiers_real_number(tmp_path: Path):
    """The row the issue was filed for."""
    tiny = _seed(tmp_path / "pg-tiny", backend="pgvector", n_vectors=1000)
    markers = _markers(_run(tmp_path, f"100m={tiny}"), "100m")
    assert markers["pgvector"] == "real, measured at n=1,000, not 100m"
    # The borrowed rows carry both halves: whose engine, and what scale.
    assert markers["qdrant"] == "measured on pgvector, not qdrant; measured at n=1,000, not 100m"
    assert (
        markers["weaviate"] == "measured on pgvector, not weaviate; measured at n=1,000, not 100m"
    )


def test_one_directory_passed_for_every_tier_makes_no_tier_real(tmp_path: Path):
    tiny = _seed(tmp_path / "pg-tiny", backend="pgvector", n_vectors=1000)
    md = _run(tmp_path, *(f"{t}={tiny}" for t in SCALE_TIERS))
    for tier in SCALE_TIERS:
        for engine, marker in _markers(md, tier).items():
            assert marker != "real", f"{tier}/{engine} claims a 1,000-vector run as its own"
            assert marker.endswith(f"measured at n=1,000, not {tier}"), (tier, engine, marker)


@pytest.mark.parametrize("tier", SCALE_TIERS)
@pytest.mark.parametrize("run_tier", SCALE_TIERS)
def test_a_run_is_real_only_at_its_own_tier(tmp_path: Path, tier: str, run_tier: str):
    """Every (tier, run scale) pair: the diagonal is `(real)`, nothing else is."""
    run = _seed(tmp_path / "run", backend="qdrant", n_vectors=_TIER_N[run_tier])
    marker = _markers(_run(tmp_path, f"{tier}={run}"), tier)["qdrant"]
    if tier == run_tier:
        assert marker == "real"
    else:
        assert marker == f"real, measured at n={_TIER_N[run_tier]:,}, not {tier}"


@pytest.mark.parametrize("n_vectors", [999_999, 1_000_001])
def test_one_vector_off_is_still_not_the_tier(tmp_path: Path, n_vectors: int):
    """The count is compared exactly; the printed n says how far off it is."""
    run = _seed(tmp_path / "run", backend="pgvector", n_vectors=n_vectors)
    marker = _markers(_run(tmp_path, f"1m={run}"), "1m")["pgvector"]
    assert marker == f"real, measured at n={n_vectors:,}, not 1m"


def test_tier_counts_are_the_tiers_names():
    """`1m` is 10**6, `10m` 10**7, `100m` 10**8, and every tier has one."""
    assert cost_table.TIER_N_VECTORS == _TIER_N
    assert set(_TIER_N) == set(SCALE_TIERS)
    for tier, n in _TIER_N.items():
        assert n == int(tier.removesuffix("m")) * 1_000_000


def test_a_file_that_records_no_scale_says_so(tmp_path: Path):
    """As a file with no `backend` reads `(provenance unrecorded)`."""
    run = _seed(tmp_path / "run", backend="pgvector")
    markers = _markers(_run(tmp_path, f"1m={run}"), "1m")
    assert markers["pgvector"] == "real, scale unrecorded"
    assert markers["qdrant"] == "measured on pgvector, not qdrant; scale unrecorded"


def test_a_workload_without_n_vectors_says_so(tmp_path: Path):
    run = _seed(tmp_path / "run", backend="pgvector", workload={"dim": 64})
    assert _markers(_run(tmp_path, f"1m={run}"), "1m")["pgvector"] == "real, scale unrecorded"


def test_only_the_wrong_scale_engine_is_labelled_among_several(tmp_path: Path):
    """Two engines at one tier (#145): each row reads its own file's scale."""
    pg = _seed(tmp_path / "pg", backend="pgvector", n_vectors=10_000_000)
    qd = _seed(tmp_path / "qd", backend="qdrant", n_vectors=1000)
    markers = _markers(_run(tmp_path, f"10m={pg}", f"10m={qd}"), "10m")
    assert markers["pgvector"] == "real"
    assert markers["qdrant"] == "real, measured at n=1,000, not 10m"
    # Weaviate falls back to the committed stub run (D-013): unchanged.
    assert markers["weaviate"] == "simulated"


def test_a_simulated_row_is_unchanged_whatever_its_scale(tmp_path: Path):
    """Control for the scope: `(simulated)` already claims no engine's measurement."""
    stub = _seed(tmp_path / "stub", backend="stub", n_vectors=1000)
    markers = _markers(_run(tmp_path, f"100m={stub}"), "100m")
    assert set(markers.values()) == {"simulated"}


def test_the_documented_command_still_reproduces_the_committed_doc(tmp_path: Path):
    """`cost_table.py --dry` over the committed stub-10k run (n=10,000) is byte-identical."""
    out = tmp_path / "cost.md"
    assert main(["--dry", "--out", str(out)]) == 0
    assert out.read_bytes() == _COMMITTED_DOC.read_bytes()


@pytest.mark.parametrize(
    "workload",
    [
        {"n_vectors": "1000000"},
        {"n_vectors": True},
        {"n_vectors": 1e6},
        {"n_vectors": [1000000]},
        [1000000],
        "1m",
    ],
    ids=["string", "bool", "float", "list", "workload-list", "workload-string"],
)
def test_a_wrong_type_scale_is_a_clean_exit_2(tmp_path: Path, capsys, workload: object):
    """Refused like a wrong-type `throughput_qps` (#243), naming the file."""
    run = _seed(tmp_path / "run", backend="pgvector", workload=workload)
    out = tmp_path / "out.md"
    assert main(["--dry", "--load-results", f"1m={run}", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert f"{run}/c001.json" in err
    assert "workload" in err
    assert not out.exists()
