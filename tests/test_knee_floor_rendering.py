"""The reported recall floor agrees with the floor that was applied (#150).

`recommended_defaults` selects at full float precision —
`c["mean_recall_at_k"] >= recall_floor` — and both branches that report the
selection rendered the floor at `.2f`. The recall beside it renders at `.3f`.

Two *different* widths in one sentence is not the collision this class usually
takes. It is an **inversion**: a collision (`0.950` against `0.95`) looks wrong
and invites a second look, while

    floor=0.9451  knee recall=0.9455  ->  "knee at recall ≥ 0.95 ... recall=0.946"

looks fine and states the reverse of what the tool did. Measured in
`prompt-regression-suite#175` and `ai-app-integration-tests#125` before it was
measured here.

The two branches need *different* fixes and that is the point of the split:

* The knee branch prints a **pair**, so the rule is the sibling repos' rule —
  both sides at the same precision, via `render_comparison`.
* The "no grid cell" branch prints the floor **alone**, under the Pareto table,
  and claims nothing in that table reaches it. That is an absolute claim, and
  no fixed width can make it safe — at `--recall-floor 0.9512` the `.2f` form
  printed a threshold of `0.95` above a table containing a cell at `0.952`. It
  uses `render_exact`.

`--recall-floor` is validated only as "a finite number in `[0, 1]`", so a
three- or four-decimal floor is accepted, and is the natural thing to pass when
tuning against a recall SLO.
"""

from __future__ import annotations

import json as _json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plot_hnsw_frontier  # noqa: E402

from vector_bench.comparison import render_comparison, render_exact  # noqa: E402

_KNEE_RE = re.compile(r"knee at recall ≥ ([^)]+)\):.*?recall=(\S+)", re.S)
_NO_CELL_RE = re.compile(r"No grid cell achieves recall ≥ (\S+?);")


def _grid(recalls: list[float]) -> dict:
    """A valid grid whose cells carry exactly the recalls given, ascending p95.

    Built from the sibling module's fixture shape rather than from scratch, so
    every required field (`run_id`, `ingest_seconds`, `p99_ms`, the `backend` and
    `workload` blocks) is present — the first draft of this helper omitted them
    and the loader's own guard caught it with a `KeyError`.

    The recalls are chosen per-case because these arms need the values to sit at
    chosen distances from a chosen floor, which is the only way to reach the
    margins this issue is about.
    """
    return {
        "backend": "hnsw-sim",
        "workload": {"n_vectors": 64, "dim": 16, "n_queries": 20, "top_k": 5, "seed": 1},
        "cells": [
            {
                "run_id": f"c{i}",
                "M": 16,
                "ef_construction": 200,
                "ef_search": 32 * (i + 1),
                "ingest_seconds": 0.1 * (i + 1),
                "mean_recall_at_k": r,
                "p50_ms": 1.0 + i,
                "p95_ms": 2.0 + i,
                "p99_ms": 3.0 + i,
            }
            for i, r in enumerate(recalls)
        ],
    }


def _run(tmp_path: Path, capsys, recalls: list[float], floor: float | None) -> str:
    path = tmp_path / "grid.json"
    path.write_text(_json.dumps(_grid(recalls)), encoding="utf-8")
    argv = [str(path)]
    if floor is not None:
        argv.append(f"--recall-floor={floor}")
    rc = plot_hnsw_frontier.main(argv)
    assert rc == 0
    return capsys.readouterr().out


# (floor, recalls). Every row has a qualifying cell, so every row prints a knee
# sentence asserting `recall >= floor`. The first is the inversion case: the
# selection is CORRECT and the old rendering stated the reverse.
INVERSION_CASES = [
    pytest.param(0.9451, [0.9455, 0.98], id="floor-4dp-recall-rounds-below-it"),
    pytest.param(0.9512, [0.952, 0.99], id="floor-4dp-just-under-the-cell"),
    pytest.param(0.955, [0.9551, 0.99], id="floor-3dp-near-miss-above"),
    pytest.param(0.95, [0.9505, 0.99], id="round-floor-recall-just-above"),
    pytest.param(0.5, [0.5001, 0.9], id="low-round-floor"),
]


@pytest.mark.parametrize(("floor", "recalls"), INVERSION_CASES)
def test_the_knee_sentence_never_states_the_reverse_of_the_selection(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """Read the sentence back as a reader does and require it to be true.

    Asserting the *ordering*, not that the two strings differ. #177 in
    `prompt-regression-suite` measured tonight what the weaker assertion costs:
    string inequality passes for `0.8500` against `0.85`, which read as the same
    value.
    """
    out = _run(tmp_path, capsys, recalls, floor)
    match = _KNEE_RE.search(out)
    assert match is not None, out
    rendered_floor, rendered_recall = match.groups()
    assert float(rendered_recall) >= float(rendered_floor), (
        f"the knee sentence states the reverse of the selection at floor={floor!r}:\n{out}"
    )


@pytest.mark.parametrize(("floor", "recalls"), INVERSION_CASES)
def test_the_knee_sentence_renders_both_sides_at_the_same_precision(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """The structural half, and the one that rejects the wrong neighbour.

    The defect here was two *different* fixed widths, which is the "widen only
    one side" shape already present in the shipped code. An ordering arm alone
    is not enough: at a round floor the trailing zeros make the mismatched pair
    come out ordered correctly anyway.
    """
    out = _run(tmp_path, capsys, recalls, floor)
    match = _KNEE_RE.search(out)
    assert match is not None, out
    rendered_floor, rendered_recall = match.groups()
    assert "." in rendered_floor, out
    assert "." in rendered_recall, out
    assert len(rendered_floor.split(".")[1]) == len(rendered_recall.split(".")[1]), (
        f"floor {rendered_floor!r} and recall {rendered_recall!r} are at different "
        f"precisions at floor={floor!r}:\n{out}"
    )


# Floors with no qualifying cell, so the "no grid cell" branch runs. The table
# above it lists the recalls, and the claim must be true *about that table*.
NO_CELL_CASES = [
    pytest.param(0.955, [0.9455, 0.952], id="floor-3dp-above-every-cell"),
    pytest.param(0.9512, [0.9455, 0.951], id="floor-4dp-above-every-cell"),
    pytest.param(0.99, [0.95, 0.98], id="round-floor-above-every-cell"),
]


@pytest.mark.parametrize(("floor", "recalls"), NO_CELL_CASES)
def test_the_no_cell_message_is_true_about_the_table_above_it(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """The strongest of the three harms, asserted as a claim rather than a string.

    The old `.2f` form printed "No grid cell achieves recall ≥ 0.95" directly
    under a Pareto table containing a cell at `0.952`. That is not ambiguous; it
    is false — and its suggested remedy ("expand the grid, higher ef_search") is
    the wrong advice for the actual situation, which is that the floor is
    `0.9512`.

    So the arm parses the threshold back out and requires that *no recall in the
    grid* clears it. Nothing here asserts a width: a future change may render it
    differently and still be correct, as long as the sentence stays true.
    """
    out = _run(tmp_path, capsys, recalls, floor)
    match = _NO_CELL_RE.search(out)
    assert match is not None, out
    claimed_floor = float(match.group(1))
    offenders = [r for r in recalls if r >= claimed_floor]
    assert offenders == [], (
        f"the message claims no cell reaches {claimed_floor!r}, but the grid "
        f"printed above it contains {offenders}:\n{out}"
    )


@pytest.mark.parametrize(("floor", "recalls"), NO_CELL_CASES)
def test_the_no_cell_message_names_the_floor_that_was_applied(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """Stronger than the arm above, and it is the one that catches a near miss.

    "No cell reaches the number I printed" can be satisfied by printing a number
    that is too *high* as well as one that is correct. Requiring the printed
    threshold to equal the applied one closes that, and it is what makes the
    message actionable: an operator who reads `0.95` and retunes for `0.95` is
    chasing a floor the tool never used.
    """
    out = _run(tmp_path, capsys, recalls, floor)
    match = _NO_CELL_RE.search(out)
    assert match is not None, out
    assert float(match.group(1)) == floor, out


def test_a_round_default_floor_still_prints_without_a_trailing_expansion(
    tmp_path: Path, capsys
) -> None:
    """GREEN-ish control on the `render_exact` branch: `0.95` stays `0.95`.

    `repr` gives the shortest round-tripping form, so the ordinary operator-facing
    number does not grow digits it never had. This is the arm that rejects
    "render the floor at some wider fixed width", which would satisfy every arm
    above while making the common message uglier and no more true.
    """
    out = _run(tmp_path, capsys, [0.90, 0.92], 0.95)
    assert "No grid cell achieves recall ≥ 0.95;" in out, out


# ----------------------------------------------------------------------
# The helpers' own contracts
# ----------------------------------------------------------------------


def test_render_exact_round_trips() -> None:
    for value in (0.95, 0.9512, 0.955, 0.0, 1.0, 0.1 + 0.2):
        assert float(render_exact(value)) == value


def test_render_comparison_returns_both_sides_at_one_width() -> None:
    left, right = render_comparison(0.9455, 0.9451, places=3)
    assert (left, right) == ("0.946", "0.945")
    assert len(left.split(".")[1]) == len(right.split(".")[1])


def test_render_comparison_widens_only_while_the_two_collide() -> None:
    assert render_comparison(0.998, 0.95, places=3) == ("0.998", "0.950")
    assert render_comparison(0.9500001, 0.95, places=3) == ("0.9500001", "0.9500000")


def test_render_comparison_requires_places() -> None:
    with pytest.raises(TypeError):
        render_comparison(0.9, 0.8)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="places must be non-negative"):
        render_comparison(0.9, 0.8, places=-1)


def test_p95_is_deliberately_not_in_this_class(tmp_path: Path, capsys) -> None:
    """Answering AC5 by assertion rather than by omission.

    `p95={...:.2f}ms` sits in the same sentence as the pair, and is *not* this
    class: nothing compares it against anything, so there is no ordering a
    rounding could invert. It keeps its own width, and this arm pins that the
    fix did not sweep it up — centralising a formatter onto every number in
    reach is how `llm-eval-harness#252` narrowed a published column.
    """
    out = _run(tmp_path, capsys, [0.96, 0.99], 0.95)
    assert re.search(r"p95=\d+\.\d{2}ms", out), out


def test_every_floor_render_in_the_script_goes_through_a_helper() -> None:
    """The population, discovered rather than listed.

    Both branches print `args.recall_floor`, and they had already drifted apart
    once — only one of them names a remedy. A third report of the floor (a
    `--json` summary is the obvious next change) fails here rather than shipping
    at a width that can misstate it.
    """
    source = (Path(__file__).resolve().parents[1] / "scripts" / "plot_hnsw_frontier.py").read_text(
        encoding="utf-8"
    )
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    offenders = [
        line.strip() for line in code.splitlines() if re.search(r"recall_floor:\.\d+f", line)
    ]
    assert offenders == [], (
        f"the recall floor is rendered at a fixed width at {offenders}; route it "
        "through render_comparison (paired) or render_exact (alone) — #150, D-014"
    )
    # Anti-vacuity: the rule must be walking a file that reports the floor at
    # all, or an empty offender list says nothing.
    assert "recall_floor" in code
    assert "render_exact(args.recall_floor)" in code
    assert "render_comparison(" in code
