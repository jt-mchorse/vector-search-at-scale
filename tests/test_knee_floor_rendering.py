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

import ast
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
    # Finer than any fixed width a neighbour would plausibly pick. Without this
    # row a `.6f` neighbour was **1 red** — and that one arm was the
    # byte-identity control, not a floor-exactness arm: every floor above
    # happens to survive six places, so the corpus could not tell "exact" from
    # "wide enough for this table". A rule stated as a width has no way to say
    # what it is for, and a corpus that only holds round-ish values cannot
    # falsify one.
    pytest.param(0.9512345678, [0.96, 0.99], id="floor-finer-than-any-fixed-width"),
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


@pytest.mark.parametrize(("floor", "recalls"), INVERSION_CASES)
def test_the_knee_sentence_names_the_floor_that_was_applied(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """The mirror of `test_the_no_cell_message_names_the_floor_that_was_applied`.

    That arm's docstring is the whole argument, and nobody had run it on this
    branch: "an operator who reads `0.95` and retunes for `0.95` is chasing a
    floor the tool never used." Both branches print the same
    `args.recall_floor`, and both make an absolute claim about a floor — "knee at
    recall ≥ X" and "No grid cell achieves recall ≥ X". Only one of them stated
    it exactly (#152, D-015).

    Sharper here than in the `else` branch, because the printed floor is a flag
    value an operator copies back and re-runs with: `--recall-floor 0.951` admits
    cells `0.9512` excluded, so the tool then recommends a **different knee**,
    and the knee is this script's entire output.

    Not D-014's inversion — `render_comparison` still keeps the two sides at one
    precision and in the right order. The ordering is fine and the floor is a
    number nobody set, which no assertion about the two rendering differently
    can see.
    """
    out = _run(tmp_path, capsys, recalls, floor)
    match = _KNEE_RE.search(out)
    assert match is not None, out
    assert float(match.group(1)) == floor, out


@pytest.mark.parametrize(("floor", "recalls"), INVERSION_CASES)
def test_both_branches_render_the_same_floor_to_the_same_value(
    tmp_path: Path, capsys, floor: float, recalls: list[float]
) -> None:
    """The structural arm: two branches of one `if`, one `args.recall_floor`.

    Asserting each branch is "correct" separately passes for a fix that touches
    only one of them. Asserting the two read back as **the same number** does
    not, and that is the relationship the defect lived in. The two need not be
    byte-identical — the `else` branch uses `repr` and this one is padded to the
    pair's shared width — so the comparison is on the value.
    """
    knee_out = _run(tmp_path, capsys, recalls, floor)
    knee_match = _KNEE_RE.search(knee_out)
    assert knee_match is not None, knee_out
    # Same floor, a grid that reaches nothing, so the other branch prints.
    no_cell_out = _run(tmp_path, capsys, [floor - 0.2, floor - 0.1], floor)
    no_cell_match = _NO_CELL_RE.search(no_cell_out)
    assert no_cell_match is not None, no_cell_out
    assert float(knee_match.group(1)) == float(no_cell_match.group(1)) == floor


def test_composing_render_exact_with_a_fixed_width_recall_restores_the_inversion() -> None:
    """The neighbour this repo is most likely to reach for, rejected by measurement.

    `render_exact` already exists here — it did not in the two sibling repos that
    met this class first — so "print the floor with `render_exact` and leave the
    recall at `.3f`" is the obvious local move. It satisfies every
    floor-exactness arm above and puts the two sides back at different
    precisions, which is exactly what #150/D-014 closed:

        floor=0.9512, recall=0.95124
        -> "knee at recall ≥ 0.9512 ... recall=0.951"

    That states the reverse of the selection that was made. A lone claim takes
    `render_exact`; a claim with a second number beside it takes
    `render_comparison` with the operand marked.
    """
    floor, recall = 0.9512, 0.95124
    composed_floor = render_exact(floor)
    composed_recall = f"{recall:.3f}"
    assert float(composed_floor) > float(composed_recall), (
        "this neighbour is supposed to read backwards; if it no longer does, the "
        "case has drifted and the arm proves nothing"
    )
    # What the shipped path does instead: one width, and both read back true.
    rendered_recall, rendered_floor = render_comparison(recall, floor, places=3, exact_other=True)
    assert float(rendered_floor) == floor
    assert float(rendered_recall) == recall
    assert float(rendered_recall) > float(rendered_floor)


def test_marking_keeps_both_sides_at_one_precision() -> None:
    """D-014's invariant survives D-015. Widening one side alone is the old shape."""
    for floor in (0.9512, 0.94512, 0.95, 0.5):
        value, other = render_comparison(0.96, floor, places=3, exact_other=True)
        assert float(other) == floor
        assert len(value.split(".")[1]) == len(other.split(".")[1])


def test_the_flags_are_symmetric_and_off_by_default() -> None:
    """Both flags exist even though only `other` is configured at today's one site.

    `llm-eval-harness`' D-029 shipped `exact_other` alone on the grounds that
    "`value` is the measured side at all six call sites" — a true statement about
    that repo's callers, promoted to a contract, which
    `prompt-regression-suite`#181 falsified the same day with a site comparing two
    configured numbers. A symmetric signature makes no claim a later caller can
    prove false.

    Off by default, so every pre-#152 caller renders exactly as it did. That is
    the same reason `places` is required here (#177): a helper must not silently
    re-render a surface that never asked it to.
    """
    assert render_comparison(0.96, 0.9512, places=3) == ("0.960", "0.951")
    assert render_comparison(0.96, 0.9512, places=3, exact_other=True) == ("0.9600", "0.9512")
    assert render_comparison(0.9512, 0.96, places=3, exact_value=True) == ("0.9512", "0.9600")


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


def test_every_configured_float_is_rendered_so_it_reads_back_as_itself() -> None:
    """The rule the arm above was one notch short of (#152, D-015).

    "Goes through a helper" was satisfied by
    `render_comparison(recall, args.recall_floor, places=3)` — which routes the
    floor through a helper and still published `0.951` for a run floored at
    `0.9512`. The property that matters is the **round trip**, and a paired
    rendering only provides it when the operand is marked.

    Derived from the argument parser rather than from this file's one call site:
    every `type=float` argument is operator input, and every rendering of one
    must either be `render_exact` (a lone claim) or a `render_comparison` call
    that marks that operand (a claim with a second number beside it). A second
    flag added later inherits the rule with no list to update.
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "plot_hnsw_frontier.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))

    float_dests: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name != "add_argument":
            continue
        if not any(
            kw.arg == "type" and getattr(kw.value, "id", "") == "float" for kw in node.keywords
        ):
            continue
        flags = [
            a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        float_dests.add(max(flags, key=len, default="").lstrip("-").replace("-", "_"))
    assert float_dests == {"recall_floor"}, (
        f"the script's `type=float` arguments are now {sorted(float_dests)}; each "
        f"one is operator input and needs checking against this rule (#152)."
    )

    unmarked: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.id if isinstance(node.func, ast.Name) else ""
        if callee not in {"render_comparison", "render_exact"}:
            continue
        if callee == "render_exact":
            continue  # exact by construction
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        for index, flag in ((0, "exact_value"), (1, "exact_other")):
            if len(node.args) <= index:
                continue
            arg = node.args[index]
            if not (isinstance(arg, ast.Attribute) and arg.attr in float_dests):
                continue
            marked = kwargs.get(flag)
            if not (isinstance(marked, ast.Constant) and marked.value is True):
                unmarked.append(f"line {node.lineno}: {ast.unparse(arg)} needs {flag}=True")
    assert not unmarked, (
        f"these `render_comparison` calls compare against a configured float and "
        f"do not mark it, so the number they print is not necessarily the one in "
        f"force: {unmarked} (#152, D-015)."
    )

    # Anti-vacuity: the walk must have found the call it is judging.
    comparison_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "render_comparison"
    ]
    assert len(comparison_calls) == 1, (
        f"found {len(comparison_calls)} render_comparison calls in the script; the "
        f"walk has stopped walking, or a second surface arrived and needs deciding."
    )
